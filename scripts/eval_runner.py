"""eval_runner.py — executa o agent em todas as queries do dataset, mede e gera relatório.

Métricas:
  - Refusal accuracy: das que devem recusar, quantas recusaram?
  - Doc retrieval recall: das com expected_doc_pattern, quantas tiveram fonte com o pattern?
  - Keyword recall na resposta: das com expected_keywords, quantos termos apareceram?
  - Reference chunk in top-K: das com reference_chunk_id, está no top-K retornado?
  - Latency stats por categoria
  - LLM-as-judge (Cohere R+) — opcional, score 1-5 de fidelidade
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from rag_agent import RAGAgent
from eval_dataset import EVAL_DATASET, CATEGORIES


def keyword_hits(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    if not keywords:
        return 0, []
    text_lower = (text or "").lower()
    hits = [k for k in keywords if k.lower() in text_lower]
    return len(hits), hits


def doc_pattern_match(sources, pattern: str | None) -> bool:
    if not pattern or not sources:
        return False
    pattern_lower = pattern.lower()
    for s in sources:
        bc = (s.breadcrumb or "").lower()
        rt = (s.registro_titulo or "").lower()
        if pattern_lower in bc or pattern_lower in rt:
            return True
    return False


def reference_chunk_in_topk(sources, ref_id: str | None) -> bool:
    if not ref_id or not sources:
        return False
    return any(s.chunk_id == ref_id for s in sources)


def main():
    print(f"\n{'='*84}\n  EVALUATION RUNNER\n{'='*84}")
    print(f"  Dataset: {len(EVAL_DATASET)} queries em {len(CATEGORIES)} categorias\n")

    agent = RAGAgent(verbose=False)
    print("  Aguardando warm-up do bge...")
    agent._bge_warmup_thread.join(timeout=120)
    print("  Bge pronto.\n")

    results = []
    for i, item in enumerate(EVAL_DATASET, start=1):
        print(f"  [{i:2d}/{len(EVAL_DATASET)}] [{item['category']:20s}] {item['query'][:60]}...")
        t0 = time.time()
        resp = agent.answer(item["query"])
        elapsed_ms = int((time.time() - t0) * 1000)

        kw_total, kw_hits = keyword_hits(resp.answer, item["expected_keywords"])
        kw_recall = (kw_total / len(item["expected_keywords"])) if item["expected_keywords"] else None

        doc_match = doc_pattern_match(resp.sources, item["expected_doc_pattern"])
        ref_in_topk = reference_chunk_in_topk(resp.sources, item["reference_chunk_id"])

        # Refusal correctness
        if item["should_refuse"]:
            refusal_correct = resp.refused
        else:
            refusal_correct = not resp.refused

        result = {
            "id": item["id"],
            "category": item["category"],
            "query": item["query"],
            "should_refuse": item["should_refuse"],
            "actual_refused": resp.refused,
            "refusal_reason": resp.refusal_reason,
            "refusal_correct": refusal_correct,
            "doc_pattern_expected": item["expected_doc_pattern"],
            "doc_match": doc_match,
            "expected_keywords": item["expected_keywords"],
            "keyword_recall": kw_recall,
            "keyword_hits_n": kw_total,
            "ref_chunk_id": item["reference_chunk_id"],
            "ref_chunk_in_topk": ref_in_topk,
            "elapsed_ms": elapsed_ms,
            "confidence": resp.confidence,
            "answer_preview": resp.answer[:200],
            "sources_count": len(resp.sources),
            "top_source_breadcrumb": resp.sources[0].breadcrumb if resp.sources else None,
        }
        results.append(result)

        ok_marker = "✓" if refusal_correct else "✗"
        print(f"        {ok_marker} {elapsed_ms:>5}ms  refused={resp.refused}({refusal_correct}) "
              f"doc_match={doc_match}  kw_recall={kw_recall}  ref_in_topk={ref_in_topk}")

    # ─── Relatório ───
    out = Path(__file__).resolve().parent.parent / "inspect" / "eval_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  [salvo em {out}]")

    print(f"\n{'='*84}\n  RELATÓRIO AGREGADO\n{'='*84}")

    # Refusal accuracy
    refusal_correct_n = sum(1 for r in results if r["refusal_correct"])
    print(f"\n  Refusal accuracy: {refusal_correct_n}/{len(results)} ({100*refusal_correct_n/len(results):.1f}%)")

    # Por categoria
    print(f"\n  Por categoria:")
    for cat in CATEGORIES:
        cat_results = [r for r in results if r["category"] == cat]
        if not cat_results:
            continue
        ref_ok = sum(1 for r in cat_results if r["refusal_correct"])
        avg_lat = sum(r["elapsed_ms"] for r in cat_results) / len(cat_results)
        avg_kw = [r["keyword_recall"] for r in cat_results if r["keyword_recall"] is not None]
        avg_kw_str = f"{sum(avg_kw)/len(avg_kw):.0%}" if avg_kw else "N/A"
        doc_ok = sum(1 for r in cat_results if r["doc_match"])
        doc_total = sum(1 for r in cat_results if r["doc_pattern_expected"])
        doc_str = f"{doc_ok}/{doc_total}" if doc_total else "N/A"
        print(f"    {cat:22s}  n={len(cat_results)}  refusal={ref_ok}/{len(cat_results)}"
              f"  doc_match={doc_str}  kw_recall_avg={avg_kw_str}  lat_avg={avg_lat:.0f}ms")

    # Reference chunk recall
    ref_queries = [r for r in results if r["ref_chunk_id"]]
    if ref_queries:
        ref_in_topk_n = sum(1 for r in ref_queries if r["ref_chunk_in_topk"])
        print(f"\n  Reference chunk in top-5 (gabarito): {ref_in_topk_n}/{len(ref_queries)} "
              f"({100*ref_in_topk_n/len(ref_queries):.1f}%)")

    # Latência
    lat_all = [r["elapsed_ms"] for r in results]
    lat_ok = [r["elapsed_ms"] for r in results if not r["actual_refused"]]
    lat_ref = [r["elapsed_ms"] for r in results if r["actual_refused"]]
    print(f"\n  Latência (ms):")
    print(f"    Geral:     min={min(lat_all)}  med={sorted(lat_all)[len(lat_all)//2]}  max={max(lat_all)}  avg={sum(lat_all)/len(lat_all):.0f}")
    if lat_ok:
        print(f"    Aceitas:   min={min(lat_ok)}  med={sorted(lat_ok)[len(lat_ok)//2]}  max={max(lat_ok)}  avg={sum(lat_ok)/len(lat_ok):.0f}")
    if lat_ref:
        print(f"    Recusadas: min={min(lat_ref)}  med={sorted(lat_ref)[len(lat_ref)//2]}  max={max(lat_ref)}  avg={sum(lat_ref)/len(lat_ref):.0f}")

    # Falhas
    failures = [r for r in results if not r["refusal_correct"]]
    if failures:
        print(f"\n  ⚠ FALHAS DE REFUSAL ({len(failures)}):")
        for f in failures:
            print(f"    [{f['id']}] should_refuse={f['should_refuse']} actual={f['actual_refused']} | {f['query'][:70]}")
            print(f"          → {f['answer_preview'][:120]}")


if __name__ == "__main__":
    main()
