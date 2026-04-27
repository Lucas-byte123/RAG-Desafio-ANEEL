"""ALTER TABLE chunks: chunk_id e parent_chunk_id pra VARCHAR2(220).
TRUNCATE chunks após ALTER pra reprocessar com fixes."""
import os, sys
from pathlib import Path
import oracledb

ROOT = Path(__file__).resolve().parent.parent
conn = oracledb.connect(
    user='ADMIN', password=os.environ['DB_ADMIN_PASS'], dsn='aneelrag_medium',
    config_dir=str(ROOT / '.secrets/wallet'),
    wallet_location=str(ROOT / '.secrets/wallet'),
    wallet_password=(ROOT / '.secrets/wallet.pass').read_text().strip(),
)
cur = conn.cursor()

print("[1/3] TRUNCATE TABLE chunks (mais rápido que DROP)...")
cur.execute("TRUNCATE TABLE chunks")

print("[2/3] ALTER chunk_id, parent_chunk_id VARCHAR2(220)...")
cur.execute("ALTER TABLE chunks MODIFY (chunk_id VARCHAR2(220))")
cur.execute("ALTER TABLE chunks MODIFY (parent_chunk_id VARCHAR2(220))")

print("[3/3] Verificando...")
cur.execute("""
    SELECT column_name, data_type, data_length
    FROM user_tab_columns
    WHERE table_name = 'CHUNKS' AND column_name IN ('CHUNK_ID', 'PARENT_CHUNK_ID')
""")
for col, dt, dl in cur.fetchall():
    print(f"  {col}: {dt}({dl})")

conn.commit()
conn.close()
print("\n[OK] tabela limpa, colunas estendidas. Pronto pra re-rodar chunker em massa.")
