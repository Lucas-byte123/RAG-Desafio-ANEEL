"""Teste completo do agente com corpus de 250k chunks + bge rerank + indexes."""

import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from rag_agent import RAGAgent

agent = RAGAgent(verbose=False)

tests = [
    ("Factual simples", "Quem preside a Comissão Especial de Licitação?"),
    ("Factual com número", "O que diz o Art. 5 da Resolução Normativa 1000?"),
    ("Tema regulatório", "Qual a definição de geração distribuída na legislação?"),
    ("Tema técnico", "Como funciona o sistema de compensação de energia elétrica?"),
    ("Filtro temporal", "Quais resoluções normativas foram publicadas em 2022?"),
    ("Off-topic", "Qual a taxa Selic atual?"),
    ("Fora do escopo temporal", "O que aconteceu em 2027?"),
    ("Tarifas", "Como é calculada a tarifa de energia elétrica?"),
]

print(f"\n{'='*72}\n  TESTE DO AGENTE — corpus completo\n{'='*72}")

for label, query in tests:
    print(f"\n{'─'*72}")
    print(f"  {label}: {query}")
    print(f"{'─'*72}")
    t0 = time.time()
    resp = agent.answer(query)
    elapsed = (time.time() - t0) * 1000

    if resp.refused:
        print(f"  [RECUSADO — {resp.refusal_reason}]")
        print(f"  {resp.answer[:300]}")
    else:
        print(f"  Resposta: {resp.answer[:600]}")
        if resp.sources:
            print(f"\n  Fontes top 3:")
            for i, s in enumerate(resp.sources[:3], start=1):
                score = (f"rerank={s.rerank_score:.3f}" if s.rerank_score is not None
                         else f"dist={s.vector_dist:.3f}")
                print(f"    [{i}] {s.breadcrumb[:60]}  pg.{s.page_start}  {score}")

    print(f"\n  ⏱  {elapsed:.0f}ms  |  confidence={resp.confidence:.2f}")
