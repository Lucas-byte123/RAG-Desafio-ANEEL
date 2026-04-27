"""
create_chunks_table.py — cria tabela CHUNKS no Autonomous DB.

Design Parent-Child retrieval:
  - chunk_level: 0 = PARENT (grande, dado ao LLM), 1 = CHILD (pequeno, usado pra busca)
  - parent_id: FK pra o próprio chunk parent (NULL se é parent)
  - text_embed: texto COM breadcrumb prefixado (vai pro embedding)
  - text_raw: texto SEM breadcrumb (vai pro LLM como contexto)
  - ancestrais: JSON com capítulo/seção/artigo/parágrafo

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/create_chunks_table.py
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

CREATE_CHUNKS_SQL = """
CREATE TABLE chunks (
    chunk_id           VARCHAR2(60) PRIMARY KEY,
    pdf_id             VARCHAR2(200) NOT NULL,
    parent_chunk_id    VARCHAR2(60),
    chunk_level        NUMBER(1) NOT NULL,
    ordem_doc          NUMBER NOT NULL,

    chunk_type         VARCHAR2(20) NOT NULL,
    page_start         NUMBER,
    page_end           NUMBER,

    tipo_canonico      VARCHAR2(50),
    ano                NUMBER,
    numeracao_item     VARCHAR2(20),

    breadcrumb         VARCHAR2(500),
    ancestrais_json    VARCHAR2(1000),

    text_embed         VARCHAR2(4000),
    text_raw           CLOB,

    num_chars          NUMBER,
    num_tokens_est     NUMBER,

    has_table          NUMBER(1) DEFAULT 0,
    created_at         VARCHAR2(40)
)
"""

CREATE_INDEXES = [
    "CREATE INDEX idx_chunks_pdf ON chunks(pdf_id)",
    "CREATE INDEX idx_chunks_parent ON chunks(parent_chunk_id)",
    "CREATE INDEX idx_chunks_level ON chunks(chunk_level)",
    "CREATE INDEX idx_chunks_tipo ON chunks(tipo_canonico)",
    "CREATE INDEX idx_chunks_ano ON chunks(ano)",
]


def main():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS.")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    print(f"[INFO] Conectando em {DSN}...")
    conn = oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )
    cur = conn.cursor()

    print("[INFO] Recriando tabela chunks (DROP + CREATE)...")
    try:
        cur.execute("DROP TABLE chunks")
        print("  - tabela anterior removida")
    except oracledb.DatabaseError as e:
        if "ORA-00942" in str(e):
            print("  - nenhuma tabela anterior")
        else:
            raise

    cur.execute(CREATE_CHUNKS_SQL)
    print("  - tabela criada")

    for idx_sql in CREATE_INDEXES:
        cur.execute(idx_sql)
    print(f"  - {len(CREATE_INDEXES)} índices criados")

    conn.commit()
    conn.close()
    print("\n[OK] Tabela chunks pronta.")


if __name__ == "__main__":
    main()
