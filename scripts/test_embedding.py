"""Testa se OCI Generative AI retorna embedding. Lista modelos primeiro."""

import oci
import sys

cfg = oci.config.from_file()
TENANCY = cfg["tenancy"]
REGION = "sa-saopaulo-1"
cfg["region"] = REGION

# Mgmt client pra listar modelos
mgmt = oci.generative_ai.GenerativeAiClient(cfg)
resp = mgmt.list_models(compartment_id=TENANCY)
print("=== Modelos EMBEDDING disponíveis ===")
embed_models = []
for m in resp.data.items:
    caps = m.capabilities or []
    if "TEXT_EMBEDDINGS" in caps:
        embed_models.append(m)
        print(f"  [{m.lifecycle_state}] {m.display_name}  vendor={m.vendor}  id={m.id}")

if not embed_models:
    sys.exit("Nenhum modelo de embedding disponível!")

# Pegar Cohere Multilingual v3 EXATO (sem "light" nem "image")
target = None
for m in embed_models:
    name_l = (m.display_name or "").lower()
    if name_l == "cohere.embed-multilingual-v3.0":
        target = m
        break
if not target:
    for m in embed_models:
        name_l = (m.display_name or "").lower()
        if "multilingual" in name_l and "v3" in name_l and "image" not in name_l and "light" not in name_l:
            target = m
            break
if not target:
    target = embed_models[0]

print(f"\n=== Testando embed com: {target.display_name} ===")
print(f"  id: {target.id}")

# Inference client
inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)

req = oci.generative_ai_inference.models.EmbedTextDetails(
    inputs=["Qual é a definição de geração distribuída na legislação ANEEL?",
            "REN 687/2015 dispõe sobre sistemas de compensação de energia."],
    serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=target.id),
    compartment_id=TENANCY,
    input_type="SEARCH_DOCUMENT",
    truncate="END",
)

try:
    resp = inf.embed_text(req)
    embs = resp.data.embeddings
    print(f"\n[OK] Recebidos {len(embs)} embeddings")
    print(f"  dim: {len(embs[0])}")
    print(f"  primeiros 5 valores do vetor 0: {embs[0][:5]}")
except Exception as e:
    print(f"\n[ERRO] {type(e).__name__}: {e}")
    sys.exit(1)
