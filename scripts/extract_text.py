"""
extract_text.py — extração estrutural de PDFs ANEEL com pymupdf + pdfplumber.

Pipeline:
  1. Seleciona do DB PDFs com status_download='success' AND status_extract != 'success'
  2. Streaming: baixa bytes do Object Storage → abre em memória → descarta
  3. Classifica: nativo vs. precisa OCR (char/página)
  4. Extrai blocos estruturados via pymupdf (texto + posição + fonte)
  5. Detecta e remove header/footer repetidos (linhas em > 60% das páginas)
  6. Detecta hierarquia legal via regex (Capítulo, Seção, Art., §, Inciso)
  7. Extrai tabelas: pymupdf primeiro, re-tenta com pdfplumber se suspeita
  8. Computa quality_score
  9. Salva JSON + Markdown em tabela EXTRACTIONS, atualiza status_extract

Idempotente: rodar 2x não reprocessa (filtro status_extract != 'success').
Paralelizado com ThreadPoolExecutor (pymupdf libera GIL no C++).

Uso:
    $env:DB_ADMIN_PASS = "..."
    $env:OCI_NAMESPACE = "grgdsxx4khc6"
    python scripts/extract_text.py
    python scripts/extract_text.py --limit 10          # testar com 10 PDFs
    python scripts/extract_text.py --pdf-id XYZ123     # 1 PDF específico
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import oci
import oracledb
import pymupdf
import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"

DSN = "aneelrag_medium"
USER = "ADMIN"
BUCKET = "aneel-rag"

EXTRACTOR_NAME = "pymupdf"
EXTRACTOR_VERSION = pymupdf.__version__

WORKERS = int(os.environ.get("EXTRACT_WORKERS", "8"))

# -------- REGEX DE HIERARQUIA LEGAL BRASILEIRA --------
RE_CAPITULO = re.compile(r"^CAP[IÍ]TULO\s+([IVXLCDM]+)\b\s*[-–—]?\s*(.*)$", re.IGNORECASE)
RE_SECAO    = re.compile(r"^SEÇ[AÃ]O\s+([IVXLCDM]+)\b\s*[-–—]?\s*(.*)$", re.IGNORECASE)
RE_ARTIGO   = re.compile(r"^Art\.?\s*(\d+)\s*[º°]?\s*[-–—\.]?\s*(.*)$", re.IGNORECASE)
RE_PARAG    = re.compile(r"^(§\s*\d+\s*[º°]?|Parágrafo\s+único)\s*[\.\-–—]?\s*(.*)$", re.IGNORECASE)
RE_INCISO   = re.compile(r"^([IVXLCDM]+)\s*[-–—]\s*(.+)$")
RE_ALINEA   = re.compile(r"^([a-z])\)\s*(.+)$")
RE_ANEXO    = re.compile(r"^ANEXO\s+([IVXLCDM\d]+)?\s*[-–—]?\s*(.*)$", re.IGNORECASE)

# Heurísticas pra qualidade
MIN_CHARS_PER_PAGE_NATIVE = 100
SUSPICIOUS_TABLE_MIN_COLS = 2
SUSPICIOUS_TABLE_MIN_ROWS = 2
TABLE_HEAVY_THRESHOLD = 3

# Tipos canônicos que merecem upgrade pra Docling (alta densidade de tabelas)
DOCLING_CANDIDATE_TIPOS = {"nota_tecnica", "edital", "anexo", "relatorio_analise"}


# -------- CONEXÕES --------

def env_or_die(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"ERRO: defina {name} antes de rodar.")
    return v


def make_oci_client():
    config = oci.config.from_file()
    return oci.object_storage.ObjectStorageClient(config)


def make_db_conn(pwd: str, wallet_pwd: str):
    return oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )


# -------- DOWNLOAD DO BUCKET (STREAMING) --------

def fetch_pdf_bytes(oci_client, namespace: str, bucket_path: str) -> bytes:
    resp = oci_client.get_object(
        namespace_name=namespace,
        bucket_name=BUCKET,
        object_name=bucket_path,
    )
    return resp.data.content


# -------- CLASSIFICAÇÃO RÁPIDA --------

def classify_pdf(doc: pymupdf.Document) -> dict:
    """Retorna dict com num_pages, chars_total, chars_per_page, is_native_text, needs_ocr."""
    num_pages = doc.page_count
    chars_total = 0
    for p in doc:
        chars_total += len(p.get_text("text"))
    chars_per_page = chars_total / num_pages if num_pages else 0
    is_native = chars_per_page >= MIN_CHARS_PER_PAGE_NATIVE
    return {
        "num_pages": num_pages,
        "chars_total": chars_total,
        "chars_per_page": round(chars_per_page, 1),
        "is_native_text": is_native,
        "needs_ocr": not is_native,
    }


# -------- NORMALIZAÇÃO DE TEXTO --------

def fix_word_breaks(text: str) -> str:
    """'palavra-\\npalavra2' → 'palavrapalavra2'; quebra de linha normal → espaço."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"(?<![\.\!\?\:])\n(?!\n)", " ", text)
    return text


def normalize_ws(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_for_match(text: str) -> str:
    s = re.sub(r"\d+", "N", text.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


# -------- DETECÇÃO DE HEADER/FOOTER --------

def find_headers_footers(doc: pymupdf.Document, n_sample: int = 20) -> tuple[list[str], list[str]]:
    """Detecta linhas de header/footer que aparecem em >= 50% das páginas.
    Retorna (headers_normalizados, footers_normalizados)."""
    num_pages = doc.page_count
    if num_pages < 3:
        return [], []

    pages_to_check = min(n_sample, num_pages)
    step = max(1, num_pages // pages_to_check)

    h_counter = Counter()
    f_counter = Counter()

    for i in range(0, num_pages, step):
        if i >= num_pages:
            break
        page = doc[i]
        ph = page.rect.height
        blocks = page.get_text("blocks")
        if not blocks:
            continue
        blocks_sorted = sorted(blocks, key=lambda b: b[1])

        for b in blocks_sorted[:2]:
            text = (b[4] or "").strip()
            if not text:
                continue
            first_line = text.split("\n", 1)[0].strip()
            if b[1] < ph * 0.12 and 10 <= len(first_line) <= 200:
                h_counter[normalize_for_match(first_line)] += 1

        for b in blocks_sorted[-2:]:
            text = (b[4] or "").strip()
            if not text:
                continue
            last_line = text.strip().split("\n")[-1].strip()
            if b[3] > ph * 0.88 and 10 <= len(last_line) <= 200:
                f_counter[normalize_for_match(last_line)] += 1

    actual_samples = sum(1 for _ in range(0, num_pages, step))
    threshold = max(2, int(actual_samples * 0.5))
    headers = [t for t, c in h_counter.items() if c >= threshold]
    footers = [t for t, c in f_counter.items() if c >= threshold]
    return headers, footers


def matches_header_footer(text: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    norm = normalize_for_match(text)[:200]
    for p in patterns:
        if not p or len(p) < 10:
            continue
        if norm == p:
            return True
        if p in norm or norm in p:
            return True
    return False


# -------- PARSER DE HIERARQUIA LEGAL --------

def classify_line(line: str) -> tuple[str, dict] | None:
    """Retorna (tipo, metadata) se a linha é um marcador estrutural conhecido, senão None."""
    s = line.strip()
    if not s:
        return None

    m = RE_CAPITULO.match(s)
    if m:
        return "capitulo", {"numero": m.group(1), "titulo": m.group(2).strip()}
    m = RE_SECAO.match(s)
    if m:
        return "secao", {"numero": m.group(1), "titulo": m.group(2).strip()}
    m = RE_ANEXO.match(s)
    if m and s.isupper():
        return "anexo", {"numero": m.group(1) or "", "titulo": m.group(2).strip()}
    m = RE_ARTIGO.match(s)
    if m:
        return "artigo", {"numero": m.group(1), "texto": m.group(2).strip()}
    m = RE_PARAG.match(s)
    if m:
        return "paragrafo", {"numero": m.group(1).strip(), "texto": m.group(2).strip()}
    m = RE_INCISO.match(s)
    if m:
        return "inciso", {"numero": m.group(1), "texto": m.group(2).strip()}
    m = RE_ALINEA.match(s)
    if m:
        return "alinea", {"marcador": m.group(1), "texto": m.group(2).strip()}
    return None


# -------- SPLIT INLINE DE MARCADORES LEGAIS --------

# Detecta "Art. N°" no meio do texto (precedido por espaço/ponto, não no início de string)
RE_INLINE_ART = re.compile(r"(?<=[\.\)\s])\s*(Art\.?\s*\d+\s*[º°]?)", re.IGNORECASE)
RE_INLINE_PARAG = re.compile(r"(?<=[\.\)\s])\s*(§\s*\d+\s*[º°]?|Parágrafo\s+único)", re.IGNORECASE)


# Só considera "Art. N°" como NOVO artigo se for precedido por ponto final + espaço
# E seguido imediatamente por maiúscula (Constituir, Esta, Ficam, Delega-se, etc.)
# evita pegar "nos termos do art. 50, § 4º" ou "art. 8 da Lei 9.074"
RE_INLINE_ART_STRICT = re.compile(
    r"(?:(?<=\.\s)|(?<=\.\t)|(?<=\.\n))(Art\.\s*\d+\s*[º°])\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ])"
)


def split_by_legal_markers(text: str) -> list[str]:
    """Quebra o texto quando encontra § (pouco comum como ref cruzada) ou Art. estrito."""
    if not text:
        return [text]
    positions = []
    for m in RE_INLINE_ART_STRICT.finditer(text):
        start = m.start(1)
        if start > 0:
            positions.append(start)
    for m in RE_INLINE_PARAG.finditer(text):
        start = m.start(1)
        if start > 0:
            positions.append(start)
    if not positions:
        return [text]
    positions = sorted(set(positions))
    out = []
    prev = 0
    for p in positions:
        chunk = text[prev:p].strip()
        if chunk:
            out.append(chunk)
        prev = p
    tail = text[prev:].strip()
    if tail:
        out.append(tail)
    return out


# -------- GEOMETRIA --------

def bbox_overlaps(b1: tuple, b2: tuple, threshold: float = 0.5) -> bool:
    """Retorna True se >= threshold da área de b1 está dentro de b2."""
    x0 = max(b1[0], b2[0]); y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2]); y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return False
    intersection = (x1 - x0) * (y1 - y0)
    b1_area = (b1[2] - b1[0]) * (b1[3] - b1[1])
    return (intersection / b1_area) >= threshold if b1_area > 0 else False


def page_median_font_size(page_dict: dict) -> float:
    sizes = []
    for b in page_dict.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size", 0)
                text = span.get("text", "").strip()
                if text and size > 0:
                    sizes.append(size)
    if not sizes:
        return 10.0
    sizes.sort()
    return sizes[len(sizes) // 2]


def block_text_and_size(block: dict) -> tuple[str, float]:
    """Concatena texto do block e retorna font size médio."""
    parts = []
    sizes = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "")
            if text:
                parts.append(text)
                sizes.append(span.get("size", 0))
        parts.append("\n")
    text = "".join(parts).rstrip()
    avg_size = sum(sizes) / len(sizes) if sizes else 10.0
    return text, avg_size


# -------- EXTRAÇÃO DE BLOCOS ESTRUTURADOS --------

def extract_blocks(doc: pymupdf.Document, header_pats: list[str], footer_pats: list[str],
                   table_bboxes_by_page: dict) -> list[dict]:
    """Extrai blocos estruturais usando get_text('dict') + filtragem por bbox de tabela.
    Cada bloco visual do pymupdf vira um item (evita colar parágrafos)."""
    all_blocks = []
    ctx = {"capitulo": None, "secao": None, "artigo": None, "paragrafo": None, "anexo": None}

    for page_idx, page in enumerate(doc, start=1):
        page_dict = page.get_text("dict")
        median_size = page_median_font_size(page_dict)
        table_bboxes = table_bboxes_by_page.get(page_idx, [])

        page_blocks = []
        for b in page_dict.get("blocks", []):
            if b.get("type") != 0:
                continue
            bbox = b.get("bbox")
            if not bbox:
                continue
            if any(bbox_overlaps(bbox, tb) for tb in table_bboxes):
                continue

            raw_text, avg_size = block_text_and_size(b)
            if not raw_text.strip():
                continue

            text = fix_word_breaks(raw_text)
            text = normalize_ws(text)
            if not text:
                continue

            if matches_header_footer(text, header_pats) or matches_header_footer(text, footer_pats):
                continue

            # split inline se encontrar marcadores legais (Art. N°, §) no meio
            sub_texts = split_by_legal_markers(text)
            for st in sub_texts:
                page_blocks.append({"bbox": bbox, "text": st, "size": avg_size})

        page_blocks.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))

        # merge blocks livres adjacentes (para juntar linhas partidas de um mesmo parágrafo)
        merged = []
        for pb in page_blocks:
            cl_curr = classify_line(pb["text"])
            if merged and cl_curr is None:
                last = merged[-1]
                cl_last = classify_line(last["text"])
                if cl_last is None:
                    # calcular distância vertical
                    gap = pb["bbox"][1] - last["bbox"][3]
                    size = (pb["size"] + last["size"]) / 2
                    size_match = abs(pb["size"] - last["size"]) < 1.0
                    last_ends_clean = last["text"].rstrip().endswith((".", "!", "?", ":", ";"))
                    if gap < size * 1.5 and size_match and not last_ends_clean:
                        last["text"] = (last["text"].rstrip() + " " + pb["text"].lstrip()).strip()
                        last["bbox"] = (min(last["bbox"][0], pb["bbox"][0]),
                                        last["bbox"][1],
                                        max(last["bbox"][2], pb["bbox"][2]),
                                        pb["bbox"][3])
                        continue
            merged.append(pb)
        page_blocks = merged

        for pb in page_blocks:
            text = pb["text"]
            size = pb["size"]

            cl = classify_line(text)
            if cl:
                tipo, meta = cl
                if tipo == "capitulo":
                    ctx["capitulo"] = f"CAP {meta['numero']}"
                    ctx["secao"] = None
                    ctx["artigo"] = None
                    ctx["paragrafo"] = None
                    all_blocks.append({
                        "type": "heading_capitulo",
                        "page": page_idx,
                        "text": f"CAPÍTULO {meta['numero']} - {meta['titulo']}".strip(" -"),
                        "metadata": meta,
                        "ancestors": {"anexo": ctx["anexo"]} if ctx["anexo"] else {},
                    })
                    continue
                if tipo == "secao":
                    ctx["secao"] = f"Seção {meta['numero']}"
                    ctx["artigo"] = None
                    ctx["paragrafo"] = None
                    all_blocks.append({
                        "type": "heading_secao",
                        "page": page_idx,
                        "text": f"Seção {meta['numero']} - {meta['titulo']}".strip(" -"),
                        "metadata": meta,
                        "ancestors": {k: v for k, v in ctx.items() if v and k in ("capitulo", "anexo")},
                    })
                    continue
                if tipo == "anexo":
                    ctx["anexo"] = f"ANEXO {meta['numero']}"
                    ctx["capitulo"] = None
                    ctx["secao"] = None
                    ctx["artigo"] = None
                    ctx["paragrafo"] = None
                    all_blocks.append({
                        "type": "heading_anexo",
                        "page": page_idx,
                        "text": f"ANEXO {meta['numero']} - {meta['titulo']}".strip(" -"),
                        "metadata": meta,
                        "ancestors": {},
                    })
                    continue
                if tipo == "artigo":
                    ctx["artigo"] = f"Art. {meta['numero']}º"
                    ctx["paragrafo"] = None
                    all_blocks.append({
                        "type": "artigo",
                        "page": page_idx,
                        "text": meta["texto"] or text,
                        "metadata": meta,
                        "ancestors": {k: v for k, v in ctx.items() if v and k != "artigo"},
                    })
                    continue
                if tipo == "paragrafo":
                    ctx["paragrafo"] = meta["numero"]
                    all_blocks.append({
                        "type": "paragrafo",
                        "page": page_idx,
                        "text": meta["texto"] or text,
                        "metadata": meta,
                        "ancestors": {k: v for k, v in ctx.items() if v and k != "paragrafo"},
                    })
                    continue
                if tipo == "inciso":
                    all_blocks.append({
                        "type": "inciso",
                        "page": page_idx,
                        "text": meta["texto"],
                        "metadata": meta,
                        "ancestors": {k: v for k, v in ctx.items() if v},
                    })
                    continue
                if tipo == "alinea":
                    all_blocks.append({
                        "type": "alinea",
                        "page": page_idx,
                        "text": meta["texto"],
                        "metadata": meta,
                        "ancestors": {k: v for k, v in ctx.items() if v},
                    })
                    continue

            if size >= median_size * 1.25 and len(text) < 200:
                block_type = "heading_livre"
                # heading em caixa alta sugere novo documento/seção → resetar ancestors legais
                if text == text.upper() and len(text) > 15:
                    ctx["artigo"] = None
                    ctx["paragrafo"] = None
            else:
                block_type = "paragrafo_livre"
            all_blocks.append({
                "type": block_type,
                "page": page_idx,
                "text": text,
                "metadata": {"font_size": round(size, 1)},
                "ancestors": {k: v for k, v in ctx.items() if v},
            })

    return all_blocks


# -------- EXTRAÇÃO DE TABELAS --------

def is_suspicious_table(rows: list[list]) -> bool:
    """Tabela suspeita: poucas linhas/colunas ou muitas células vazias."""
    if not rows:
        return True
    if len(rows) < SUSPICIOUS_TABLE_MIN_ROWS:
        return True
    cols = max(len(r) for r in rows)
    if cols < SUSPICIOUS_TABLE_MIN_COLS:
        return True
    empty_cells = sum(1 for r in rows for c in r if not (c or "").strip())
    total_cells = sum(len(r) for r in rows)
    if total_cells and (empty_cells / total_cells) > 0.6:
        return True
    return False


def extract_tables_pymupdf(doc: pymupdf.Document) -> tuple[list[dict], dict]:
    """Retorna (tables, bboxes_por_page)."""
    tables = []
    bboxes_by_page = {}
    for page_idx, page in enumerate(doc, start=1):
        try:
            tf = page.find_tables()
        except Exception:
            continue
        for t in tf:
            try:
                rows = t.extract()
            except Exception:
                continue
            if not rows:
                continue
            bbox = tuple(t.bbox) if hasattr(t, "bbox") else None
            if bbox:
                bboxes_by_page.setdefault(page_idx, []).append(bbox)
            tables.append({
                "page": page_idx,
                "bbox": list(bbox) if bbox else None,
                "rows": rows,
                "n_rows": len(rows),
                "n_cols": max(len(r) for r in rows) if rows else 0,
                "suspicious": is_suspicious_table(rows),
                "source": "pymupdf",
            })
    return tables, bboxes_by_page


def extract_tables_pdfplumber(pdf_bytes: bytes, pages_to_retry: list[int]) -> list[dict]:
    """Re-extrai tabelas com pdfplumber só das páginas suspeitas."""
    tables = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_idx in pages_to_retry:
                if page_idx < 1 or page_idx > len(pdf.pages):
                    continue
                try:
                    page = pdf.pages[page_idx - 1]
                    for tbl in page.extract_tables() or []:
                        if not tbl:
                            continue
                        tables.append({
                            "page": page_idx,
                            "rows": tbl,
                            "n_rows": len(tbl),
                            "n_cols": max(len(r) for r in tbl) if tbl else 0,
                            "suspicious": is_suspicious_table(tbl),
                            "source": "pdfplumber",
                        })
                except Exception:
                    continue
    except Exception:
        pass
    return tables


# -------- QUALITY SCORE --------

def compute_quality_score(metrics: dict) -> float:
    score = 1.0
    if metrics.get("needs_ocr"):
        score -= 0.4
    if metrics.get("chars_per_page", 0) < 300:
        score -= 0.15
    n_tables = metrics.get("num_tables", 0)
    n_susp = metrics.get("num_suspicious_tables", 0)
    if n_tables > 0:
        score -= 0.1 * (n_susp / n_tables)
    if metrics.get("num_articles", 0) == 0 and metrics.get("num_pages", 0) > 5:
        score -= 0.05
    return max(0.0, round(score, 3))


# -------- SERIALIZAÇÃO --------

def build_markdown(blocks: list[dict], tables: list[dict]) -> str:
    """Gera Markdown legível pro LLM e pra debug visual."""
    out = []
    for b in blocks:
        t = b["type"]
        text = b["text"]
        if t == "heading_capitulo":
            out.append(f"\n# {text}\n")
        elif t == "heading_secao":
            out.append(f"\n## {text}\n")
        elif t == "heading_anexo":
            out.append(f"\n# {text}\n")
        elif t == "artigo":
            num = b["metadata"].get("numero", "")
            out.append(f"\n**Art. {num}º** {text}\n")
        elif t == "paragrafo":
            num = b["metadata"].get("numero", "")
            out.append(f"\n{num}. {text}\n")
        elif t == "inciso":
            num = b["metadata"].get("numero", "")
            out.append(f"  - {num}. {text}\n")
        elif t == "alinea":
            mark = b["metadata"].get("marcador", "")
            out.append(f"    - {mark}) {text}\n")
        else:
            out.append(f"\n{text}\n")

    for tbl in tables:
        out.append(f"\n**[Tabela p.{tbl['page']} — {tbl['n_rows']}×{tbl['n_cols']}]**\n\n")
        rows = tbl["rows"]
        if rows:
            header = rows[0]
            out.append("| " + " | ".join((c or "").strip() for c in header) + " |\n")
            out.append("| " + " | ".join(["---"] * len(header)) + " |\n")
            for row in rows[1:]:
                out.append("| " + " | ".join((c or "").strip() for c in row) + " |\n")
    return "".join(out)


# -------- PROCESSO POR PDF --------

def process_pdf(pdf_id: str, url: str, bucket_path: str, tipo_canonico: str,
                oci_client, namespace: str) -> dict:
    """Retorna dict com todos os campos pra inserir em extractions."""
    t0 = time.time()

    pdf_bytes = fetch_pdf_bytes(oci_client, namespace, bucket_path)
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise RuntimeError(f"pymupdf.open falhou: {e}")

    try:
        meta = classify_pdf(doc)

        header_pats, footer_pats = find_headers_footers(doc)

        tables, table_bboxes = extract_tables_pymupdf(doc)

        blocks = extract_blocks(doc, header_pats, footer_pats, table_bboxes)

        suspicious_pages = sorted({t["page"] for t in tables if t["suspicious"]})
        if suspicious_pages:
            retry = extract_tables_pdfplumber(pdf_bytes, suspicious_pages)
            if retry:
                better = {t["page"]: t for t in retry if not t["suspicious"]}
                tables = [t for t in tables if t["page"] not in better] + list(better.values())

        num_articles = sum(1 for b in blocks if b["type"] == "artigo")
        num_tables = len(tables)
        num_susp = sum(1 for t in tables if t["suspicious"])
        needs_docling = (num_tables >= TABLE_HEAVY_THRESHOLD or
                         tipo_canonico in DOCLING_CANDIDATE_TIPOS or
                         num_susp >= 2)

        metrics = {
            **meta,
            "num_blocks": len(blocks),
            "num_articles": num_articles,
            "num_tables": num_tables,
            "num_suspicious_tables": num_susp,
        }
        quality = compute_quality_score(metrics)

        header_pat = "; ".join(header_pats)[:500] if header_pats else None
        footer_pat = "; ".join(footer_pats)[:500] if footer_pats else None
        struct_json = {
            "pdf_id": pdf_id,
            "extractor": EXTRACTOR_NAME,
            "extractor_version": EXTRACTOR_VERSION,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "quality_score": quality,
            "header_patterns": header_pats,
            "footer_patterns": footer_pats,
            "blocks": blocks,
            "tables": tables,
        }

        markdown = build_markdown(blocks, tables)

        return {
            "pdf_id": pdf_id,
            "extractor": EXTRACTOR_NAME,
            "extractor_version": EXTRACTOR_VERSION,
            "extracted_at": struct_json["extracted_at"],
            "num_pages": meta["num_pages"],
            "num_blocks": len(blocks),
            "num_articles": num_articles,
            "num_tables": num_tables,
            "num_suspicious_tables": num_susp,
            "chars_total": meta["chars_total"],
            "chars_per_page": meta["chars_per_page"],
            "quality_score": quality,
            "is_native_text": 1 if meta["is_native_text"] else 0,
            "needs_ocr": 1 if meta["needs_ocr"] else 0,
            "needs_docling": 1 if needs_docling else 0,
            "header_pattern": header_pat,
            "footer_pattern": footer_pat,
            "extracted_json": json.dumps(struct_json, ensure_ascii=False),
            "extracted_markdown": markdown,
            "elapsed_s": round(time.time() - t0, 2),
        }
    finally:
        doc.close()


# -------- PERSISTÊNCIA --------

UPSERT_SQL = """
MERGE INTO extractions e
USING (SELECT :pdf_id AS pdf_id FROM dual) s
ON (e.pdf_id = s.pdf_id)
WHEN MATCHED THEN UPDATE SET
  extractor = :extractor,
  extractor_version = :extractor_version,
  extracted_at = :extracted_at,
  num_pages = :num_pages,
  num_blocks = :num_blocks,
  num_articles = :num_articles,
  num_tables = :num_tables,
  num_suspicious_tables = :num_suspicious_tables,
  chars_total = :chars_total,
  chars_per_page = :chars_per_page,
  quality_score = :quality_score,
  is_native_text = :is_native_text,
  needs_ocr = :needs_ocr,
  needs_docling = :needs_docling,
  header_pattern = SUBSTR(:header_pattern, 1, 500),
  footer_pattern = SUBSTR(:footer_pattern, 1, 500),
  extracted_json = :extracted_json,
  extracted_markdown = :extracted_markdown,
  last_error = NULL
WHEN NOT MATCHED THEN INSERT (
  pdf_id, extractor, extractor_version, extracted_at,
  num_pages, num_blocks, num_articles, num_tables, num_suspicious_tables,
  chars_total, chars_per_page, quality_score,
  is_native_text, needs_ocr, needs_docling,
  header_pattern, footer_pattern,
  extracted_json, extracted_markdown
) VALUES (
  :pdf_id, :extractor, :extractor_version, :extracted_at,
  :num_pages, :num_blocks, :num_articles, :num_tables, :num_suspicious_tables,
  :chars_total, :chars_per_page, :quality_score,
  :is_native_text, :needs_ocr, :needs_docling,
  SUBSTR(:header_pattern, 1, 500), SUBSTR(:footer_pattern, 1, 500),
  :extracted_json, :extracted_markdown
)
"""

UPDATE_MANIFEST_SQL = """
UPDATE manifest SET status_extract = :status WHERE pdf_id = :pdf_id
"""

MARK_FAILED_SQL = """
MERGE INTO extractions e
USING (SELECT :pdf_id AS pdf_id FROM dual) s
ON (e.pdf_id = s.pdf_id)
WHEN MATCHED THEN UPDATE SET last_error = SUBSTR(:err, 1, 2000), extracted_at = :ts
WHEN NOT MATCHED THEN INSERT (pdf_id, extractor, extracted_at, last_error)
VALUES (:pdf_id, :extractor, :ts, SUBSTR(:err, 1, 2000))
"""


DB_FIELDS = {
    "pdf_id", "extractor", "extractor_version", "extracted_at",
    "num_pages", "num_blocks", "num_articles", "num_tables", "num_suspicious_tables",
    "chars_total", "chars_per_page", "quality_score",
    "is_native_text", "needs_ocr", "needs_docling",
    "header_pattern", "footer_pattern",
    "extracted_json", "extracted_markdown",
}


def save_extraction(conn, row: dict):
    db_row = {k: row.get(k) for k in DB_FIELDS}
    cur = conn.cursor()
    cur.setinputsizes(
        extracted_json=oracledb.DB_TYPE_CLOB,
        extracted_markdown=oracledb.DB_TYPE_CLOB,
    )
    cur.execute(UPSERT_SQL, db_row)
    cur.execute(UPDATE_MANIFEST_SQL, {"status": "success", "pdf_id": row["pdf_id"]})
    conn.commit()


def save_failure(conn, pdf_id: str, err: str):
    cur = conn.cursor()
    ts = datetime.now(timezone.utc).isoformat()
    cur.execute(MARK_FAILED_SQL, {
        "pdf_id": pdf_id, "err": err[:2000], "ts": ts, "extractor": EXTRACTOR_NAME,
    })
    cur.execute(UPDATE_MANIFEST_SQL, {"status": "failed_extract", "pdf_id": pdf_id})
    conn.commit()


# -------- MAIN --------

SELECT_SQL = """
SELECT m.pdf_id, m.url, m.bucket_path, m.tipo_canonico
FROM manifest m
WHERE m.status_download = 'success'
  AND (m.status_extract IS NULL OR m.status_extract NOT IN ('success','failed_extract'))
ORDER BY m.ano, m.pdf_id
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="processar só N PDFs (0=todos)")
    parser.add_argument("--pdf-id", type=str, default=None, help="processar só 1 PDF específico")
    parser.add_argument("--pdf-ids-file", type=str, default=None, help="arquivo com 1 pdf_id por linha")
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--retry-failed", action="store_true", help="re-tentar PDFs com status_extract='failed_extract'")
    parser.add_argument("--force", action="store_true", help="re-extrair mesmo se já está 'success'")
    args = parser.parse_args()

    pwd = env_or_die("DB_ADMIN_PASS")
    namespace = env_or_die("OCI_NAMESPACE")
    if not WALLET_PASS_FILE.exists():
        sys.exit(f"ERRO: wallet.pass não encontrado em {WALLET_PASS_FILE}")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    print(f"[INFO] Conectando em {DSN}...")
    select_conn = make_db_conn(pwd, wallet_pwd)
    cur = select_conn.cursor()

    if args.pdf_id or args.pdf_ids_file:
        if args.pdf_ids_file:
            pids = [line.strip() for line in Path(args.pdf_ids_file).read_text().splitlines() if line.strip()]
        else:
            pids = [args.pdf_id]
        placeholders = ",".join(f":p{i}" for i in range(len(pids)))
        sql = f"""
        SELECT m.pdf_id, m.url, m.bucket_path, m.tipo_canonico
        FROM manifest m
        WHERE m.pdf_id IN ({placeholders}) AND m.status_download = 'success'
        """
        params = {f"p{i}": p for i, p in enumerate(pids)}
        cur.execute(sql, params)
    else:
        sql = SELECT_SQL
        if args.force:
            sql = sql.replace("AND (m.status_extract IS NULL OR m.status_extract NOT IN ('success','failed_extract'))", "")
        elif args.retry_failed:
            sql = sql.replace("NOT IN ('success','failed_extract')", "!= 'success'")
        if args.limit > 0:
            sql += f" FETCH FIRST {args.limit} ROWS ONLY"
        cur.arraysize = 500
        cur.prefetchrows = 501
        cur.execute(sql)

    rows = cur.fetchall()
    select_conn.close()
    print(f"[INFO] {len(rows):,} PDFs a extrair")
    if not rows:
        print("[OK] Nada a fazer.")
        return

    print(f"[INFO] Abrindo {args.workers} workers em paralelo...")
    oci_client = make_oci_client()

    db_pool = oracledb.create_pool(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
        min=2, max=max(args.workers + 2, 4), increment=1,
        getmode=oracledb.POOL_GETMODE_WAIT,
    )

    stats = {"ok": 0, "fail": 0, "start": time.time()}
    total = len(rows)

    def worker(row):
        pdf_id, url, bucket_path, tipo_canonico = row
        try:
            result = process_pdf(pdf_id, url, bucket_path, tipo_canonico or "",
                                 oci_client, namespace)
            with db_pool.acquire() as conn:
                save_extraction(conn, result)
            return ("ok", pdf_id, result["quality_score"], result["elapsed_s"],
                    result["num_pages"], result["num_tables"], result["needs_docling"])
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            err_msg = f"{type(e).__name__}: {e}"
            try:
                with db_pool.acquire() as conn:
                    save_failure(conn, pdf_id, err_msg + "\n" + tb)
            except Exception as ee:
                print(f"  [!] falha ao salvar erro pra {pdf_id}: {ee}", flush=True)
            return ("fail", pdf_id, err_msg, 0, 0, 0, 0)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, r) for r in rows]
        for i, fut in enumerate(as_completed(futures), start=1):
            status, pdf_id, *rest = fut.result()
            if status == "ok":
                stats["ok"] += 1
                q, elapsed, pages, n_tbl, needs_doc = rest
                if i % 20 == 0 or i == 1:
                    elapsed_total = time.time() - stats["start"]
                    rate = stats["ok"] / elapsed_total if elapsed_total else 0
                    eta = (total - i) / rate / 60 if rate else 0
                    doc_flag = " [→DOCLING]" if needs_doc else ""
                    print(f"  [{i}/{total}] {pdf_id[:40]:40s} q={q:.2f} "
                          f"p={pages} t={n_tbl} {elapsed:.1f}s "
                          f"taxa={rate:.1f}/s eta={eta:.0f}min{doc_flag}", flush=True)
            else:
                stats["fail"] += 1
                err = rest[0]
                print(f"  [{i}/{total}] [FAIL] {pdf_id[:40]:40s} {err[:100]}", flush=True)

    db_pool.close()

    elapsed = time.time() - stats["start"]
    print(f"\n[FIM] ok={stats['ok']:,} fail={stats['fail']:,} "
          f"({elapsed/60:.1f}min, taxa={stats['ok']/elapsed:.1f}/s)")


if __name__ == "__main__":
    main()
