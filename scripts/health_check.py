"""Health check completo do pipeline."""

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

def section(t):
    print("\n" + "=" * 72 + f"\n  {t}\n" + "=" * 72)


# 1. Volume
section("1. VOLUMES")
cur.execute("SELECT COUNT(*) FROM manifest")
n_m, = cur.fetchone()
cur.execute("SELECT COUNT(*) FROM extractions WHERE last_error IS NULL")
n_e, = cur.fetchone()
cur.execute("SELECT COUNT(*), COUNT(DISTINCT pdf_id) FROM chunks")
n_c, n_c_pdfs = cur.fetchone()
cur.execute("SELECT COUNT(*) FROM chunk_vectors")
n_v, = cur.fetchone()
print(f"  manifest:       {n_m:>7,}")
print(f"  extractions ok: {n_e:>7,}  (cobertura {100*n_e/n_m:.1f}%)")
print(f"  chunks total:   {n_c:>7,}  ({n_c_pdfs:,} PDFs distintos)")
print(f"  vectors:        {n_v:>7,}")

# 2. Distribuição de chunks/pdf
section("2. CHUNKS POR PDF (saúde do chunker)")
cur.execute("""
    SELECT MIN(c), AVG(c), MAX(c), COUNT(*) FROM (
        SELECT pdf_id, COUNT(*) AS c FROM chunks GROUP BY pdf_id
    )
""")
r = cur.fetchone()
print(f"  chunks/pdf  min={r[0]}  avg={r[1]:.1f}  max={r[2]}  pdfs={r[3]:,}")

cur.execute("SELECT COUNT(*) FROM chunks WHERE chunk_level=1")
n_child, = cur.fetchone()
cur.execute("SELECT COUNT(*) FROM chunks WHERE chunk_level=0")
n_par, = cur.fetchone()
print(f"  parents={n_par:,}  children={n_child:,}")

# 3. Tamanhos
section("3. TAMANHOS DOS CHUNKS")
cur.execute("""
    SELECT chunk_level,
           MIN(num_chars), AVG(num_chars), MAX(num_chars),
           SUM(CASE WHEN num_chars < 50 THEN 1 ELSE 0 END) AS tiny,
           SUM(CASE WHEN num_chars > 4500 THEN 1 ELSE 0 END) AS huge
    FROM chunks GROUP BY chunk_level ORDER BY chunk_level
""")
for lvl, mn, av, mx, tiny, huge in cur.fetchall():
    label = "parent" if lvl == 0 else "child"
    print(f"  L{lvl} ({label}): chars min={mn} avg={av:.0f} max={mx}  tiny<50={tiny}  huge>4500={huge}")

# 4. Tipos de chunk
section("4. TIPOS DE CHUNK")
cur.execute("SELECT chunk_type, COUNT(*) FROM chunks GROUP BY chunk_type ORDER BY 2 DESC")
for t, n in cur.fetchall():
    print(f"  {t:20s} {n:>7,}")

# 5. Breadcrumbs
section("5. SAÚDE DOS BREADCRUMBS")
cur.execute("""
    SELECT
        SUM(CASE WHEN breadcrumb IS NULL OR breadcrumb = '' THEN 1 ELSE 0 END) AS sem_bc,
        SUM(CASE WHEN INSTR(breadcrumb, ' > ') > 0 THEN 1 ELSE 0 END) AS rico,
        COUNT(*) AS total
    FROM chunks
""")
sem, rico, tot = cur.fetchone()
print(f"  sem breadcrumb: {sem:,} ({100*sem/tot:.1f}%)")
print(f"  com hierarquia (' > '): {rico:,} ({100*rico/tot:.1f}%)")
print(f"  total: {tot:,}")

cur.execute("""
    SELECT breadcrumb, COUNT(*) FROM chunks
    WHERE breadcrumb IS NOT NULL AND breadcrumb != ''
    GROUP BY breadcrumb ORDER BY 2 DESC FETCH FIRST 10 ROWS ONLY
""")
print("\n  Top 10 breadcrumbs:")
for bc, n in cur.fetchall():
    print(f"    {n:>5,}  {(bc or '')[:80]}")

# 6. Distribuição por ano/tipo
section("6. CHUNKS POR ANO E TIPO")
cur.execute("""
    SELECT ano, COUNT(*) FROM chunks GROUP BY ano ORDER BY ano
""")
for a, n in cur.fetchall():
    print(f"  {a}: {n:,}")

print()
cur.execute("""
    SELECT tipo_canonico, COUNT(*) FROM chunks
    GROUP BY tipo_canonico ORDER BY 2 DESC FETCH FIRST 8 ROWS ONLY
""")
for t, n in cur.fetchall():
    print(f"  {t:25s} {n:>7,}")

# 7. Extraction quality
section("7. QUALIDADE EXTRAÇÃO")
cur.execute("""
    SELECT
        ROUND(MIN(quality_score),2), ROUND(AVG(quality_score),3), ROUND(MAX(quality_score),2),
        SUM(CASE WHEN quality_score < 0.7 THEN 1 ELSE 0 END) AS bad,
        SUM(CASE WHEN needs_docling=1 THEN 1 ELSE 0 END) AS doc
    FROM extractions WHERE last_error IS NULL
""")
mn, av, mx, bad, doc = cur.fetchone()
print(f"  quality: min={mn} avg={av} max={mx}")
print(f"  quality < 0.7 (suspeitas): {bad}")
print(f"  needs_docling: {doc}")

# 8. Anomalias
section("8. ANOMALIAS A INVESTIGAR")
cur.execute("""
    SELECT COUNT(*) FROM extractions
    WHERE num_pages > 0 AND num_blocks = 0 AND last_error IS NULL
""")
zero_blocks, = cur.fetchone()
print(f"  PDFs com 0 blocks (apenas tabelas?): {zero_blocks}")

cur.execute("""
    SELECT COUNT(*) FROM extractions
    WHERE num_articles = 0 AND num_blocks > 30 AND last_error IS NULL
""")
no_arts, = cur.fetchone()
print(f"  PDFs grandes sem nenhum artigo (despachos típicos): {no_arts}")

cur.execute("""
    SELECT COUNT(*) FROM chunks WHERE LENGTH(text_raw) < 30
""")
tiny_chunks, = cur.fetchone()
print(f"  chunks com < 30 chars: {tiny_chunks}")

# 9. PDFs sem chunks
section("9. PDFs EXTRAÍDOS MAS SEM CHUNKS")
cur.execute("""
    SELECT COUNT(*) FROM extractions e
    WHERE e.extracted_json IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.pdf_id = e.pdf_id)
""")
no_chunks, = cur.fetchone()
print(f"  PDFs extraídos sem chunks (próxima rodada do chunker pega): {no_chunks:,}")

conn.close()
print("\n" + "=" * 72)
