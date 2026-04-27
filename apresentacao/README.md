# 📁 Material da apresentação — RAG ANEEL

**Apresentação:** 2026-04-27
**URL ao vivo:** https://137-131-141-27.nip.io/
**Repo:** https://github.com/Lucas-byte123/RAG-Desafio-ANEEL

---

## Como usar esta pasta

Os arquivos estão numerados na ordem em que você deve consumi-los:

| # | Arquivo | Pra que serve |
|---|---|---|
| 01 | [`01_BRIEFING_ESCOLHAS.md`](01_BRIEFING_ESCOLHAS.md) | **Briefing das escolhas técnicas** no formato Q&A. 16 seções: cada uma tem o que foi escolhido, alternativas que existiam, e um soundbite pronto pra você usar. **Comece aqui** — é a referência completa pro Q&A. |
| 02 | [`02_SLIDES.md`](02_SLIDES.md) | **8 slides** prontos com bullets + notas do orador. Formato Markdown compatível com Marp (pode virar HTML/PDF) ou copiar pra Google Slides/PPT slide a slide. |
| 03 | [`03_ROTEIRO_TALK.md`](03_ROTEIRO_TALK.md) | **Roteiro de fala** com tempo estimado por slide, transições, plano B se algo falhar, checklist 30 min antes da apresentação. |
| 04 | [`04_DEMO_ROTEIRO.md`](04_DEMO_ROTEIRO.md) | **Roteiro da demo ao vivo** — 7 queries pré-selecionadas em ordem, com falas pra cada uma. Use isso quando abrir o navegador na frente do avaliador. |

---

## Plano sugerido pra próximas 5h

**1h antes da apresentação:**
- Ler `01_BRIEFING_ESCOLHAS.md` (15 min) — preencher os "Pra você" das
  seções 1-5 com 1-2 frases pessoais (5 min cada). Ou só usar os
  soundbites prontos.
- Ler `03_ROTEIRO_TALK.md` (5 min) — mentalizar o ritmo dos 5 min de talk.
- Ler `04_DEMO_ROTEIRO.md` (5 min) — confirmar que sabe a ordem das 7 queries.

**30 min antes:**
- Checklist do `03_ROTEIRO_TALK.md` seção "Pré-apresentação".

**Durante:**
- Manter `03_ROTEIRO_TALK.md` aberto no celular ou outra aba como cola.
- Se travou no Q&A: `01_BRIEFING_ESCOLHAS.md` apêndice tem respostas
  prontas pras 5 perguntas mais prováveis.

---

## Slides — como gerar HTML/PDF (opcional)

Se quiser renderizar `02_SLIDES.md` como apresentação visual:

```bash
# Instalar Marp CLI (uma vez, requer Node)
npm install -g @marp-team/marp-cli

# A partir desta pasta:
cd apresentacao
marp 02_SLIDES.md --html -o slides.html
# OU:
marp 02_SLIDES.md --pdf -o slides.pdf
# OU rodar com auto-reload enquanto edita:
marp -s 02_SLIDES.md
```

Se não quiser instalar Marp, basta abrir o `.md` no VS Code com a extensão
**Marp for VS Code** instalada — funciona offline e mostra preview.

Última opção: copiar manualmente cada slide pro Google Slides ou
PowerPoint — são 8, leva ~10 min.
