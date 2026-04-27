import os
import oracledb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
conn = oracledb.connect(
    user='ADMIN', password=os.environ['DB_ADMIN_PASS'], dsn='aneelrag_medium',
    config_dir=str(ROOT / '.secrets' / 'wallet'),
    wallet_location=str(ROOT / '.secrets' / 'wallet'),
    wallet_password=(ROOT / '.secrets' / 'wallet.pass').read_text().strip(),
)
cur = conn.cursor()
cur.execute("SELECT status_download, COUNT(*) FROM manifest GROUP BY status_download ORDER BY 2 DESC")
for s, n in cur.fetchall():
    print(f"  {s:28s} {n:>7,}")
print("\nPor ano:")
cur.execute("SELECT ano, SUM(CASE WHEN status_download='success' THEN 1 ELSE 0 END), COUNT(*) FROM manifest GROUP BY ano ORDER BY ano")
for a, ok, tot in cur.fetchall():
    print(f"  {a}: {ok:,}/{tot:,} ({100*ok/tot:.1f}%)")
conn.close()
