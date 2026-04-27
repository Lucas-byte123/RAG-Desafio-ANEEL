"""Smoke test do streaming."""
import sys, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from rag_agent import RAGAgent

a = RAGAgent(verbose=False)

print("\n=== STREAMING TEST ===\n")
print("Q: Quem preside a Comissao Especial de Licitacao?\n")
print("A: ", end="", flush=True)

t_first = None
t0 = time.time()
for evt, payload in a.answer_stream("Quem preside a Comissao Especial de Licitacao?"):
    if evt == "meta":
        t_meta = (time.time() - t0) * 1000
        print(f"\n[retrieval done: {t_meta:.0f}ms — generating...]\nA: ", end="", flush=True)
    elif evt == "token":
        if t_first is None:
            t_first = (time.time() - t0) * 1000
        print(payload, end="", flush=True)
    elif evt == "done":
        elapsed = payload.elapsed_ms
        print(f"\n\n[total {elapsed}ms | first token at {t_first:.0f}ms | sources={len(payload.sources)}]")
