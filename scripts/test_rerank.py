"""Smoke test Cohere Rerank."""
import oci, sys, os

cfg = oci.config.from_file()
cfg["region"] = "sa-saopaulo-1"
tenancy = cfg["tenancy"]

mgmt = oci.generative_ai.GenerativeAiClient(cfg)
resp = mgmt.list_models(compartment_id=tenancy)
model = None
for m in resp.data.items:
    if "rerank" in (m.display_name or "").lower() and m.lifecycle_state == "ACTIVE":
        print(f"found: {m.display_name}  caps={m.capabilities}")
        model = m

inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)

from oci.generative_ai_inference.models import RerankTextDetails, OnDemandServingMode

req = RerankTextDetails(
    input="Qual o prazo de vigência da portaria?",
    documents=[
        "Art. 5º Esta Portaria entra em vigor na data de sua publicação, com prazo de vigência de um ano.",
        "Art. 1º Constituir a Comissão Especial de Licitação",
        "Art. 2º A Comissão será composta pelos servidores a seguir",
    ],
    serving_mode=OnDemandServingMode(model_id=model.id),
    compartment_id=tenancy,
    top_n=3,
    is_echo=False,
)
try:
    r = inf.rerank_text(req)
    for d in r.data.document_ranks:
        print(f"  idx={d.index}  score={d.relevance_score:.4f}")
except oci.exceptions.ServiceError as e:
    print(f"ERR status={e.status} code={e.code} msg={e.message[:200]}")
except Exception as e:
    print(f"ERR {type(e).__name__}: {e}")
