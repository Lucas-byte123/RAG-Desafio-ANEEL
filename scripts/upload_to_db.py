"""
upload_to_db.py

Cria a tabela 'manifest' no Autonomous DB 26ai e popula com os 27.025 registros
do manifest.parquet local. Idempotente — pode rodar de novo (DROP + CREATE).

Pré-requisitos:
    - Wallet extraída em .secrets/wallet/
    - Senha da wallet em .secrets/wallet.pass
    - Senha do ADMIN do DB em env var DB_ADMIN_PASS

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/upload_to_db.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import oracledb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"
PARQUET = ROOT / "manifest" / "manifest.parquet"

DSN = "aneelrag_medium"
USER = "ADMIN"

CREATE_TABLE_SQL = """
CREATE TABLE manifest (
    pdf_id              VARCHAR2(200) PRIMARY KEY,
    url                 VARCHAR2(500) NOT NULL,
    arquivo_original    VARCHAR2(300),
    arquivo_norm        VARCHAR2(300) NOT NULL,
    bucket_path         VARCHAR2(500) NOT NULL,
    url_hash            VARCHAR2(20),
    ano                 NUMBER,
    data_publicacao     VARCHAR2(20),
    numeracao_item      VARCHAR2(20),
    tipo                VARCHAR2(500),
    tipo_canonico       VARCHAR2(50),
    registro_titulo     VARCHAR2(500),
    registro_autor      VARCHAR2(200),
    registro_material   VARCHAR2(100),
    registro_esfera     VARCHAR2(100),
    registro_situacao   VARCHAR2(200),
    registro_assinatura VARCHAR2(50),
    registro_publicacao VARCHAR2(50),
    registro_assunto    VARCHAR2(200),
    registro_ementa     CLOB,
    status_download     VARCHAR2(30) DEFAULT 'pending',
    http_status         NUMBER,
    file_bytes          NUMBER,
    sha256              VARCHAR2(64),
    pdf_pages           NUMBER,
    attempts            NUMBER DEFAULT 0,
    last_error          VARCHAR2(2000),
    downloaded_at       VARCHAR2(40),
    status_extract      VARCHAR2(30) DEFAULT 'pending',
    status_embed        VARCHAR2(30) DEFAULT 'pending',
    status_index        VARCHAR2(30) DEFAULT 'pending',
    retrieval_text_l1   VARCHAR2(4000),
    manifest_built_at   VARCHAR2(40)
)
"""

INSERT_SQL = """
INSERT INTO manifest (
    pdf_id, url, arquivo_original, arquivo_norm, bucket_path, url_hash,
    ano, data_publicacao, numeracao_item, tipo, tipo_canonico,
    registro_titulo, registro_autor, registro_material, registro_esfera,
    registro_situacao, registro_assinatura, registro_publicacao,
    registro_assunto, registro_ementa,
    status_download, http_status, file_bytes, sha256, pdf_pages,
    attempts, last_error, downloaded_at,
    status_extract, status_embed, status_index,
    retrieval_text_l1, manifest_built_at
) VALUES (
    :1, :2, :3, :4, :5, :6,
    :7, :8, :9, :10, :11,
    :12, :13, :14, :15,
    :16, :17, :18,
    :19, :20,
    :21, :22, :23, :24, :25,
    :26, :27, :28,
    :29, :30, :31,
    :32, :33
)
"""

# Ordem das colunas no parquet — tem que casar com a ordem dos :N do INSERT
PARQUET_COLS = [
    "pdf_id", "url", "arquivo_original", "arquivo_norm", "bucket_path", "url_hash",
    "ano", "data_publicacao", "numeracao_item", "tipo", "tipo_canonico",
    "registro_titulo", "registro_autor", "registro_material", "registro_esfera",
    "registro_situacao", "registro_assinatura", "registro_publicacao",
    "registro_assunto", "registro_ementa",
    "status_download", "http_status", "bytes", "sha256", "pdf_pages",
    "attempts", "last_error", "downloaded_at",
    "status_extract", "status_embed", "status_index",
    "retrieval_text_l1", "manifest_built_at",
]


def main():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS antes de rodar (env var).")
    if not WALLET_PASS_FILE.exists():
        sys.exit(f"ERRO: arquivo de senha da wallet não encontrado: {WALLET_PASS_FILE}")
    if not PARQUET.exists():
        sys.exit(f"ERRO: manifest.parquet não encontrado: {PARQUET}")

    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    print(f"[INFO] Lendo {PARQUET.name}...")
    df = pd.read_parquet(PARQUET)
    print(f"[INFO] {len(df):,} linhas, {len(df.columns)} colunas")

    # NaN/NaT -> None pra Oracle aceitar
    df = df.astype(object).where(pd.notnull(df), None)

    print(f"[INFO] Conectando em {DSN} (mTLS via wallet)...")
    conn = oracledb.connect(
        user=USER,
        password=pwd,
        dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )
    print(f"[OK] Conectado. Versão do servidor: {conn.version}")

    cur = conn.cursor()

    print("[INFO] Recriando tabela manifest (DROP + CREATE)...")
    try:
        cur.execute("DROP TABLE manifest")
        print("  - tabela anterior removida")
    except oracledb.DatabaseError as e:
        if "ORA-00942" in str(e):  # table does not exist
            print("  - nenhuma tabela anterior")
        else:
            raise

    cur.execute(CREATE_TABLE_SQL)
    print("  - tabela criada")

    for idx_sql in [
        "CREATE INDEX idx_manifest_status_dl ON manifest(status_download)",
        "CREATE INDEX idx_manifest_ano ON manifest(ano)",
        "CREATE INDEX idx_manifest_tipo_can ON manifest(tipo_canonico)",
    ]:
        cur.execute(idx_sql)
    print("  - 3 índices criados")

    rows = [tuple(r) for r in df[PARQUET_COLS].itertuples(index=False, name=None)]
    print(f"[INFO] Inserindo {len(rows):,} linhas em batches de 500...")

    t0 = time.time()
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        cur.executemany(INSERT_SQL, rows[i:i + BATCH])
        if (i // BATCH) % 10 == 0:
            print(f"  ...{i + BATCH:,}/{len(rows):,}", flush=True)
    conn.commit()
    elapsed = time.time() - t0

    cur.execute("SELECT COUNT(*) FROM manifest")
    total = cur.fetchone()[0]
    print(f"\n[OK] {total:,} linhas no DB ({elapsed:.1f}s, {total/elapsed:.0f} linhas/s)")

    print("\nDistribuição por ano:")
    cur.execute("SELECT ano, COUNT(*) FROM manifest GROUP BY ano ORDER BY ano")
    for ano, n in cur.fetchall():
        print(f"  {ano}: {n:,}")

    print("\nDistribuição por tipo canônico:")
    cur.execute("SELECT tipo_canonico, COUNT(*) FROM manifest GROUP BY tipo_canonico ORDER BY COUNT(*) DESC")
    for tipo, n in cur.fetchall():
        print(f"  {tipo:24s} {n:,}")

    print("\nStatus do download (deve ser 100% pending):")
    cur.execute("SELECT status_download, COUNT(*) FROM manifest GROUP BY status_download")
    for status, n in cur.fetchall():
        print(f"  {status}: {n:,}")

    conn.close()
    print("\n[OK] DB seedeado. Pronto pro downloader na VM.")


if __name__ == "__main__":
    main()
