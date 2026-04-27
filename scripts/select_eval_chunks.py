"""Seleciona chunks variados e ricos pra eu formular queries-gabarito."""
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

print("="*100)
print("  CATEGORIA 1: ARTIGOS FORMAIS COM NÚMERO ESPECÍFICO (factual numérica)")
print("="*100)
cur.execute("""
    SELECT chunk_id, breadcrumb, page_start, SUBSTR(text_raw, 1, 300)
    FROM chunks
    WHERE chunk_type = 'artigo'
      AND num_chars BETWEEN 200 AND 1000
      AND breadcrumb LIKE 'REN %'
      AND ano IN (2016, 2021, 2022)
    ORDER BY DBMS_RANDOM.VALUE
    FETCH FIRST 8 ROWS ONLY
""")
for r in cur.fetchall():
    cid, bc, pg, txt = r[0], r[1], r[2], r[3].read() if r[3] else ""
    print(f"\n[{cid}]  {bc}  pg.{pg}")
    print(f"  {txt[:280]}...")

print("\n\n" + "="*100)
print("  CATEGORIA 2: TEXTOS DEFINICIONAIS (conceitos/definições)")
print("="*100)
cur.execute("""
    SELECT chunk_id, breadcrumb, page_start, SUBSTR(text_raw, 1, 300)
    FROM chunks
    WHERE chunk_level = 1
      AND num_chars BETWEEN 250 AND 800
      AND (LOWER(text_raw) LIKE '%define%'
           OR LOWER(text_raw) LIKE '%considera-se%'
           OR LOWER(text_raw) LIKE '%entende-se%'
           OR LOWER(text_raw) LIKE 'i - %'
           OR LOWER(text_raw) LIKE '%é o conjunto%'
           OR LOWER(text_raw) LIKE '%significa%')
      AND ano IN (2016, 2021, 2022)
    ORDER BY DBMS_RANDOM.VALUE
    FETCH FIRST 6 ROWS ONLY
""")
for r in cur.fetchall():
    cid, bc, pg, txt = r[0], r[1], r[2], r[3].read() if r[3] else ""
    print(f"\n[{cid}]  {bc}  pg.{pg}")
    print(f"  {txt[:280]}...")

print("\n\n" + "="*100)
print("  CATEGORIA 3: NOTAS TÉCNICAS COM CONCLUSÕES (analítica)")
print("="*100)
cur.execute("""
    SELECT chunk_id, breadcrumb, page_start, SUBSTR(text_raw, 1, 300)
    FROM chunks
    WHERE chunk_level = 1
      AND tipo_canonico = 'NOTA_TECNICA'
      AND num_chars BETWEEN 300 AND 1000
      AND (LOWER(text_raw) LIKE '%conclui%'
           OR LOWER(text_raw) LIKE '%proposta%'
           OR LOWER(text_raw) LIKE '%recomenda%')
      AND ano IN (2021, 2022)
    ORDER BY DBMS_RANDOM.VALUE
    FETCH FIRST 4 ROWS ONLY
""")
for r in cur.fetchall():
    cid, bc, pg, txt = r[0], r[1], r[2], r[3].read() if r[3] else ""
    print(f"\n[{cid}]  {bc}  pg.{pg}")
    print(f"  {txt[:280]}...")

print("\n\n" + "="*100)
print("  CATEGORIA 4: TABELAS (numéricas)")
print("="*100)
cur.execute("""
    SELECT chunk_id, breadcrumb, page_start, SUBSTR(text_raw, 1, 400)
    FROM chunks
    WHERE chunk_type = 'tabela'
      AND num_chars BETWEEN 300 AND 1500
      AND ano IN (2021, 2022)
    ORDER BY DBMS_RANDOM.VALUE
    FETCH FIRST 4 ROWS ONLY
""")
for r in cur.fetchall():
    cid, bc, pg, txt = r[0], r[1], r[2], r[3].read() if r[3] else ""
    print(f"\n[{cid}]  {bc}  pg.{pg}")
    print(f"  {txt[:380]}...")

conn.close()
