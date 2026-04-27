"""
create_extractions_table.py — cria tabela EXTRACTIONS no Autonomous DB.

Design: tabela separada de MANIFEST por 3 razões:
  1. Permite re-extração (DELETE + INSERT) sem mexer no manifest
  2. Permite múltiplas estratégias de extração no futuro (pymupdf, docling) via coluna 'extractor'
  3. CLOB grande não polui SELECT * do manifest

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/create_extractions_table.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"

DSN = "aneelrag_medium"
USER = "ADMIN"

CREATE_EXTRACTIONS_SQL = """
CREATE TABLE extractions (
    pdf_id              VARCHAR2(200) PRIMARY KEY,
    extractor           VARCHAR2(30)  NOT NULL,
    extractor_version   VARCHAR2(30),
    extracted_at        VARCHAR2(40),

    num_pages           NUMBER,
    num_blocks          NUMBER,
    num_articles        NUMBER,
    num_tables          NUMBER,
    num_suspicious_tables NUMBER,
    chars_total         NUMBER,
    chars_per_page      NUMBER,

    quality_score       NUMBER(5, 3),
    is_native_text      NUMBER(1),
    needs_ocr           NUMBER(1),
    needs_docling       NUMBER(1),

    header_pattern      VARCHAR2(500),
    footer_pattern      VARCHAR2(500),

    extracted_json      CLOB,
    extracted_markdown  CLOB,

    last_error          VARCHAR2(2000),
    CONSTRAINT fk_extr_manifest FOREIGN KEY (pdf_id) REFERENCES manifest(pdf_id)
)
"""

CREATE_INDEXES = [
    "CREATE INDEX idx_extr_extractor ON extractions(extractor)",
    "CREATE INDEX idx_extr_quality ON extractions(quality_score)",
    "CREATE INDEX idx_extr_needs_docling ON extractions(needs_docling)",
]


def main():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS antes de rodar.")
    if not WALLET_PASS_FILE.exists():
        sys.exit(f"ERRO: wallet.pass não encontrado em {WALLET_PASS_FILE}")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    print(f"[INFO] Conectando em {DSN}...")
    conn = oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )
    cur = conn.cursor()

    print("[INFO] Recriando tabela extractions (DROP + CREATE)...")
    try:
        cur.execute("DROP TABLE extractions")
        print("  - tabela anterior removida")
    except oracledb.DatabaseError as e:
        if "ORA-00942" in str(e):
            print("  - nenhuma tabela anterior")
        else:
            raise

    cur.execute(CREATE_EXTRACTIONS_SQL)
    print("  - tabela criada")

    for idx_sql in CREATE_INDEXES:
        cur.execute(idx_sql)
    print(f"  - {len(CREATE_INDEXES)} índices criados")

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM extractions")
    (n,) = cur.fetchone()
    print(f"\n[OK] Tabela extractions pronta ({n:,} linhas).")

    conn.close()


if __name__ == "__main__":
    main()
