"""Testa fluxo conversacional (follow-up com contexto)."""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from rag_agent import RAGAgent


def main():
    agent = RAGAgent(verbose=False)

    history = []

    turns = [
        "Quem preside a Comissão Especial de Licitação?",
        "E quem é o vice-presidente?",
        "Quais tipos de leilões ela coordena?",
    ]

    for t in turns:
        print("\n" + "="*72)
        print(f"USER: {t}")
        print("="*72)
        resp = agent.answer_with_history(t, history=history)
        if resp.rewritten_query:
            print(f"[reformulado: {resp.rewritten_query}]")
        print(f"ASSISTANT: {resp.answer}")
        print(f"[confidence={resp.confidence:.2f}  elapsed={resp.elapsed_ms}ms  refused={resp.refused}]")
        history.append({"role": "user", "content": t})
        history.append({"role": "assistant", "content": resp.answer})


if __name__ == "__main__":
    main()
