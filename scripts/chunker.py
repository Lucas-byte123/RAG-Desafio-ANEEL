"""
chunker.py — transforma extractions em chunks Parent-Child para RAG.

Lê tabela EXTRACTIONS (JSON estruturado), produz tabela CHUNKS com:
  - CHILD chunks (nível 1, ~400 tokens): unidade de busca vetorial
  - PARENT chunks (nível 0, ~1200 tokens): contexto ao LLM

Princípios:
  - Artigo é unidade atômica (com seus §, incisos, alíneas juntos)
  - Tabela é chunk único com cabeçalho replicado em splits
  - Breadcrumb ancestral prefixado no text_embed ('REN 687/2015 > CAP II > Art. 5º')
  - text_raw (sem breadcrumb) vai pro LLM; text_embed vai pro embedding

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/chunker.py
    python scripts/chunker.py --limit 20 --force
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"

DSN = "aneelrag_medium"
USER = "ADMIN"

# Tamanhos alvo (aproximação PT: 1 token ~= 4 chars)
CHILD_MAX_CHARS = 1600       # ~400 tokens. Cohere v3 suporta 512 tokens.
PARENT_MAX_CHARS = 4800      # ~1200 tokens.
CHILD_OVERLAP_CHARS = 150    # apenas em splits de texto livre grande
EMBED_TEXT_MAX_BYTES = 3900  # VARCHAR2(4000 BYTE) — Oracle conta bytes, não chars
MIN_CHUNK_CHARS = 80         # chunks menores que isso são ruído (assinaturas, "Atenciosamente,")
CELL_MAX_CHARS = 400         # cada célula de tabela truncada
HARD_MAX_CHILD_CHARS = 2000  # safety final: chunk > isso é descartado

# Regex pra extrair numeração do título do registro
RE_NUM_ITEM = re.compile(
    r"\b(REN|RES|REA|REH|DSP|NT|NTF|DSL|DEL|OFC|PRT|CIR|AUP|DEC|ATA|RTC|VOT)\b[^\d\n]{0,40}?(\d[\d\./\-]*)",
    re.IGNORECASE,
)


def env_or_die(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"ERRO: {name} não definida")
    return v


def make_db_conn(pwd, wallet_pwd):
    return oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )


# -------- BREADCRUMB --------

def extract_numeracao(registro_titulo: str | None) -> str:
    """Extrai 'REN 687/2015' ou 'DSP 956/2021' do título do registro."""
    if not registro_titulo:
        return ""
    m = RE_NUM_ITEM.search(registro_titulo)
    if m:
        tipo_sigla = m.group(1).upper()
        num = m.group(2)
        return f"{tipo_sigla} {num}"
    return ""


def build_breadcrumb(pdf_meta: dict, ancestors: dict) -> str:
    """Monta 'REN 687/2015 > CAP II > Seção I > Art. 5º > §2°'."""
    parts = []
    numeracao = pdf_meta.get("numeracao_doc", "")
    ano = pdf_meta.get("ano", "")
    tipo = pdf_meta.get("tipo_canonico", "")

    if numeracao:
        if ano and str(ano) not in numeracao:
            parts.append(f"{numeracao}/{ano}")
        else:
            parts.append(numeracao)
    elif tipo:
        parts.append(f"{tipo} {ano}" if ano else tipo)

    for k in ("anexo", "capitulo", "secao", "artigo", "paragrafo"):
        v = ancestors.get(k)
        if v:
            parts.append(v)

    return " > ".join(parts)


# -------- ATOMS --------

def normalize_cell(text) -> str:
    """Normaliza e trunca célula de tabela pra evitar células gigantes."""
    if text is None:
        return ""
    s = str(text).strip().replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s)
    if len(s) > CELL_MAX_CHARS:
        s = s[:CELL_MAX_CHARS] + "…"
    return s


def table_to_markdown(table: dict) -> str:
    """Serializa tabela em markdown. Retorna string."""
    rows = table.get("rows") or []
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    norm_rows = [[normalize_cell(c) for c in r] + [""] * (n_cols - len(r)) for r in rows]
    header = norm_rows[0]
    body = norm_rows[1:] if len(norm_rows) > 1 else []
    lines = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * n_cols) + " |")
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def split_table_by_rows(table: dict, max_chars: int) -> list[str]:
    """Divide tabela em fatias ≤ max_chars, replicando o cabeçalho em cada."""
    rows = table.get("rows") or []
    if not rows:
        return []
    header = [normalize_cell(c) for c in rows[0]]
    body_rows = rows[1:]
    if not body_rows:
        return [table_to_markdown(table)]

    n_cols = len(header)
    header_line = "| " + " | ".join(header) + " |"
    sep_line = "| " + " | ".join(["---"] * n_cols) + " |"
    prefix = f"{header_line}\n{sep_line}\n"
    prefix_chars = len(prefix)

    out = []
    current_body = []
    current_chars = prefix_chars
    for row in body_rows:
        row_cells = [normalize_cell(c) for c in row] + [""] * (n_cols - len(row))
        row_line = "| " + " | ".join(row_cells) + " |"
        row_chars = len(row_line) + 1
        # Safety: se mesmo APÓS truncate de células 1 row já excede, força chunk só dela
        if row_chars > max_chars:
            if current_body:
                out.append(prefix + "\n".join(current_body))
                current_body = []
                current_chars = prefix_chars
            out.append(prefix + row_line[:max_chars - prefix_chars])
            continue
        if current_chars + row_chars > max_chars and current_body:
            out.append(prefix + "\n".join(current_body))
            current_body = [row_line]
            current_chars = prefix_chars + row_chars
        else:
            current_body.append(row_line)
            current_chars += row_chars
    if current_body:
        out.append(prefix + "\n".join(current_body))
    return out


def make_atom_from_blocks(blocks: list[dict], kind: str) -> dict:
    """Junta lista de blocks contíguos num único atom textual."""
    if not blocks:
        return {}
    first = blocks[0]
    texts = []
    for b in blocks:
        t = b.get("type", "")
        meta = b.get("metadata", {}) or {}
        text = b.get("text", "").strip()
        if not text:
            continue
        if t == "artigo":
            num = meta.get("numero", "")
            texts.append(f"Art. {num}º {text}" if num and not text.lower().startswith("art") else text)
        elif t == "paragrafo":
            num = meta.get("numero", "")
            texts.append(f"{num} {text}" if num else text)
        elif t == "inciso":
            num = meta.get("numero", "")
            texts.append(f"{num} - {text}" if num else text)
        elif t == "alinea":
            mark = meta.get("marcador", "")
            texts.append(f"{mark}) {text}" if mark else text)
        elif t == "heading_capitulo":
            texts.append(f"\n{text}\n")
        elif t == "heading_secao":
            texts.append(f"\n{text}\n")
        elif t == "heading_anexo":
            texts.append(f"\n{text}\n")
        elif t == "heading_livre":
            texts.append(f"\n{text}\n")
        else:
            texts.append(text)
    joined = "\n".join(t.strip() for t in texts if t.strip())
    return {
        "kind": kind,
        "text": joined,
        "ancestors": first.get("ancestors", {}),
        "page_start": first.get("page", 1),
        "page_end": blocks[-1].get("page", first.get("page", 1)),
        "num_chars": len(joined),
    }


def extract_atoms(blocks: list[dict], tables: list[dict]) -> list[dict]:
    """Produz lista de atoms ordenados por ocorrência no documento.
    Atom = unidade semântica mínima: artigo+filhos, parágrafos-livres-contíguos, ou tabela."""
    atoms = []

    # Normalizar blocks com "ordem_original" pra preservar order
    for i, b in enumerate(blocks):
        b["_ord"] = i

    # Agrupar em runs:
    # - run de artigo: começa com type=="artigo", coleta filhos até próximo artigo ou heading
    # - run livre: paragrafo_livre/heading_livre consecutivos SEM artigo no ancestor
    current_run = []
    current_run_type = None  # "artigo" | "livre"

    def flush():
        nonlocal current_run
        if not current_run:
            return
        kind = "artigo" if current_run_type == "artigo" else "texto"
        atoms.append(make_atom_from_blocks(current_run, kind))
        current_run = []

    for b in blocks:
        t = b.get("type", "")
        anc = b.get("ancestors", {}) or {}

        if t == "artigo":
            flush()
            current_run = [b]
            current_run_type = "artigo"
            continue

        if t in ("paragrafo", "inciso", "alinea") and current_run_type == "artigo" and anc.get("artigo"):
            current_run.append(b)
            continue

        # heading grande ou novo contexto: flush e começa run livre se couber
        if t in ("heading_capitulo", "heading_secao", "heading_anexo", "heading_livre"):
            flush()
            # heading vira atom solo
            atoms.append(make_atom_from_blocks([b], "heading"))
            current_run_type = None
            continue

        # paragrafo_livre, paragrafo/inciso/alinea sem artigo ancora = texto livre
        if current_run_type != "livre":
            flush()
            current_run_type = "livre"
        current_run.append(b)

    flush()

    # Tabelas: inserir na posição certa (por página)
    for tbl in tables:
        atom = {
            "kind": "tabela",
            "text": "",
            "ancestors": {},
            "page_start": tbl.get("page", 1),
            "page_end": tbl.get("page", 1),
            "num_chars": 0,
            "table": tbl,
        }
        if tbl.get("suspicious"):
            atom["_suspicious"] = True
        atoms.append(atom)

    atoms.sort(key=lambda a: (a.get("page_start", 0), 0 if a.get("kind") == "tabela" else 1))

    return atoms


# -------- CHILDREN de um atom --------

def split_text_preserving_sentences(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split de texto grande respeitando limites de sentença."""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[\.\!\?])\s+", text)
    out = []
    current = []
    current_len = 0
    for s in sentences:
        s_len = len(s) + 1
        if current_len + s_len > max_chars and current:
            out.append(" ".join(current))
            # overlap: pegar últimas N chars do chunk anterior
            if overlap > 0:
                prev = out[-1]
                start = max(0, len(prev) - overlap)
                tail = prev[start:]
                tail = re.split(r"\s", tail, maxsplit=1)
                current = [tail[-1]] if tail else []
                current_len = len(current[0]) if current else 0
            else:
                current = []
                current_len = 0
        current.append(s)
        current_len += s_len
    if current:
        out.append(" ".join(current))
    return out


def children_of_atom(atom: dict, max_chars: int) -> list[dict]:
    """Produz children chunks de um atom. Tabelas preservam cabeçalho em splits."""
    kind = atom["kind"]
    if kind == "tabela":
        table = atom["table"]
        md_parts = split_table_by_rows(table, max_chars)
        return [
            {
                "text": p,
                "ancestors": atom.get("ancestors", {}),
                "page_start": atom["page_start"],
                "page_end": atom["page_end"],
                "has_table": 1,
                "chunk_type": "tabela",
            }
            for p in md_parts
        ]

    text = atom.get("text", "").strip()
    if not text:
        return []
    parts = split_text_preserving_sentences(text, max_chars, CHILD_OVERLAP_CHARS)
    return [
        {
            "text": p,
            "ancestors": atom.get("ancestors", {}),
            "page_start": atom["page_start"],
            "page_end": atom["page_end"],
            "has_table": 0,
            "chunk_type": kind,
        }
        for p in parts
    ]


# -------- PARENTS --------

def build_parents_from_children(children: list[dict], max_chars: int) -> list[dict]:
    """Agrupa children contíguos em parent chunks ≤ max_chars.
    Parent mantém texto bruto concatenado (com \\n\\n separando)."""
    parents = []
    current = []
    current_len = 0
    for c in children:
        c_len = len(c["text"]) + 2
        if current_len + c_len > max_chars and current:
            parents.append({
                "text": "\n\n".join(c["text"] for c in current),
                "children_idx": [c["_idx"] for c in current],
                "ancestors": current[0].get("ancestors", {}),
                "page_start": current[0]["page_start"],
                "page_end": current[-1]["page_end"],
                "has_table": 1 if any(c.get("has_table") for c in current) else 0,
            })
            current = []
            current_len = 0
        current.append(c)
        current_len += c_len
    if current:
        parents.append({
            "text": "\n\n".join(c["text"] for c in current),
            "children_idx": [c["_idx"] for c in current],
            "ancestors": current[0].get("ancestors", {}),
            "page_start": current[0]["page_start"],
            "page_end": current[-1]["page_end"],
            "has_table": 1 if any(c.get("has_table") for c in current) else 0,
        })
    return parents


# -------- CHUNK PDF --------

def chunk_pdf(extraction_json: dict, pdf_meta: dict) -> list[dict]:
    """Retorna lista de chunks (parents + children intercalados) pra inserir."""
    blocks = extraction_json.get("blocks", [])
    tables = extraction_json.get("tables", [])
    if not blocks and not tables:
        return []

    atoms = extract_atoms(blocks, tables)
    if not atoms:
        return []

    all_children = []
    for atom in atoms:
        kids = children_of_atom(atom, CHILD_MAX_CHARS)
        for k in kids:
            text = (k.get("text") or "").strip()
            if len(text) < MIN_CHUNK_CHARS:
                continue
            # safety final: descartar chunks acima do hard max (pode acontecer com tabelas patológicas)
            if len(text) > HARD_MAX_CHILD_CHARS:
                k["text"] = text[:HARD_MAX_CHILD_CHARS]
            k["_idx"] = len(all_children)
            all_children.append(k)

    if not all_children:
        return []

    parents = build_parents_from_children(all_children, PARENT_MAX_CHARS)

    pdf_id = pdf_meta["pdf_id"]
    pdf_meta_aug = dict(pdf_meta)
    pdf_meta_aug["numeracao_doc"] = extract_numeracao(pdf_meta.get("registro_titulo"))

    ts = datetime.now(timezone.utc).isoformat()
    out_chunks = []
    ordem = 0

    for p_idx, p in enumerate(parents):
        parent_id = f"{pdf_id[:180]}_p{p_idx:03d}"
        parent_breadcrumb = build_breadcrumb(pdf_meta_aug, p["ancestors"])
        out_chunks.append({
            "chunk_id": parent_id,
            "pdf_id": pdf_id,
            "parent_chunk_id": None,
            "chunk_level": 0,
            "ordem_doc": ordem,
            "chunk_type": "parent_mixto" if p["has_table"] else "parent_texto",
            "page_start": p["page_start"],
            "page_end": p["page_end"],
            "tipo_canonico": pdf_meta.get("tipo_canonico"),
            "ano": pdf_meta.get("ano"),
            "numeracao_item": pdf_meta.get("numeracao_item"),
            "breadcrumb": parent_breadcrumb[:500],
            "ancestrais_json": json.dumps(p["ancestors"], ensure_ascii=False)[:1000],
            "text_embed": None,
            "text_raw": p["text"],
            "num_chars": len(p["text"]),
            "num_tokens_est": len(p["text"]) // 4,
            "has_table": p["has_table"],
            "created_at": ts,
        })
        ordem += 1

        for c_rel_idx in p["children_idx"]:
            c = all_children[c_rel_idx]
            child_id = f"{pdf_id[:180]}_c{ordem:04d}"
            breadcrumb = build_breadcrumb(pdf_meta_aug, c["ancestors"])
            text_embed_prefix = f"{breadcrumb}: " if breadcrumb else ""
            text_embed_full = text_embed_prefix + c["text"]
            encoded = text_embed_full.encode("utf-8")[:EMBED_TEXT_MAX_BYTES]
            text_embed = encoded.decode("utf-8", errors="ignore")
            out_chunks.append({
                "chunk_id": child_id,
                "pdf_id": pdf_id,
                "parent_chunk_id": parent_id,
                "chunk_level": 1,
                "ordem_doc": ordem,
                "chunk_type": c["chunk_type"],
                "page_start": c["page_start"],
                "page_end": c["page_end"],
                "tipo_canonico": pdf_meta.get("tipo_canonico"),
                "ano": pdf_meta.get("ano"),
                "numeracao_item": pdf_meta.get("numeracao_item"),
                "breadcrumb": breadcrumb[:500],
                "ancestrais_json": json.dumps(c["ancestors"], ensure_ascii=False)[:1000],
                "text_embed": text_embed,
                "text_raw": c["text"],
                "num_chars": len(c["text"]),
                "num_tokens_est": len(c["text"]) // 4,
                "has_table": c.get("has_table", 0),
                "created_at": ts,
            })
            ordem += 1

    return out_chunks


# -------- PERSISTÊNCIA --------

INSERT_CHUNK_SQL = """
INSERT INTO chunks (
    chunk_id, pdf_id, parent_chunk_id, chunk_level, ordem_doc,
    chunk_type, page_start, page_end,
    tipo_canonico, ano, numeracao_item,
    breadcrumb, ancestrais_json,
    text_embed, text_raw,
    num_chars, num_tokens_est, has_table, created_at
) VALUES (
    :chunk_id, :pdf_id, :parent_chunk_id, :chunk_level, :ordem_doc,
    :chunk_type, :page_start, :page_end,
    :tipo_canonico, :ano, :numeracao_item,
    :breadcrumb, :ancestrais_json,
    :text_embed, :text_raw,
    :num_chars, :num_tokens_est, :has_table, :created_at
)
"""

DELETE_PDF_CHUNKS_SQL = "DELETE FROM chunks WHERE pdf_id = :pdf_id"

UPDATE_MANIFEST_SQL = "UPDATE manifest SET status_chunk = :status WHERE pdf_id = :pdf_id"


def save_chunks(conn, pdf_id: str, chunks: list[dict]):
    cur = conn.cursor()
    cur.execute(DELETE_PDF_CHUNKS_SQL, {"pdf_id": pdf_id})
    if chunks:
        cur.setinputsizes(text_raw=oracledb.DB_TYPE_CLOB)
        cur.executemany(INSERT_CHUNK_SQL, chunks)
    try:
        cur.execute(UPDATE_MANIFEST_SQL, {"status": "success", "pdf_id": pdf_id})
    except oracledb.DatabaseError as e:
        if "ORA-00904" in str(e):
            pass
        else:
            raise
    conn.commit()


# -------- MAIN --------

SELECT_SQL = """
SELECT m.pdf_id, m.tipo_canonico, m.ano, m.numeracao_item, m.registro_titulo,
       e.extracted_json, e.num_pages
FROM manifest m
JOIN extractions e ON e.pdf_id = m.pdf_id
WHERE e.extracted_json IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.pdf_id = m.pdf_id)
ORDER BY m.ano, m.pdf_id
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pdf-id", type=str, default=None)
    args = parser.parse_args()

    pwd = env_or_die("DB_ADMIN_PASS")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    print(f"[INFO] Conectando em {DSN}...")
    select_conn = make_db_conn(pwd, wallet_pwd)
    cur = select_conn.cursor()

    if args.pdf_id:
        cur.execute("""
            SELECT m.pdf_id, m.tipo_canonico, m.ano, m.numeracao_item, m.registro_titulo,
                   e.extracted_json, e.num_pages
            FROM manifest m JOIN extractions e ON e.pdf_id = m.pdf_id
            WHERE m.pdf_id = :pid
        """, {"pid": args.pdf_id})
    else:
        sql = SELECT_SQL
        if args.force:
            sql = sql.replace("AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.pdf_id = m.pdf_id)", "")
        if args.limit > 0:
            sql += f" FETCH FIRST {args.limit} ROWS ONLY"
        cur.arraysize = 200
        cur.prefetchrows = 201
        cur.execute(sql)

    rows = []
    while True:
        chunk = cur.fetchmany(200)
        if not chunk:
            break
        for r in chunk:
            pid, tipo, ano, num, titulo, ejson_lob, npages = r
            ejson = ejson_lob.read() if ejson_lob else None
            rows.append((pid, tipo, ano, num, titulo, ejson, npages))

    select_conn.close()
    print(f"[INFO] {len(rows):,} PDFs a chunkar")
    if not rows:
        print("[OK] Nada a fazer.")
        return

    db_pool = oracledb.create_pool(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
        min=2, max=max(args.workers + 2, 4), increment=1,
        getmode=oracledb.POOL_GETMODE_WAIT,
    )

    stats = {"ok": 0, "fail": 0, "n_chunks": 0, "start": time.time()}
    total = len(rows)

    def worker(row):
        pdf_id, tipo, ano, num, titulo, ejson_str, npages = row
        try:
            extraction = json.loads(ejson_str)
            meta = {
                "pdf_id": pdf_id, "tipo_canonico": tipo, "ano": ano,
                "numeracao_item": num, "registro_titulo": titulo,
            }
            chunks = chunk_pdf(extraction, meta)
            with db_pool.acquire() as conn:
                save_chunks(conn, pdf_id, chunks)
            return ("ok", pdf_id, len(chunks))
        except Exception as e:
            tb = traceback.format_exc(limit=2)
            return ("fail", pdf_id, f"{type(e).__name__}: {e}\n{tb}")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, r) for r in rows]
        for i, fut in enumerate(as_completed(futures), start=1):
            status, pdf_id, info = fut.result()
            if status == "ok":
                stats["ok"] += 1
                stats["n_chunks"] += info
                if i % 50 == 0 or i == 1:
                    elapsed = time.time() - stats["start"]
                    rate = stats["ok"] / elapsed if elapsed else 0
                    eta = (total - i) / rate / 60 if rate else 0
                    print(f"  [{i}/{total}] {pdf_id[:40]:40s} +{info:3d} chunks  "
                          f"total={stats['n_chunks']:,}  taxa={rate:.1f}/s  eta={eta:.0f}min",
                          flush=True)
            else:
                stats["fail"] += 1
                print(f"  [{i}/{total}] [FAIL] {pdf_id[:40]:40s} {str(info)[:120]}", flush=True)

    db_pool.close()

    elapsed = time.time() - stats["start"]
    print(f"\n[FIM] ok={stats['ok']:,} fail={stats['fail']:,} "
          f"chunks_gerados={stats['n_chunks']:,} ({elapsed/60:.1f}min)")


if __name__ == "__main__":
    main()
