# Roteiro de fala — RAG ANEEL

**Estrutura:** 5 min talk (slides 1-6) + 7 min demo (slide 7 + DEMO_ROTEIRO.md)
+ 3 min Q&A (slide 8 + apêndice).

---

## Talk (5 min, 8 slides totais com transições)

### 0:00 — Slide 1 (capa)

> "Boa [tarde/noite]. Construí um agente RAG que responde sobre 27 mil PDFs
> da legislação ANEEL. Stack 100% Oracle Cloud. Tá rodando ao vivo nessa URL.
> [aponta]. Em 12 minutos vou mostrar a arquitetura, mostrar o agente
> respondendo, e abrir pra perguntas. Vamos lá."

**Transição:** "Por que construí isso?" → próximo slide.

### 0:30 — Slide 2 (problema)

> "A regulação ANEEL é gigante e densa. 27 mil PDFs em 3 anos. Hoje quem
> precisa consultar usa busca textual fraca em PDFs ou paga consultoria
> jurídica. Eu queria ver até onde dá pra ir com RAG bem feito — citação
> obrigatória, retrieval híbrido, guardrails — pra que a resposta seja
> *verificável*, não 'confie no chatbot'."

**Transição:** "A arquitetura tem dois pipelines:" → próximo slide.

### 1:10 — Slide 3 (arquitetura)

> "Ingestão rodou uma vez, levou 18 horas: download, extração estrutural —
> preservando capítulo, seção, artigo —, chunking Parent-Child, embedding
> com Cohere e indexação no Oracle 23ai com HNSW vetorial e BM25 lexical
> no mesmo banco. Runtime roda a cada query: 5 guardrails, busca híbrida,
> rerank local, e geração com Cohere Command R+ via streaming. Resposta
> sempre cita fonte."

**Transição:** "Cada componente foi escolha consciente:" → próximo slide.

### 1:50 — Slide 4 (stack)

> "Oracle 23ai porque vector + BM25 + relacional num motor só, e Always
> Free até 20 GB. Cohere Multilingual v3 porque é forte em português e
> está em São Paulo. Bge-reranker rodando local em CPU porque o Cohere
> Rerank em São Paulo só tem em Dedicated Cluster — 5 dólares por hora.
> Local: zero custo, 150ms. Cohere Command R+ porque é um modelo TUNADO
> pra RAG. Streamlit pra prototipo rápido. VM Compute pra manter o BGE
> quente em memória — serverless cold start seria 5 segundos a cada query
> fria."

**Transição:** "RAG sem guardrails é perigoso:" → próximo slide.

### 2:40 — Slide 5 (guardrails)

> "5 camadas em ordem CRESCENTE de custo. Recusar cedo economiza tokens
> caros. Cada recusa é registrada com refusal_reason específico no log
> JSONL — eu abro e vejo exatamente por que cada query foi recusada.
> Adicionei recente um classificador de intenção: 'obrigado' responde em
> ZERO milissegundos sem chamar nada; 'explica melhor' usa só o histórico
> em 3 segundos. Pra uma conversa fluida fazia falta."

**Transição:** "Pra confiar no que eu acabei de falar, métrica:" → próximo slide.

### 3:20 — Slide 6 (métricas)

> "Sem métrica, qualquer melhoria é vibe-driven. Eu construí 25
> queries-gabarito em 4 categorias. O eval roda em 8 minutos. Resultados:
> 100% de refusal accuracy nas off-topic; 75% de doc match nas factuais;
> reference chunk em top-5 em 67%; latência off-topic 1 segundo, pergunta
> real 30-40 segundos. É baseline — pra produto precisaria 200+ queries
> e human-in-the-loop. Mas é mais que vibe."

**Transição:** "Vou mostrar ao vivo:" → demo (slide 7).

### 4:00 — Slide 7 (demo, 7 min)

**[Abrir browser em https://137-131-141-27.nip.io/ — janela maximizada]**

Seguir **DEMO_ROTEIRO.md** seção "Demo ao vivo — 7 queries em ordem". Resumo:

| # | Query | O que mostra |
|---|---|---|
| 1 | "qual a altura do neymar" | Recusa em 600ms — guardrail de escopo |
| 2 | "tem desconto na conta de luz pra quem é pobre?" | Glossário coloquial→técnico |
| 3 | "quem é o presidente da aneel?" | Diretor-Geral via system prompt refinado |
| 4 | "qual a multa pra quem rouba luz?" | Honestidade quando corpus tem limite |
| 5 | "o que é microgeração distribuída?" | Happy path — qualidade base |
| 6 | "como reclamar da distribuidora?" | Procedimento via Parent-Child |
| 7 | "o que é PLD?" | Sigla expandida via glossário ANEEL |

**Transição depois da demo:** "Pra fechar:" → último slide.

### 11:00 — Slide 8 (decisões + próximos passos)

> "Algumas decisões que diferenciam: Parent-Child, RRF sem normalização,
> glossário coloquial→técnico, classificador de intenção, citação
> obrigatória. Pra produto: pool Oracle pra concorrência, eval com 200+
> queries, modo offline pra reprodução sem credenciais OCI. Repo no
> GitHub aberto, MIT, com README, DEPLOY.md de produção, Dockerfile
> multi-stage, eval reproduzível. Posso compartilhar."

### 11:30 — Q&A (3 min)

Perguntas prováveis e respostas curtas estão em **APRESENTACAO.md** seção
"Apêndice — perguntas que o avaliador provavelmente vai fazer". Memorizar
3 pelo menos:

1. **"E se o corpus tivesse 1M PDFs?"** → "Oracle HNSW escala com tuning de
   M e ef_construction. Embedding viraria gargalo — usaria batch endpoint
   ou modelo self-hosted."
2. **"Como sabe que não alucina?"** → "Testei queries-trap: 'qual o conteúdo
   do artigo 999 da REN 1000?' — artigo que não existe. O agente recusou
   sem inventar. Citação obrigatória + system prompt instruindo a NÃO
   copiar fontes dos exemplos. Mas não é zero-risco — é mitigação ativa."
3. **"Por que não LangChain / LlamaIndex?"** → "Frameworks abstraem o que eu
   queria controlar: query expansion, threshold de cada guardrail, prompt
   template, lógica Parent-Child. SDKs diretos da Cohere e Oracle me deram
   transparência total e debug fácil."

---

## Pré-apresentação (30 min antes)

- [ ] Abrir https://137-131-141-27.nip.io/ e fazer 1 query qualquer pra
      pré-aquecer o BGE (a 1ª query depois de restart leva ~5s a mais)
- [ ] Abrir health endpoint em outra aba: https://137-131-141-27.nip.io/health
      — confirmar `oracle.ok=true` e `oci_genai.ok=true`
- [ ] Limpar histórico do Chrome/Edge — não vazar prints anteriores
- [ ] Ter este roteiro aberto em outra aba ou no celular
- [ ] Abrir DEMO_ROTEIRO.md numa aba (script da demo de 7 queries)
- [ ] Bateria do laptop > 50% e/ou plugado
- [ ] Fechar todas as outras apps que possam vibrar/notificar

---

## Plano B — se algo falhar durante a demo

- **Streamlit caiu:** abrir health endpoint e mostrar JSON. Mostrar repo
  no GitHub na outra aba. Apontar pro `eval_runner.py` (rodável local).
- **Internet caiu:** mostrar README.md com a seção Arquitetura (tem ASCII
  art do pipeline). Mostrar `scripts/eval_dataset.py` com queries-gabarito.
- **Cohere ficou fora:** o health endpoint mostra `oci_genai.ok=false` —
  apontar pra esse status e explicar fallbacks teóricos. Os guardrails
  (recusa temporal, escopo, retrieval híbrido) continuam funcionando sem
  LLM, dá pra demonstrar parcialmente.

---

## Como você vai falar — dicas técnicas

- **Ritmo:** 5 min são ~600 palavras. Não enche de bullets verbalmente —
  diga 1-2 frases por slide e DEIXE A IMAGEM TRABALHAR.
- **Demos antes de slides:** se sentir que o avaliador tá perdido, abre o
  agente e mostra. Concreto > abstrato.
- **Honestidade vence:** quando algo não funciona perfeito (Q4 sobre
  multa), fala "olha, esse caso o corpus 2016-2022 não cobre — o agente
  preferiu dizer 'não encontrei' do que inventar. Isso é desejável."
- **Em Q&A difícil:** "Boa pergunta, não testei isso especificamente. Pelo
  que sei do componente X, eu esperaria Y, mas só com dado eu te respondo
  com confiança." É melhor que chutar.
