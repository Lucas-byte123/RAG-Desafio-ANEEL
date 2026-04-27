"""
create_vectors_table.py — cria tabela CHUNK_VECTORS (1024-dim) + índices.

VECTOR(1024, FLOAT32) compatível com Cohere Embed Multilingual v3.
Índice HNSW pra busca rápida + Text Index pra BM25 (hybrid search).

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/create_vectors_table.py
"""

import os
import sys
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"

DSN = "aneelrag_medium"
USER = "ADMIN"


CREATE_TABLE_SQL = """
CREATE TABLE chunk_vectors (
    chunk_id     VARCHAR2(60) PRIMARY KEY,
    embedding    VECTOR(1024, FLOAT32),
    model_id     VARCHAR2(150),
    embedded_at  VARCHAR2(40)
)
"""


def main():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    conn = oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )
    cur = conn.cursor()

    print("[INFO] Recriando tabela chunk_vectors...")
    try:
        cur.execute("DROP TABLE chunk_vectors")
        print("  - tabela anterior removida")
    except oracledb.DatabaseError as e:
        if "ORA-00942" in str(e):
            print("  - nenhuma tabela anterior")
        else:
            raise

    cur.execute(CREATE_TABLE_SQL)
    print("  - tabela criada com VECTOR(1024, FLOAT32)")

    conn.commit()
    conn.close()
    print("\n[OK] Tabela chunk_vectors pronta.")
    print("\nNOTA: Índices (HNSW vector + Text BM25) são criados DEPOIS do embedding em massa,")
    print("      via scripts/build_indexes.py — é mais eficiente indexar com dados já carregados.")


if __name__ == "__main__":
    main()
