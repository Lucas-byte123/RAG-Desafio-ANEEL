"""Investigação profunda do Cohere Rerank em sa-saopaulo-1."""

import oci
import sys
import json

cfg = oci.config.from_file()
cfg["region"] = "sa-saopaulo-1"
tenancy = cfg["tenancy"]

print("="*72)
print("  1. LISTANDO TODOS OS MODELOS RERANK em sa-saopaulo-1")
print("="*72)

mgmt = oci.generative_ai.GenerativeAiClient(cfg)
resp = mgmt.list_models(compartment_id=tenancy)

rerank_all = []
for m in resp.data.items:
    name = (m.display_name or "").lower()
    caps = m.capabilities or []
    if "rerank" in name or "TEXT_RERANK" in caps:
        rerank_all.append(m)
        print(f"\n  {m.display_name}")
        print(f"    id: {m.id}")
        print(f"    state: {m.lifecycle_state}")
        print(f"    caps: {caps}")
        print(f"    vendor: {m.vendor}")
        if hasattr(m, "version"):
            print(f"    version: {m.version}")
        if hasattr(m, "is_long_term_supported"):
            print(f"    LTS: {m.is_long_term_supported}")
        if hasattr(m, "type"):
            print(f"    type: {m.type}")

if not rerank_all:
    print("  Nenhum modelo rerank no compartment raiz.")
    sys.exit()

print("\n" + "="*72)
print("  2. TESTANDO CADA UM COM REQUEST MINIMAL")
print("="*72)

inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)
from oci.generative_ai_inference.models import RerankTextDetails, OnDemandServingMode

for m in rerank_all:
    print(f"\n  → {m.display_name}")
    try:
        req = RerankTextDetails(
            input="prazo de vigência",
            documents=["Esta Portaria entra em vigor com prazo de um ano.",
                      "Os leilões serão coordenados pela CEL."],
            serving_mode=OnDemandServingMode(model_id=m.id),
            compartment_id=tenancy,
            top_n=2,
        )
        r = inf.rerank_text(req)
        print(f"    ✓ FUNCIONOU")
        for d in r.data.document_ranks:
            print(f"       idx={d.index}  score={d.relevance_score:.4f}")
    except oci.exceptions.ServiceError as e:
        print(f"    ✗ status={e.status} code={e.code}")
        print(f"       msg: {e.message[:200]}")
        if hasattr(e, "request_endpoint"):
            print(f"       endpoint: {e.request_endpoint}")

print("\n" + "="*72)
print("  3. CHECANDO ENDPOINTS DEDICATED (caso seja por endpoint)")
print("="*72)
try:
    resp = mgmt.list_endpoints(compartment_id=tenancy)
    if not resp.data.items:
        print("  Nenhum endpoint dedicated criado.")
    for e in resp.data.items[:5]:
        print(f"  endpoint: {e.display_name}  state={e.lifecycle_state}")
except Exception as e:
    print(f"  erro listando endpoints: {e}")

print("\n" + "="*72)
print("  4. CHECANDO se existe Hosted Application com rerank")
print("="*72)
try:
    resp = mgmt.list_hosted_deployments(compartment_id=tenancy)
    if not resp.data.items:
        print("  Nenhum hosted deployment.")
    for h in resp.data.items[:5]:
        print(f"  deployment: {h.display_name}  state={h.lifecycle_state}")
except Exception as e:
    print(f"  erro: {e}")
