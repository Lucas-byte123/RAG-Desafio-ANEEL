"""test_search.py — testa busca vetorial em Oracle 23ai."""

import array
import os
import sys
from pathlib import Path

import oci
import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "Quem preside a Comissão Especial de Licitação?"
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    cfg = oci.config.from_file()
    cfg["region"] = "sa-saopaulo-1"
    tenancy = cfg["tenancy"]

    mgmt = oci.generative_ai.GenerativeAiClient(cfg)
    resp = mgmt.list_models(compartment_id=tenancy)
    model = None
    for m in resp.data.items:
        if (m.display_name or "").lower() == "cohere.embed-multilingual-v3.0" and m.lifecycle_state == "ACTIVE":
            model = m
            break
    if not model:
        sys.exit("Modelo não achado")

    inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)
    req = oci.generative_ai_inference.models.EmbedTextDetails(
        inputs=[query],
        serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=model.id),
        compartment_id=tenancy,
        input_type="SEARCH_QUERY",
        truncate="END",
    )
    emb = inf.embed_text(req).data.embeddings[0]
    qvec = array.array("f", emb)

    conn = oracledb.connect(
        user="ADMIN", password=os.environ["DB_ADMIN_PASS"], dsn="aneelrag_medium",
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS_FILE.read_text().strip(),
    )
    cur = conn.cursor()

    sql = """
    SELECT c.chunk_id, c.breadcrumb, c.chunk_type, c.page_start,
           VECTOR_DISTANCE(v.embedding, :qvec, COSINE) AS dist,
           SUBSTR(c.text_raw, 1, 200) AS snippet
    FROM chunks c
    JOIN chunk_vectors v ON v.chunk_id = c.chunk_id
    ORDER BY dist ASC
    FETCH APPROX FIRST :k ROWS ONLY
    """
    try:
        cur.execute(sql, {"qvec": qvec, "k": k})
    except oracledb.DatabaseError as e:
        if "ORA-13199" in str(e) or "FETCH APPROX" in str(e):
            sql = sql.replace("FETCH APPROX FIRST", "FETCH FIRST")
            cur.execute(sql, {"qvec": qvec, "k": k})
        else:
            raise

    print(f"\n=== QUERY ===\n  {query}\n")
    print(f"=== TOP {k} ===")
    for cid, bc, ctype, pg, dist, snip in cur.fetchall():
        snip_text = snip.read() if hasattr(snip, "read") else snip
        print(f"\n  dist={dist:.4f}  {cid}")
        print(f"  type={ctype}  breadcrumb={bc}  pg={pg}")
        print(f"  snippet: {snip_text}")

    conn.close()


if __name__ == "__main__":
    main()
