"""
build_manifest.py

Lê os 3 JSONs originais da ANEEL (biblioteca_aneel_*_metadados.json) e produz
o manifesto-mãe do projeto RAG: uma tabela parquet com 1 linha por PDF
contendo todo o estado e metadados necessários ao pipeline (download → extract
→ chunk → embed → index).

Uso:
    python build_manifest.py

Saída:
    ../manifest/manifest.parquet     (principal — sobe pro Object Storage e seedeia DB)
    ../manifest/manifest_sample.csv  (1000 linhas para inspeção humana)
    ../manifest/report.txt           (relatório de sanity check)

Dependências:
    pip install pandas pyarrow
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
JSON_FILES = [
    ROOT / "biblioteca_aneel_gov_br_legislacao_2016_metadados.json",
    ROOT / "biblioteca_aneel_gov_br_legislacao_2021_metadados.json",
    ROOT / "biblioteca_aneel_gov_br_legislacao_2022_metadados.json",
]
OUT_DIR = ROOT / "manifest"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_filename(raw: str) -> str:
    """URL-decode → strip acentos → lowercase → safe chars."""
    s = unquote(raw)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def slugify(text: str, max_len: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "", s.lower())
    return s[:max_len] or "tipo"


def url_hash(url: str, length: int = 8) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:length]


# Normalização do "tipo" (campo bruto do scraper tem 736 variações).
# Categorias canônicas usadas como filtro estruturado no retrieval.
TIPO_PATTERNS = [
    ("DECISAO_JUDICIAL", re.compile(r"decis[aã]o\s*judicial", re.I)),
    ("DECISAO",          re.compile(r"^decis[aã]o", re.I)),
    ("VOTO_VISTA",       re.compile(r"voto[\s\-_]+vista", re.I)),
    ("VOTO_CONDUTOR",    re.compile(r"voto\s*condutor", re.I)),
    ("VOTO_SEPARADO",    re.compile(r"voto\s+em\s+separado", re.I)),
    ("VOTO",             re.compile(r"^voto\b", re.I)),
    ("NOTA_TECNICA",     re.compile(r"nota\s*t[ée]cnica", re.I)),
    ("MEMORIA_CALCULO",  re.compile(r"mem[oó]ria\s*de\s*c[aá]lculo", re.I)),
    ("BASE_DADOS",       re.compile(r"base\s*de\s*dados", re.I)),
    ("EXPOSICAO_MOTIVOS",re.compile(r"exposi[cç][aã]o\s*de\s*motivos", re.I)),
    ("ANEXO",            re.compile(r"^anexo", re.I)),
    ("TEXTO_INTEGRAL",   re.compile(r"texto\s*integ", re.I)),
    ("TEXTO_INTEGRAL",   re.compile(r"^texto$", re.I)),
    ("TEXTO_INTEGRAL",   re.compile(r"^pdf$", re.I)),
]


def canonicalize_tipo(tipo_raw: str | None) -> str:
    """Reduz 736 variações brutas a ~12 categorias canônicas pra filtro estruturado."""
    if not tipo_raw:
        return "OUTROS"
    s = tipo_raw.strip()
    for label, pattern in TIPO_PATTERNS:
        if pattern.search(s):
            return label
    return "OUTROS"


def strip_label(value: str | None) -> str | None:
    """Remove prefixos tipo 'Esfera:', 'Situação:', 'Assunto:' que vêm grudados nos JSONs."""
    if value is None:
        return None
    return re.sub(r"^[A-Za-zÀ-ÿ ]+:\s*", "", value).strip() or None


def extract_year(date_str: str) -> int | None:
    try:
        return int(date_str[:4])
    except (TypeError, ValueError):
        return None


def iter_records():
    for path in JSON_FILES:
        if not path.exists():
            print(f"[WARN] arquivo não encontrado: {path}", file=sys.stderr)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for date_str, payload in data.items():
            for reg in payload.get("registros", []):
                yield date_str, reg


def build_rows():
    rows = []
    seen_url = set()
    duplicates_by_url = 0

    for date_str, reg in iter_records():
        item_num_raw = reg.get("numeracaoItem", "").strip().rstrip(".")
        titulo = reg.get("titulo")
        autor = reg.get("autor")
        assunto = strip_label(reg.get("assunto"))
        situacao = strip_label(reg.get("situacao"))
        esfera = strip_label(reg.get("esfera"))
        assinatura = strip_label(reg.get("assinatura"))
        publicacao = strip_label(reg.get("publicacao"))
        ementa = reg.get("ementa")  # pode ser None
        material = reg.get("material")

        for pdf in reg.get("pdfs", []):
            url = pdf.get("url", "").strip()
            if not url:
                continue
            if url in seen_url:
                duplicates_by_url += 1
                continue
            seen_url.add(url)

            arquivo_orig = pdf.get("arquivo") or Path(urlparse(url).path).name
            arquivo_norm = normalize_filename(arquivo_orig)
            tipo_raw = (pdf.get("tipo") or "").rstrip(":").strip()
            tipo_slug = slugify(tipo_raw)
            ano = extract_year(date_str)
            uh = url_hash(url)

            pdf_id = f"{date_str}_{item_num_raw or '0'}_{tipo_slug}_{uh}"
            bucket_path = f"raw/{ano or 'unknown'}/{arquivo_norm}"

            rows.append({
                # Identificação
                "pdf_id": pdf_id,
                "url": url,
                "arquivo_original": arquivo_orig,
                "arquivo_norm": arquivo_norm,
                "bucket_path": bucket_path,
                "url_hash": uh,
                # Metadados do registro
                "ano": ano,
                "data_publicacao": date_str,
                "numeracao_item": item_num_raw,
                "tipo": tipo_raw,
                "tipo_canonico": canonicalize_tipo(tipo_raw),
                "registro_titulo": titulo,
                "registro_autor": autor,
                "registro_material": material,
                "registro_esfera": esfera,
                "registro_situacao": situacao,
                "registro_assinatura": assinatura,
                "registro_publicacao": publicacao,
                "registro_assunto": assunto,
                "registro_ementa": ementa,
                # Estado do pipeline (preenchido pelo downloader / extrator / embedder)
                "status_download": "pending",
                "http_status": None,
                "bytes": None,
                "sha256": None,
                "pdf_pages": None,
                "attempts": 0,
                "last_error": None,
                "downloaded_at": None,
                "status_extract": "pending",
                "status_embed": "pending",
                "status_index": "pending",
                # Texto reduzido pra retrieval da camada 1 (ementa enriquecida)
                "retrieval_text_l1": _build_retrieval_text(titulo, assunto, ementa, autor),
                "manifest_built_at": datetime.now(timezone.utc).isoformat(),
            })

    return rows, duplicates_by_url


def _build_retrieval_text(titulo, assunto, ementa, autor) -> str:
    """Texto curto e auto-suficiente que será embeddado na 'camada 1' do RAG.
    Funciona mesmo sem o PDF — útil quando ementa está disponível.
    """
    parts = []
    if titulo:
        parts.append(titulo.strip())
    if autor:
        parts.append(f"Autor: {autor.strip()}")
    if assunto:
        parts.append(f"Assunto: {assunto.strip()}")
    if ementa:
        parts.append(ementa.strip().rstrip("Imprimir").strip())
    return " | ".join(parts).strip()


def detect_collisions(df: pd.DataFrame) -> pd.DataFrame:
    """Linhas onde (ano, arquivo_norm) repete — colisão real de bucket_path."""
    dup_mask = df.duplicated(subset=["ano", "arquivo_norm"], keep=False)
    return df[dup_mask].sort_values(["ano", "arquivo_norm"])


def resolve_collisions(df: pd.DataFrame) -> pd.DataFrame:
    """Anexa o url_hash ao nome quando há colisão, pra preservar todos os arquivos."""
    dup_mask = df.duplicated(subset=["ano", "arquivo_norm"], keep=False)
    if not dup_mask.any():
        return df

    def _rename(row):
        base = Path(row["arquivo_norm"])
        new_name = f"{base.stem}_{row['url_hash']}{base.suffix}"
        return new_name

    df.loc[dup_mask, "arquivo_norm"] = df.loc[dup_mask].apply(_rename, axis=1)
    df.loc[dup_mask, "bucket_path"] = df.loc[dup_mask].apply(
        lambda r: f"raw/{r['ano'] or 'unknown'}/{r['arquivo_norm']}", axis=1
    )
    return df


def write_report(df: pd.DataFrame, dup_url: int, collisions_before: int) -> str:
    by_year = df.groupby("ano", dropna=False).size().to_dict()
    by_tipo = df.groupby("tipo").size().sort_values(ascending=False).head(10).to_dict()
    by_canonico = df.groupby("tipo_canonico").size().sort_values(ascending=False).to_dict()
    null_ementa = int(df["registro_ementa"].isna().sum())
    avg_retrieval_len = int(df["retrieval_text_l1"].str.len().mean())

    lines = [
        "=" * 60,
        "MANIFESTO ANEEL — RELATÓRIO DE SANITY CHECK",
        f"Gerado em: {datetime.now(timezone.utc).isoformat()}",
        "=" * 60,
        "",
        f"Total de PDFs únicos no manifesto: {len(df):,}",
        f"URLs duplicadas (descartadas): {dup_url:,}",
        f"Colisões de nome resolvidas via hash: {collisions_before:,}",
        "",
        "Distribuição por ano:",
    ]
    for ano, n in sorted(by_year.items(), key=lambda kv: (kv[0] or 0)):
        lines.append(f"  {ano}: {n:,}")
    lines += [
        "",
        "Top 10 tipos brutos (sujos do scraper):",
    ]
    for tipo, n in by_tipo.items():
        lines.append(f"  {tipo or '(vazio)':40s} {n:,}")
    lines += [
        "",
        f"Tipos canônicos (categorias usadas no filtro estruturado): {len(by_canonico)}",
    ]
    for tipo, n in by_canonico.items():
        lines.append(f"  {tipo:24s} {n:,}")
    lines += [
        "",
        f"Registros com 'ementa' nula: {null_ementa:,} ({null_ementa / len(df):.1%})",
        f"Tamanho médio do texto de retrieval L1: {avg_retrieval_len} chars",
        "",
        "Status do pipeline (todos começam pending):",
        f"  status_download=pending: {(df['status_download'] == 'pending').sum():,}",
        "",
        "Próximos passos:",
        "  1. Subir manifest.parquet pro Object Storage (bucket aneel-rag, key 'manifest/manifest.parquet')",
        "  2. Seedear tabela 'manifest' no Autonomous DB com essas linhas",
        "  3. Iniciar downloader resiliente na VM A1",
    ]
    return "\n".join(lines)


def main():
    print(f"[INFO] Lendo JSONs em {ROOT}...")
    rows, dup_url = build_rows()
    if not rows:
        print("[ERRO] Nenhuma linha gerada — verifique os JSONs.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows)
    print(f"[INFO] {len(df):,} PDFs únicos por URL")
    print(f"[INFO] {dup_url:,} URLs duplicadas descartadas")

    collisions_df = detect_collisions(df)
    n_collisions = len(collisions_df)
    if n_collisions:
        print(f"[INFO] {n_collisions} colisões de (ano, arquivo_norm) detectadas — resolvendo via hash")
        df = resolve_collisions(df)
        # Re-confere
        if df.duplicated(subset=["bucket_path"]).any():
            print("[ERRO] Após resolução ainda há bucket_path duplicado!", file=sys.stderr)
            sys.exit(2)

    # Sanity final
    assert df["pdf_id"].is_unique, "pdf_id não é único"
    assert df["bucket_path"].is_unique, "bucket_path não é único"

    parquet_path = OUT_DIR / "manifest.parquet"
    csv_sample_path = OUT_DIR / "manifest_sample.csv"
    report_path = OUT_DIR / "report.txt"

    df.to_parquet(parquet_path, index=False, compression="zstd")
    df.head(1000).to_csv(csv_sample_path, index=False, encoding="utf-8-sig")
    report = write_report(df, dup_url, n_collisions)
    report_path.write_text(report, encoding="utf-8")

    print()
    print(report)
    print()
    print(f"[OK] manifest.parquet escrito em {parquet_path} ({parquet_path.stat().st_size / 1024:.1f} KB)")
    print(f"[OK] amostra CSV em {csv_sample_path}")
    print(f"[OK] relatório em {report_path}")


if __name__ == "__main__":
    main()
