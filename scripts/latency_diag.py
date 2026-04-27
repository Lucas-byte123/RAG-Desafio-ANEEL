"""Diagnóstico DETALHADO de latência por componente do RAG."""

import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import oci
import oracledb
import array

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"


def t(label):
    return time.time()


print("=" * 72)
print("  DIAGNÓSTICO DE LATÊNCIA — cada componente isolado")
print("=" * 72)

# 0. Conectar tudo
t0 = t("init")
cfg = oci.config.from_file()
cfg["region"] = "sa-saopaulo-1"
tenancy = cfg["tenancy"]
mgmt = oci.generative_ai.GenerativeAiClient(cfg)
inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)
print(f"  [init OCI clients]               {(time.time()-t0)*1000:.0f} ms")

# Buscar models
t0 = time.time()
resp = mgmt.list_models(compartment_id=tenancy)
models = resp.data.items
embed_model = next((m for m in models if (m.display_name or "").lower() == "cohere.embed-multilingual-v3.0" and m.lifecycle_state == "ACTIVE"), None)
llm_model = next((m for m in models if "command-r-plus" in (m.display_name or "").lower() and m.lifecycle_state == "ACTIVE"), None)
print(f"  [list models]                    {(time.time()-t0)*1000:.0f} ms")

# DB
t0 = time.time()
wallet_pwd = WALLET_PASS_FILE.read_text().strip()
conn = oracledb.connect(user="ADMIN", password=os.environ["DB_ADMIN_PASS"], dsn="aneelrag_medium",
                        config_dir=str(WALLET_DIR), wallet_location=str(WALLET_DIR), wallet_password=wallet_pwd)
print(f"  [DB connect]                     {(time.time()-t0)*1000:.0f} ms")

# Test query
QUERY = "Quem preside a Comissao Especial de Licitacao?"
print(f"\n  Query: '{QUERY}'\n")

# 1. Embed query
print("─" * 50)
t0 = time.time()
req = oci.generative_ai_inference.models.EmbedTextDetails(
    inputs=[QUERY],
    serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=embed_model.id),
    compartment_id=tenancy,
    input_type="SEARCH_QUERY",
    truncate="END",
)
emb = inf.embed_text(req).data.embeddings[0]
qvec = array.array("f", emb)
elapsed_embed = (time.time() - t0) * 1000
print(f"  1. Embed query (Cohere)          {elapsed_embed:.0f} ms")

# 2. Vector search
t0 = time.time()
cur = conn.cursor()
cur.execute("""
    SELECT c.chunk_id, VECTOR_DISTANCE(v.embedding, :qvec, COSINE)
    FROM chunks c JOIN chunk_vectors v ON v.chunk_id = c.chunk_id
    WHERE c.chunk_level = 1
    ORDER BY 2 ASC FETCH FIRST 25 ROWS ONLY
""", {"qvec": qvec})
vec_results = cur.fetchall()
elapsed_vec = (time.time() - t0) * 1000
print(f"  2. Vector search (HNSW 250k)     {elapsed_vec:.0f} ms  (top1 dist={vec_results[0][1]:.3f})")

# 3. BM25 search
t0 = time.time()
cur.execute("""
    SELECT c.chunk_id, SCORE(1) AS s
    FROM chunks c
    WHERE c.chunk_level = 1 AND CONTAINS(c.text_embed, :q, 1) > 0
    ORDER BY s DESC FETCH FIRST 25 ROWS ONLY
""", {"q": "preside OR Comissão OR Especial OR Licitação"})
bm25_results = cur.fetchall()
elapsed_bm25 = (time.time() - t0) * 1000
print(f"  3. BM25 search (Oracle Text)     {elapsed_bm25:.0f} ms  ({len(bm25_results)} hits)")

# 4. bge rerank
t0 = time.time()
from sentence_transformers import CrossEncoder
m = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
elapsed_load = (time.time() - t0) * 1000
print(f"  4a. bge load (1ª vez)            {elapsed_load:.0f} ms")

# Get text for top 30 chunks
chunk_ids = [r[0] for r in vec_results]
placeholders = ",".join(f":p{i}" for i in range(len(chunk_ids)))
params = {f"p{i}": cid for i, cid in enumerate(chunk_ids)}
cur.execute(f"SELECT chunk_id, text_embed FROM chunks WHERE chunk_id IN ({placeholders})", params)
texts = [(cid, te) for cid, te in cur.fetchall()]
t0 = time.time()
pairs = [[QUERY, te[:1500]] for cid, te in texts]
scores = m.predict(pairs, show_progress_bar=False)
elapsed_rerank = (time.time() - t0) * 1000
print(f"  4b. bge rerank 25 docs           {elapsed_rerank:.0f} ms")

# 5. LLM
t0 = time.time()
from oci.generative_ai_inference.models import (
    ChatDetails, OnDemandServingMode, CohereChatRequest
)
req = ChatDetails(
    compartment_id=tenancy,
    serving_mode=OnDemandServingMode(model_id=llm_model.id),
    chat_request=CohereChatRequest(
        message=f"Responda em PT-BR: {QUERY}\nContexto: O presidente da CEL é Romário de Oliveira Batista.",
        temperature=0.2,
        max_tokens=200,
        is_stream=False,
    ),
)
resp = inf.chat(req)
elapsed_llm = (time.time() - t0) * 1000
print(f"  5. LLM Cohere R+ (200 tok)       {elapsed_llm:.0f} ms")

# Total
print("─" * 50)
total = elapsed_embed + elapsed_vec + elapsed_bm25 + elapsed_rerank + elapsed_llm
print(f"  TOTAL (sem bge load)             {total:.0f} ms")
print(f"  TOTAL (com bge load)             {total + elapsed_load:.0f} ms")

print("\n" + "=" * 72)
print("  STREAMING TEST")
print("=" * 72)
import json
t0 = time.time()
req2 = ChatDetails(
    compartment_id=tenancy,
    serving_mode=OnDemandServingMode(model_id=llm_model.id),
    chat_request=CohereChatRequest(
        message=f"Em 30 palavras: {QUERY}",
        temperature=0.2,
        max_tokens=200,
        is_stream=True,
    ),
)
resp = inf.chat(req2)
n_tokens = 0
n_events = 0
first_token_at = None
for event in resp.data.events():
    n_events += 1
    try:
        data = json.loads(event.data)
        if data.get("text") and not data.get("finishReason"):
            if first_token_at is None:
                first_token_at = (time.time() - t0) * 1000
            n_tokens += 1
    except Exception:
        pass

elapsed = (time.time() - t0) * 1000
print(f"  Events:               {n_events}")
print(f"  Token chunks:         {n_tokens}")
print(f"  First token at:       {first_token_at}ms" if first_token_at else "  (no token chunks)")
print(f"  Total streaming:      {elapsed:.0f}ms")
if n_tokens > 1:
    print(f"  → streaming REAL (chunks chegando incrementalmente)")
elif n_tokens == 1:
    print(f"  → streaming FAKE (vem 1 chunk só com tudo) ← problema!")
else:
    print(f"  → sem chunks de texto")

conn.close()
