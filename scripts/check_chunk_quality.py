"""Verifica se fix dos chunks gigantes funcionou + análise de qualidade."""

import os
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

print("="*72)
print("  ANÁLISE DOS CHUNKS APÓS RODADA 2 (com fixes)")
print("="*72)

cur.execute("""
    SELECT chunk_level,
           COUNT(*),
           MIN(num_chars), AVG(num_chars), MAX(num_chars),
           SUM(CASE WHEN num_chars < 50 THEN 1 ELSE 0 END) AS tiny,
           SUM(CASE WHEN num_chars > 2000 THEN 1 ELSE 0 END) AS huge,
           SUM(CASE WHEN num_chars > 4000 THEN 1 ELSE 0 END) AS gigante
    FROM chunks GROUP BY chunk_level ORDER BY chunk_level
""")
print("\n[Distribuição de tamanhos]")
for lvl, n, mn, av, mx, tiny, huge, gigante in cur.fetchall():
    label = "parent" if lvl == 0 else "child "
    print(f"  L{lvl} ({label}): n={n:,} chars min={mn} avg={av:.0f} max={mx}")
    print(f"           tiny<50={tiny}  huge>2000={huge}  gigante>4000={gigante}")

print("\n[PDF antes problemático: NT 222/2021 com 625 chunks]")
cur.execute("""
    SELECT chunk_level, COUNT(*), MAX(num_chars), AVG(num_chars)
    FROM chunks
    WHERE pdf_id LIKE '%notatecnicano2222021sgtaneel%'
    GROUP BY chunk_level
""")
for lvl, n, mx, avg in cur.fetchall():
    print(f"  L{lvl}: count={n}  max_chars={mx}  avg_chars={avg:.0f}")

print("\n[Top 5 PDFs com MAIS chunks (poderia ter monstros)]")
cur.execute("""
    SELECT pdf_id, COUNT(*), MAX(num_chars)
    FROM chunks
    GROUP BY pdf_id
    ORDER BY COUNT(*) DESC
    FETCH FIRST 5 ROWS ONLY
""")
for pid, n, mx in cur.fetchall():
    print(f"  {pid[:60]:60s} chunks={n:>4,}  max={mx:,}")

print("\n[Top 5 PDFs com chunks de char MAIORES]")
cur.execute("""
    SELECT pdf_id, MAX(num_chars), COUNT(*)
    FROM chunks
    GROUP BY pdf_id
    ORDER BY MAX(num_chars) DESC
    FETCH FIRST 5 ROWS ONLY
""")
for pid, mx, n in cur.fetchall():
    print(f"  {pid[:60]:60s} max={mx:>6,}  chunks={n}")

print("\n[Comparação tamanhos médios por tipo]")
cur.execute("""
    SELECT chunk_type,
           COUNT(*),
           ROUND(AVG(num_chars)) AS avg_chars,
           MAX(num_chars) AS max_chars
    FROM chunks GROUP BY chunk_type ORDER BY 2 DESC
""")
for t, n, avg, mx in cur.fetchall():
    print(f"  {t:18s} n={n:>7,}  avg={avg:>5}  max={mx:>6,}")

conn.close()
