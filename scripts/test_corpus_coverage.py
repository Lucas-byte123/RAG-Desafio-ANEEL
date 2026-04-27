"""Confirma que o RAG realmente acessa o corpus completo dos 27k PDFs."""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from rag_agent import RAGAgent

agent = RAGAgent(verbose=False)

queries = [
    ("2016 - portaria comissão", "Quem preside a Comissão Especial de Licitação?"),
    ("2021 - definição GD", "Qual a definição de microgeração distribuída?"),
    ("2022 - resolução recente", "Quais resoluções normativas foram publicadas em 2022?"),
    ("Tarifas", "Como é calculada a tarifa de energia elétrica?"),
    ("Anexos técnicos", "Como funcionam as bandeiras tarifárias?"),
]

pdfs_seen = set()
for label, q in queries:
    t0 = time.time()
    resp = agent.answer(q)
    elapsed = (time.time() - t0) * 1000
    print(f"\n[{label}]  {elapsed:.0f}ms  conf={resp.confidence:.2f}  refused={resp.refused}")
    print(f"  Q: {q}")
    if resp.sources:
        for s in resp.sources[:3]:
            pdfs_seen.add(s.pdf_id)
            print(f"    - {s.registro_titulo[:50] if s.registro_titulo else s.pdf_id[:50]}  pg.{s.page_start}  rerank={s.rerank_score:.3f}" if s.rerank_score else f"    - {s.pdf_id[:50]}")

print(f"\n=== TOTAL: {len(pdfs_seen)} PDFs distintos retornados em 5 queries ===")
print("PDFs:")
for p in pdfs_seen:
    print(f"  {p}")
