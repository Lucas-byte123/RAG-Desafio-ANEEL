"""
build_indexes.py — cria índices no DB após chunks+vectors populados.

Índices:
  1. HNSW Vector Index sobre chunk_vectors.embedding (Oracle 23ai)
  2. Oracle Text Index sobre chunks.text_embed (BM25 — pra hybrid search)

Rodar UMA VEZ após embedding em massa concluído.
HNSW é custoso: construção ~1-2 min por milhão de vetores. BM25 Text ~1 min.

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/build_indexes.py
    python scripts/build_indexes.py --drop   # rebuild do zero
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"


VECTOR_INDEX_SQL = """
CREATE VECTOR INDEX idx_chunk_vec_hnsw
ON chunk_vectors (embedding)
ORGANIZATION INMEMORY NEIGHBOR GRAPH
DISTANCE COSINE
WITH TARGET ACCURACY 95
PARAMETERS (TYPE HNSW, NEIGHBORS 32, EFCONSTRUCTION 300)
"""

TEXT_INDEX_SQL = """
CREATE INDEX idx_chunks_fts
ON chunks (text_embed)
INDEXTYPE IS CTXSYS.CONTEXT
PARAMETERS ('LEXER PORT_LEXER SYNC (ON COMMIT)')
"""

PORT_LEXER_SQL = """
BEGIN
  CTX_DDL.CREATE_PREFERENCE('PORT_LEXER', 'BASIC_LEXER');
  CTX_DDL.SET_ATTRIBUTE('PORT_LEXER', 'BASE_LETTER', 'YES');
  CTX_DDL.SET_ATTRIBUTE('PORT_LEXER', 'MIXED_CASE', 'NO');
END;
"""


def connect():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS.")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()
    return oracledb.connect(
        user="ADMIN", password=pwd, dsn="aneelrag_medium",
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )


def try_drop(cur, obj, kind):
    try:
        cur.execute(f"DROP {kind} {obj}")
        print(f"  - {kind} {obj} removido")
    except oracledb.DatabaseError as e:
        msg = str(e)
        if "ORA-00942" in msg or "ORA-01418" in msg or "does not exist" in msg.lower():
            pass
        else:
            print(f"  ! erro ao drop {obj}: {msg[:100]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true", help="drop e recria todos os índices")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM chunk_vectors")
    (n_vec,) = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM chunks WHERE text_embed IS NOT NULL")
    (n_text,) = cur.fetchone()
    print(f"[INFO] chunk_vectors: {n_vec:,}   chunks c/ text_embed: {n_text:,}")

    if n_vec < 10:
        print("[ABORTA] Menos de 10 vetores — rode embed_index.py primeiro.")
        conn.close()
        return

    if args.drop:
        print("\n[INFO] Dropping índices existentes...")
        try_drop(cur, "idx_chunk_vec_hnsw", "INDEX")
        try_drop(cur, "idx_chunks_fts", "INDEX")

    print(f"\n[1/2] Criando HNSW vector index...")
    t0 = time.time()
    try:
        cur.execute(VECTOR_INDEX_SQL)
        print(f"  OK ({time.time()-t0:.1f}s)")
    except oracledb.DatabaseError as e:
        msg = str(e)
        if "ORA-00955" in msg:
            print("  [já existe]")
        else:
            print(f"  ERRO: {msg[:200]}")
            raise

    print(f"\n[2/2] Criando Text Index (BM25) pra hybrid search...")
    t0 = time.time()
    try:
        cur.execute(PORT_LEXER_SQL)
    except oracledb.DatabaseError as e:
        if "DRG-10507" not in str(e):
            print(f"  ! preference lexer: {e}")
    try:
        cur.execute(TEXT_INDEX_SQL)
        print(f"  OK ({time.time()-t0:.1f}s)")
    except oracledb.DatabaseError as e:
        msg = str(e)
        if "ORA-00955" in msg:
            print("  [já existe]")
        else:
            print(f"  ERRO: {msg[:200]}")
            raise

    conn.commit()

    print("\n[VALIDAÇÃO] sanity query vector...")
    cur.execute("""
        SELECT chunk_id FROM chunk_vectors FETCH FIRST 1 ROWS ONLY
    """)
    print(f"  {cur.fetchone()}")

    conn.close()
    print("\n[OK] Índices prontos.")


if __name__ == "__main__":
    main()
