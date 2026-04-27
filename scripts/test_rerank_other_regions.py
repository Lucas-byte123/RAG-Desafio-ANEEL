"""Testa Cohere Rerank em outras regiões OCI: us-chicago-1, eu-frankfurt-1, us-ashburn-1."""

import oci
import sys

REGIONS = ["us-chicago-1", "us-ashburn-1", "eu-frankfurt-1"]

cfg_base = oci.config.from_file()
tenancy = cfg_base["tenancy"]

for region in REGIONS:
    print(f"\n{'='*60}\n  REGIÃO: {region}\n{'='*60}")
    cfg = dict(cfg_base)
    cfg["region"] = region
    try:
        mgmt = oci.generative_ai.GenerativeAiClient(cfg)
        resp = mgmt.list_models(compartment_id=tenancy)
        rerank_models = [m for m in resp.data.items
                         if "rerank" in (m.display_name or "").lower()
                         and m.lifecycle_state == "ACTIVE"]
        if not rerank_models:
            print(f"  [info] sem modelos rerank ATIVE listados")
            continue
        for m in rerank_models:
            print(f"  found: {m.display_name}  caps={m.capabilities}")

        target = rerank_models[0]
        print(f"\n  testando inferência com {target.display_name}...")

        inf = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg)
        from oci.generative_ai_inference.models import RerankTextDetails, OnDemandServingMode

        req = RerankTextDetails(
            input="Qual o prazo de vigência da Portaria?",
            documents=[
                "Art. 5º Esta Portaria entra em vigor com prazo de vigência de um ano.",
                "Art. 1º Constituir a Comissão Especial de Licitação",
            ],
            serving_mode=OnDemandServingMode(model_id=target.id),
            compartment_id=tenancy,
            top_n=2, is_echo=False,
        )
        r = inf.rerank_text(req)
        print(f"  ✓ FUNCIONOU em {region}")
        for d in r.data.document_ranks:
            print(f"    idx={d.index}  score={d.relevance_score:.4f}")

    except oci.exceptions.ServiceError as e:
        if e.status == 404:
            print(f"  ✗ status 404 — modelo listado mas não serving inference")
        elif e.status == 401 or e.status == 403:
            print(f"  ✗ status {e.status} — sem permissão pra essa região")
        else:
            print(f"  ✗ status {e.status} {e.code}: {e.message[:120]}")
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {str(e)[:120]}")
