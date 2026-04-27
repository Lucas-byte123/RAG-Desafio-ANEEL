"""
embed_index.py — gera embeddings dos chunks via OCI Generative AI + grava no Oracle.

- Seleciona chunks (default: apenas children, chunk_level=1) ainda sem embedding
- Chama Cohere Embed Multilingual v3 em batches de 96, input_type=SEARCH_DOCUMENT
- Grava VECTOR(1024, FLOAT32) na tabela chunk_vectors
- Idempotente: rodar 2x não re-embeda (LEFT JOIN não matched)

Env:
    DB_ADMIN_PASS — senha ADMIN
Uso:
    python scripts/embed_index.py
    python scripts/embed_index.py --limit 500
    python scripts/embed_index.py --level all  # embeda parents também
"""

from __future__ import annotations

import argparse
import array
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import oci
import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"

DSN = "aneelrag_medium"
USER = "ADMIN"

BATCH_SIZE = 96          # limite do Cohere
EMBED_DIM = 1024


def env_or_die(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"ERRO: {name} não definida")
    return v


def make_db_conn(pwd, wallet_pwd):
    return oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )


def find_embedding_model(mgmt_client, tenancy_id):
    resp = mgmt_client.list_models(compartment_id=tenancy_id)
    for m in resp.data.items:
        name = (m.display_name or "").lower()
        if name == "cohere.embed-multilingual-v3.0" and (m.lifecycle_state or "") == "ACTIVE":
            return m
    for m in resp.data.items:
        name = (m.display_name or "").lower()
        caps = m.capabilities or []
        if ("TEXT_EMBEDDINGS" in caps and "multilingual" in name and "v3" in name
                and "image" not in name and "light" not in name):
            return m
    sys.exit("Nenhum modelo Cohere multilingual v3 encontrado")


SELECT_PENDING_SQL = """
SELECT c.chunk_id, c.text_embed
FROM chunks c
LEFT JOIN chunk_vectors v ON v.chunk_id = c.chunk_id
WHERE v.chunk_id IS NULL
  AND c.text_embed IS NOT NULL
  AND LENGTH(c.text_embed) > 10
  {level_filter}
ORDER BY c.pdf_id, c.ordem_doc
"""

UPSERT_VECTOR_SQL = """
MERGE INTO chunk_vectors t
USING (SELECT :chunk_id AS chunk_id FROM dual) s
ON (t.chunk_id = s.chunk_id)
WHEN MATCHED THEN UPDATE SET embedding = :embedding, model_id = :model_id, embedded_at = :embedded_at
WHEN NOT MATCHED THEN INSERT (chunk_id, embedding, model_id, embedded_at)
VALUES (:chunk_id, :embedding, :model_id, :embedded_at)
"""


def batch(iterable, n):
    buf = []
    for item in iterable:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def embed_batch(inf_client, model_id, compartment_id, texts, input_type="SEARCH_DOCUMENT"):
    req = oci.generative_ai_inference.models.EmbedTextDetails(
        inputs=texts,
        serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=model_id),
        compartment_id=compartment_id,
        input_type=input_type,
        truncate="END",
    )
    resp = inf_client.embed_text(req)
    return resp.data.embeddings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--level", choices=["child", "parent", "all"], default="child")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    pwd = env_or_die("DB_ADMIN_PASS")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    cfg = oci.config.from_file()
    cfg["region"] = "sa-saopaulo-1"
    tenancy = cfg["tenancy"]

    print("[INFO] Listando modelos embeddings...")
    mgmt = oci.generative_ai.GenerativeAiClient(cfg)
    model = find_embedding_model(mgmt, tenancy)
    print(f"[INFO] Usando: {model.display_name}")

    inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)

    print(f"[INFO] Conectando {DSN}...")
    conn = make_db_conn(pwd, wallet_pwd)
    cur = conn.cursor()

    level_filter = ""
    if args.level == "child":
        level_filter = "AND c.chunk_level = 1"
    elif args.level == "parent":
        level_filter = "AND c.chunk_level = 0"
    sql = SELECT_PENDING_SQL.format(level_filter=level_filter)
    if args.limit > 0:
        sql = sql.rstrip().rstrip(";") + f"\nFETCH FIRST {args.limit} ROWS ONLY"

    cur.arraysize = 500
    cur.prefetchrows = 501
    cur.execute(sql)

    pending = []
    while True:
        block = cur.fetchmany(500)
        if not block:
            break
        for cid, te in block:
            text = te if isinstance(te, str) else (te.read() if te else "")
            if text and len(text) > 10:
                pending.append((cid, text))

    print(f"[INFO] {len(pending):,} chunks a embedar ({args.level})")
    if not pending:
        print("[OK] Nada a fazer.")
        conn.close()
        return

    total = len(pending)
    n_ok = 0
    n_fail = 0
    t0 = time.time()
    model_id = model.id

    upsert_cur = conn.cursor()

    for bi, chunk_batch in enumerate(batch(pending, args.batch_size)):
        ids = [c[0] for c in chunk_batch]
        texts = [c[1] for c in chunk_batch]
        try:
            embs = embed_batch(inf, model_id, tenancy, texts)
        except oci.exceptions.ServiceError as e:
            print(f"  [BATCH {bi} ERRO] {e.status} {e.code}: {e.message[:100]}", flush=True)
            if e.status == 429:
                time.sleep(10)
                try:
                    embs = embed_batch(inf, model_id, tenancy, texts)
                except Exception as e2:
                    n_fail += len(chunk_batch)
                    continue
            else:
                n_fail += len(chunk_batch)
                continue
        except Exception as e:
            print(f"  [BATCH {bi} ERRO] {type(e).__name__}: {e}", flush=True)
            n_fail += len(chunk_batch)
            continue

        ts = datetime.now(timezone.utc).isoformat()
        rows_to_save = []
        for cid, emb in zip(ids, embs):
            vec = array.array("f", emb)
            rows_to_save.append({
                "chunk_id": cid,
                "embedding": vec,
                "model_id": model.display_name,
                "embedded_at": ts,
            })
        try:
            upsert_cur.executemany(UPSERT_VECTOR_SQL, rows_to_save)
            conn.commit()
            n_ok += len(rows_to_save)
        except Exception as e:
            print(f"  [DB ERRO batch {bi}] {e}", flush=True)
            n_fail += len(rows_to_save)
            continue

        if (bi + 1) % 5 == 0 or (bi + 1) * args.batch_size >= total:
            elapsed = time.time() - t0
            rate = n_ok / elapsed if elapsed else 0
            eta = (total - n_ok) / rate / 60 if rate else 0
            print(f"  [{n_ok:,}/{total:,}] ok  fail={n_fail} taxa={rate:.0f}/s "
                  f"eta={eta:.1f}min", flush=True)

    conn.close()
    elapsed = time.time() - t0
    print(f"\n[FIM] ok={n_ok:,} fail={n_fail:,} em {elapsed/60:.1f}min "
          f"(taxa={n_ok/elapsed:.0f}/s)")


if __name__ == "__main__":
    main()
