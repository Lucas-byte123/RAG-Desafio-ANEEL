"""Bateria de testes finais — variedade de queries com cronometragem."""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from rag_agent import RAGAgent

agent = RAGAgent(verbose=False)

# Esperar bge esquentar
print("Aguardando warm-up do bge...")
agent._bge_warmup_thread.join(timeout=120)
print("Bge pronto.\n")

testes = [
    ("OFF-TOPIC", "Qual altura do Neymar?"),
    ("OFF-TOPIC", "Como fazer brigadeiro?"),
    ("OFF-TOPIC", "Quem ganhou a Copa do Mundo de 2022?"),
    ("GUARDRAIL ANO", "Resoluções da ANEEL em 2027"),
    ("GUARDRAIL ESCOPO", "Qual taxa Selic atual?"),
    ("FACTUAL CURTA", "Quem preside a Comissão Especial de Licitação?"),
    ("FACTUAL NUMÉRICA", "Qual o prazo de vigência da Portaria 3700?"),
    ("DEFINIÇÃO", "O que é geração distribuída?"),
    ("PROCESSO", "Como funciona o sistema de compensação de energia elétrica?"),
    ("INSTITUCIONAL", "Como funciona a ANEEL?"),
    ("MULTI-DOC", "Quais resoluções normativas foram publicadas em 2022?"),
    ("TARIFAS", "Como é calculada a tarifa de energia elétrica?"),
    ("BANDEIRAS", "O que são as bandeiras tarifárias?"),
    ("TÉCNICA", "Como funciona o leilão de energia A-5?"),
    ("REGULATÓRIA", "Quais são os deveres das distribuidoras de energia elétrica?"),
]

print("="*84)
print(f"  {'CATEGORIA':20s}  {'TIME':>7s}  STATUS  QUERY")
print("="*84)

results = []
for label, q in testes:
    t0 = time.time()
    r = agent.answer(q)
    elapsed_ms = (time.time()-t0)*1000
    status = "RECUSA" if r.refused else f"OK[{r.confidence:.0%}]"
    results.append((label, elapsed_ms, status, q, r))
    short_q = q[:54]
    print(f"  {label[:20]:20s}  {elapsed_ms:>5.0f}ms  {status:8s}  {short_q}")

print("\n" + "="*84)
print("  ESTATÍSTICAS")
print("="*84)
ok_times = [r[1] for r in results if not r[4].refused]
ref_times = [r[1] for r in results if r[4].refused]
print(f"  Queries OK:       {len(ok_times)}")
print(f"  Queries recusadas: {len(ref_times)}")
if ok_times:
    print(f"  Tempo OK:    min={min(ok_times):.0f}  med={sorted(ok_times)[len(ok_times)//2]:.0f}  max={max(ok_times):.0f}  avg={sum(ok_times)/len(ok_times):.0f} ms")
if ref_times:
    print(f"  Tempo recusa: min={min(ref_times):.0f}  med={sorted(ref_times)[len(ref_times)//2]:.0f}  max={max(ref_times):.0f}  avg={sum(ref_times)/len(ref_times):.0f} ms")
