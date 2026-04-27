"""
review_pipeline.py — revisão completa do estado do pipeline.

Checa todos os componentes do RAG:
  1. Download (manifest status distribution)
  2. Extração (coerência extractions vs manifest, quality scores)
  3. Chunking (chunks por PDF, distribuição parent/child)
  4. Embedding (coverage vs chunks)
  5. Integridade referencial
  6. Sanity de conteúdo (texts vazios, vetores degenerados)
  7. Multi-query search test (3 queries de tipos diferentes)
"""

from __future__ import annotations

import array
import os
import sys
from pathlib import Path

import oci
import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"


def hr(title):
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def connect():
    return oracledb.connect(
        user="ADMIN", password=os.environ["DB_ADMIN_PASS"],
        dsn="aneelrag_medium",
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS_FILE.read_text().strip(),
    )


def get_embed_model_id(cfg, tenancy):
    cfg["region"] = "sa-saopaulo-1"
    mgmt = oci.generative_ai.GenerativeAiClient(cfg)
    resp = mgmt.list_models(compartment_id=tenancy)
    for m in resp.data.items:
        if (m.display_name or "").lower() == "cohere.embed-multilingual-v3.0" \
                and m.lifecycle_state == "ACTIVE":
            return m.id
    return None


def review_download(cur):
    hr("1. DOWNLOAD")
    cur.execute("SELECT status_download, COUNT(*) FROM manifest GROUP BY status_download ORDER BY 2 DESC")
    total = 0
    for s, n in cur.fetchall():
        print(f"  {s:28s} {n:>7,}")
        total += n
    print(f"  {'TOTAL':28s} {total:>7,}")
    cur.execute("""
        SELECT ano,
               SUM(CASE WHEN status_download='success' THEN 1 ELSE 0 END) AS ok,
               COUNT(*) AS tot
        FROM manifest GROUP BY ano ORDER BY ano
    """)
    print("\n  Por ano:")
    for a, ok, tot in cur.fetchall():
        pct = 100 * ok / tot
        print(f"    {a}: {ok:,}/{tot:,} ({pct:.1f}%)")


def review_extractions(cur):
    hr("2. EXTRAÇÕES")
    cur.execute("SELECT COUNT(*) FROM extractions WHERE last_error IS NULL")
    (n_ok,) = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM extractions WHERE last_error IS NOT NULL")
    (n_fail,) = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM manifest WHERE status_download='success'")
    (n_dl,) = cur.fetchone()

    print(f"  extractions ok:          {n_ok:,}")
    print(f"  extractions com erro:    {n_fail:,}")
    print(f"  manifest success (alvo): {n_dl:,}")
    print(f"  cobertura:               {100*n_ok/n_dl:.1f}%" if n_dl else "  (nenhum baixado ainda)")

    if n_ok:
        cur.execute("""
            SELECT
              MIN(quality_score), AVG(quality_score), MAX(quality_score),
              SUM(CASE WHEN quality_score < 0.5 THEN 1 ELSE 0 END),
              SUM(CASE WHEN needs_docling=1 THEN 1 ELSE 0 END),
              SUM(CASE WHEN needs_ocr=1 THEN 1 ELSE 0 END)
            FROM extractions WHERE last_error IS NULL
        """)
        r = cur.fetchone()
        qmin, qavg, qmax, n_bad, n_doc, n_ocr = r
        print(f"\n  quality: min={qmin:.2f} avg={qavg:.2f} max={qmax:.2f}")
        print(f"  quality < 0.5 (ruim): {n_bad:,} ({100*n_bad/n_ok:.1f}%)")
        print(f"  needs_docling:        {n_doc:,} ({100*n_doc/n_ok:.1f}%)")
        print(f"  needs_ocr:            {n_ocr:,} ({100*n_ocr/n_ok:.1f}%)")

        cur.execute("""
            SELECT SUM(num_blocks), AVG(num_blocks), SUM(num_articles), AVG(num_articles),
                   SUM(num_tables)
            FROM extractions WHERE last_error IS NULL
        """)
        r = cur.fetchone()
        sb, ab, sa, aa, st = r
        print(f"\n  blocks: total={sb:,} média/pdf={ab:.1f}")
        print(f"  artigos: total={sa:,} média/pdf={aa:.1f}")
        print(f"  tabelas: total={st:,}")


def review_chunks(cur):
    hr("3. CHUNKS")
    cur.execute("SELECT COUNT(*) FROM chunks")
    (n_c,) = cur.fetchone()
    cur.execute("SELECT COUNT(DISTINCT pdf_id) FROM chunks")
    (n_pdf,) = cur.fetchone()
    print(f"  chunks totais:     {n_c:,}")
    print(f"  PDFs com chunks:   {n_pdf:,}")
    if not n_c:
        return
    cur.execute("""
        SELECT chunk_level, COUNT(*), AVG(num_chars), MIN(num_chars), MAX(num_chars)
        FROM chunks GROUP BY chunk_level ORDER BY chunk_level
    """)
    print("  por nível:")
    for lvl, n, avg, mn, mx in cur.fetchall():
        label = "parent" if lvl == 0 else "child "
        print(f"    L{lvl} ({label}): {n:,}  chars avg={avg:.0f} min={mn} max={mx}")

    cur.execute("SELECT COUNT(*) FROM chunks WHERE text_embed IS NULL AND chunk_level=1")
    (n_no_embed,) = cur.fetchone()
    print(f"\n  children sem text_embed: {n_no_embed}")

    cur.execute("SELECT COUNT(*) FROM chunks WHERE LENGTH(text_raw) < 20")
    (n_tiny,) = cur.fetchone()
    print(f"  chunks com text_raw < 20 chars: {n_tiny}")

    cur.execute("""
        SELECT chunk_type, COUNT(*) FROM chunks GROUP BY chunk_type ORDER BY 2 DESC
    """)
    print("\n  por tipo:")
    for t, n in cur.fetchall():
        print(f"    {t:20s} {n:,}")


def review_vectors(cur):
    hr("4. VETORES")
    cur.execute("SELECT COUNT(*) FROM chunk_vectors")
    (n_vec,) = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM chunks WHERE chunk_level=1 AND text_embed IS NOT NULL")
    (n_target,) = cur.fetchone()
    print(f"  chunk_vectors:          {n_vec:,}")
    print(f"  children a embedar:     {n_target:,}")
    pct = 100 * n_vec / n_target if n_target else 0
    print(f"  cobertura:              {pct:.1f}%")

    if n_vec:
        cur.execute("""
            SELECT c.chunk_id FROM chunks c
            LEFT JOIN chunk_vectors v ON v.chunk_id = c.chunk_id
            WHERE v.chunk_id IS NULL AND c.chunk_level = 1 AND c.text_embed IS NOT NULL
            FETCH FIRST 3 ROWS ONLY
        """)
        missing = [r[0] for r in cur.fetchall()]
        if missing:
            print(f"\n  Ex de chunks embed pendentes: {missing[:3]}")

        cur.execute("""
            SELECT v.chunk_id FROM chunk_vectors v
            LEFT JOIN chunks c ON c.chunk_id = v.chunk_id
            WHERE c.chunk_id IS NULL FETCH FIRST 3 ROWS ONLY
        """)
        orphans = [r[0] for r in cur.fetchall()]
        if orphans:
            print(f"  ALERTA: vetores órfãos (sem chunk correspondente): {orphans}")


def review_referential_integrity(cur):
    hr("5. INTEGRIDADE REFERENCIAL")

    cur.execute("""
        SELECT COUNT(*) FROM extractions e
        LEFT JOIN manifest m ON m.pdf_id = e.pdf_id
        WHERE m.pdf_id IS NULL
    """)
    (n,) = cur.fetchone()
    print(f"  extractions órfãs (pdf_id não em manifest): {n}")

    cur.execute("""
        SELECT COUNT(*) FROM chunks c
        LEFT JOIN manifest m ON m.pdf_id = c.pdf_id
        WHERE m.pdf_id IS NULL
    """)
    (n,) = cur.fetchone()
    print(f"  chunks órfãos (pdf_id não em manifest):      {n}")

    cur.execute("""
        SELECT COUNT(*) FROM chunks c
        LEFT JOIN extractions e ON e.pdf_id = c.pdf_id
        WHERE e.pdf_id IS NULL
    """)
    (n,) = cur.fetchone()
    print(f"  chunks sem extraction:                       {n}")

    cur.execute("""
        SELECT COUNT(*) FROM chunks c
        WHERE c.chunk_level = 1 AND c.parent_chunk_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM chunks p WHERE p.chunk_id = c.parent_chunk_id AND p.chunk_level = 0)
    """)
    (n,) = cur.fetchone()
    print(f"  children apontando pra parent inexistente:   {n}")


def search_query(inf, cfg, tenancy, model_id, conn, query, k=3):
    req = oci.generative_ai_inference.models.EmbedTextDetails(
        inputs=[query],
        serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=model_id),
        compartment_id=tenancy,
        input_type="SEARCH_QUERY",
        truncate="END",
    )
    emb = inf.embed_text(req).data.embeddings[0]
    qvec = array.array("f", emb)

    cur = conn.cursor()
    sql = """
    SELECT c.chunk_id, c.breadcrumb, c.chunk_type, c.page_start,
           VECTOR_DISTANCE(v.embedding, :qvec, COSINE) AS dist,
           SUBSTR(c.text_raw, 1, 200) AS snippet
    FROM chunks c JOIN chunk_vectors v ON v.chunk_id = c.chunk_id
    ORDER BY dist ASC FETCH FIRST :k ROWS ONLY
    """
    cur.execute(sql, {"qvec": qvec, "k": k})
    return cur.fetchall()


def review_search(conn):
    hr("6. TESTE DE BUSCA (multi-query)")
    cfg = oci.config.from_file()
    cfg["region"] = "sa-saopaulo-1"
    tenancy = cfg["tenancy"]
    model_id = get_embed_model_id(cfg, tenancy)
    if not model_id:
        print("  [skip] modelo não achado")
        return
    inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)

    queries = [
        "Quem preside a Comissão Especial de Licitação?",
        "Quais os tipos de leilões para contratação de energia?",
        "Qual o prazo de vigência da Portaria?",
    ]
    for q in queries:
        print(f"\n  QUERY: {q}")
        try:
            results = search_query(inf, cfg, tenancy, model_id, conn, q, k=2)
            for cid, bc, ctype, pg, dist, snip in results:
                snip_text = snip.read() if hasattr(snip, "read") else snip
                snip_text = (snip_text or "").replace("\n", " ")[:150]
                print(f"    dist={dist:.3f} {bc:30s} {ctype:12s} pg={pg} | {snip_text}")
        except Exception as e:
            print(f"    [ERRO] {e}")


def main():
    conn = connect()
    cur = conn.cursor()
    review_download(cur)
    review_extractions(cur)
    review_chunks(cur)
    review_vectors(cur)
    review_referential_integrity(cur)
    review_search(conn)
    conn.close()
    print("\n" + "=" * 72)
    print("  REVIEW COMPLETO")
    print("=" * 72)


if __name__ == "__main__":
    main()
