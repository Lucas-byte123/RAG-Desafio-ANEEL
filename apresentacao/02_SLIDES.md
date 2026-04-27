# Slides — RAG ANEEL

**Formato:** 8 slides totais. Pensados pra 5-6 min de fala (não fica nem
acelerado nem arrastado). Cada slide tem **bullets** (pra projetar) e **notas
do orador** (o que você fala). O conteúdo é em Markdown — abre direto no
[Marp for VS Code](https://marketplace.visualstudio.com/items?itemName=marp-team.marp-vscode),
ou cole no Google Slides/PowerPoint slide a slide.

---

## SLIDE 1 — Capa

**Título:** RAG sobre 27.025 PDFs da legislação ANEEL

**Subtítulo:** Stack 100% Oracle Cloud + open-source onde dá vantagem técnica

**Rodapé:**
- Giancarlo Moraes
- Apresentação 2026-04-27
- Demo ao vivo: https://137-131-141-27.nip.io/
- Repo: https://github.com/Lucas-byte123/RAG-Desafio-ANEEL

**Notas do orador (~30s):**
> "Olá! Construí um agente RAG que responde perguntas sobre a legislação da
> ANEEL — Agência Nacional de Energia Elétrica do Brasil. O corpus tem 27 mil
> PDFs dos anos 2016, 2021 e 2022. A stack é 100% Oracle Cloud com componentes
> open-source onde fazem diferença técnica. O agente está rodando 24/7 nessa
> URL — vou demonstrar ao vivo."

---

## SLIDE 2 — Por que esse problema importa

**Bullets:**
- Legislação ANEEL: **resoluções, portarias, despachos, ofícios, notas técnicas**
- 27 mil PDFs em **3 anos** — corpus técnico-jurídico denso
- Hoje: consulta manual, busca textual fraca, **siglas e jargão** afastam não-especialistas
- **Custo de errar:** decisões regulatórias afetam tarifa, geração distribuída, conexão à rede

**Notas do orador (~40s):**
> "Por que isso? A regulação do setor elétrico brasileiro é volumosa e técnica.
> Resoluções normativas, portarias, despachos — só nos 3 anos que peguei,
> são 27 mil documentos. Hoje quem precisa consultar isso usa busca textual
> fraca em PDFs ou paga consultoria jurídica. Um agente RAG bem feito permite
> uma camada de acesso muito mais ampla — e o custo de uma resposta errada é
> alto: a regulação afeta tarifa de luz, conexão de geração distribuída,
> autos de infração. Por isso construí com **citação obrigatória** de fonte
> em cada afirmação — o usuário sempre pode verificar."

---

## SLIDE 3 — Arquitetura em 1 diagrama

**Diagrama (use ASCII no Marp ou desenhe simples no PPT):**

```
                  ┌──────────────────────────────────────────┐
INGESTÃO          │  27k PDFs → extract → chunker (P-C)      │
(rodada 1x)       │              ↓                           │
                  │   Cohere Embed Multilingual v3 (OCI)     │
                  │              ↓                           │
                  │   Oracle 23ai (HNSW + Text BM25)         │
                  └──────────────────────────────────────────┘

                  ┌──────────────────────────────────────────┐
RUNTIME           │  query → guardrails (5 camadas)          │
(por request)     │       → vector + BM25 → RRF fusion       │
                  │       → bge-reranker-v2-m3 (local CPU)   │
                  │       → Cohere Command R+ (streaming)    │
                  │       → resposta + [FONTE: ...]          │
                  └──────────────────────────────────────────┘
```

**Notas do orador (~40s):**
> "Tem dois pipelines. O de **ingestão** rodou uma vez, levou cerca de 18 horas:
> baixou os 27 mil PDFs, extraiu texto preservando hierarquia legal, fez
> chunking Parent-Child, gerou embeddings com Cohere e indexou no Oracle 23ai
> com HNSW vetorial e BM25 lexical. O de **runtime** roda a cada query: passa
> por 5 guardrails, faz busca híbrida vetor+BM25, funde com RRF, faz rerank
> com bge-reranker rodando local em CPU, e gera a resposta com Cohere Command
> R+ via streaming. Resposta cita fonte sempre."

---

## SLIDE 4 — Stack: o que escolhi e por quê

**Tabela:**

| Camada | Escolha | Razão principal |
|---|---|---|
| Banco vetorial + lexical | **Oracle Autonomous DB 23ai** | Vector HNSW + BM25 + relacional num motor só. Always Free 20 GB. |
| Embeddings | **Cohere Multilingual v3** (OCI) | Forte em PT-BR, 1024 dim, hospedado em sa-saopaulo-1 |
| Reranker | **bge-reranker-v2-m3** (local CPU) | Cohere Rerank em SP só via Dedicated (US$ 5/h). BGE: zero custo, ~150ms. |
| LLM | **Cohere Command R+ 08-2024** (OCI) | Tunado pra RAG (citações, baixa alucinação), nativo OCI |
| UI | **Streamlit** + SSE streaming | 200 linhas de Python, chat + sidebar. Pro MVP é o melhor ROI. |
| Compute | **OCI E5.Flex** + Caddy + systemd hardened | VM mantém BGE quente em RAM (vs cold start serverless de 5s) |

**Notas do orador (~50s):**
> "Stack: **Oracle 23ai** porque vector + BM25 + relacional num único motor —
> menos componentes, menos pontos de falha, e Always Free até 20 GB.
> **Cohere Embed Multilingual v3** porque é forte em português e está
> hospedado em São Paulo, latência baixa. **Bge-reranker local** porque o
> Cohere Rerank em São Paulo só está disponível em Dedicated Cluster, que
> custa 5 dólares por hora — caríssimo. O BGE é multilíngue, SOTA, roda em
> CPU em 150ms. **Cohere Command R+** porque é um modelo TUNADO pra RAG —
> citações inline, baixa tendência a inventar fato sobre contexto fornecido.
> **Streamlit** porque pro MVP me dá chat + sidebar em 200 linhas Python.
> **VM Compute** em vez de serverless porque o BGE fica QUENTE em memória —
> serverless teria cold start de 5 segundos a cada query."

---

## SLIDE 5 — 5 camadas de guardrails

**Diagrama:**

```
Camada 1   ─→  Temporal       (regex de ano)        ~1 ms
Camada 2   ─→  Escopo          (keyword domínio)     ~1 ms
Camada 3   ─→  Vazio           (retrieval = 0)       ~500 ms
Camada 4   ─→  Gap semântico   (vector mediano)      ~500 ms  [só se BGE off]
Camada 5a  ─→  Rerank low      (BGE score < 0.02)    ~200 ms
Camada 5b  ─→  Citação ausente (LLM sem [FONTE:])    ~30 s
                       ↓
              + classificador de intenção (chitchat/meta/pergunta)
              evita pipeline em "obrigado", "explica melhor"
```

**Notas do orador (~50s):**
> "RAG sem guardrails é perigoso — LLM pode inventar fonte, responder com
> confiança baseado em chunk irrelevante. Eu construí 5 camadas em ordem
> CRESCENTE de custo: regex de ano custa 1 milissegundo; recusa por escopo
> custa 1 ms; retrieval custa 500ms; rerank custa 200ms; LLM custa 30 segundos.
> Recusar CEDO economiza tokens caros. Cada recusa é registrada com um
> `refusal_reason` específico no log JSONL — abro o log e vejo exatamente
> por que cada query foi recusada. Adicionei recente um classificador de
> intenção: chitchat ('obrigado', 'oi') responde em 0ms sem chamar nada;
> meta-conversa ('explica melhor', 'pode repetir') usa só o histórico, sem
> nova consulta — em 3 segundos em vez de 30."

---

## SLIDE 6 — Métricas (eval suite, 25 queries)

**Tabela:**

| Métrica | Valor |
|---|---|
| Refusal accuracy (off-topic + temporal) | **25/25 = 100%** |
| Doc match em queries factuais | **6/8 = 75%** |
| Reference chunk em top-5 | **4/6 = 67%** |
| Keyword recall (queries factuais) | **48-80%** |
| Latência off-topic (recusa cedo) | **~1 s** |
| Latência fim a fim (real question) | **~30-40 s** |
| Latência meta-conversa (após chitchat fix) | **~3 s** |
| Citação de fonte em respostas válidas | **100%** |

**Notas do orador (~40s):**
> "Sem métrica, qualquer 'melhoria' é vibe-driven. Construí um dataset de 25
> queries-gabarito em 4 categorias: factuais com documento e palavras-chave
> esperados, conceituais, off-topic que devem recusar, e fora-escopo-temporal.
> O eval roda em 8 minutos. Resultado: 100% de refusal accuracy nas off-topic;
> 75% de doc match nas factuais; reference chunk em top-5 67%. Latência
> off-topic é 1 segundo porque recusa cedo; pergunta real leva 30 a 40
> segundos com geração streaming."

---

## SLIDE 7 — Demo ao vivo (~7 min)

**Bullets:**
- URL: https://137-131-141-27.nip.io/
- 7 queries pré-selecionadas (ver DEMO_ROTEIRO.md)
- Cobre: off-topic recusada / coloquial expandida / liderança / regulação
  pessoal / sigla / honestidade quando corpus tem limite

**Notas do orador (~10s antes de abrir o browser):**
> "Vou abrir o agente ao vivo. Deixa eu mostrar 7 queries que cobrem o
> espectro: recusa de off-topic, expansão de coloquial pra termo técnico,
> liderança da agência, sigla expandida via glossário, e — importante —
> uma query onde a resposta é 'não encontrei' porque o corpus 2016-2022
> não cobre. Mostrar honestidade do agente é tão importante quanto mostrar
> capacidade."

**[ABRIR https://137-131-141-27.nip.io/ E SEGUIR DEMO_ROTEIRO.md]**

---

## SLIDE 8 — Decisões notáveis & próximos passos

**Decisões técnicas:**
- **Parent-Child retrieval:** chunks-filho de ~400 tokens pra busca, parents de ~1200 pra LLM
- **RRF (Reciprocal Rank Fusion):** combina vector + BM25 sem viés de escala
- **Glossário coloquial→técnico:** 40 mapeamentos (gato de luz → fraude)
- **Classificador de intenção:** evita pipeline em mensagens conversacionais
- **Citação obrigatória:** LLM forçado a `[FONTE: doc, pg]` em cada afirmação

**Próximos passos (se virasse produto):**
- Pool de conexões Oracle (multi-usuário concorrente)
- Eval expandido pra 200+ queries com human-in-the-loop
- Modo offline com seed dataset (50-100 PDFs) pra reprodução sem credenciais OCI
- Fine-tune do reranker em pares query/chunk de domínio

**Notas do orador (~30s):**
> "Algumas decisões que diferenciam: Parent-Child retrieval — busco no chunk
> pequeno, pego o pai grande pro LLM ter mais contexto. RRF pra combinar
> ranker vetor e ranker BM25 sem normalizar score. Glossário coloquial→técnico
> porque embedding não milagre traduz 'gato de luz' pra 'fraude'. E o
> classificador de intenção que tirou conversas casuais do pipeline. Pra
> virar produto: pool de conexões Oracle, eval com 200+ queries, modo offline
> pra reprodução."

---

## Apêndice (não slide, só pra você ter à mão na Q&A)

Veja **APRESENTACAO.md** seção "Apêndice — perguntas que o avaliador vai
fazer" pra respostas curtas a:

- "E se o corpus tivesse 1M PDFs?"
- "Sua eval só tem 25 queries — como confia?"
- "Por que não usou LangChain / LlamaIndex?"
- "Quanto custa rodar?"
- "Como sabe que não alucina?" → resposta concreta com TRAP 1 (Art. 999 inexistente, agente recusou sem inventar)

---

## Como gerar HTML/PDF dos slides com Marp

```bash
# Instalar Marp CLI (uma vez)
npm install -g @marp-team/marp-cli

# Gerar HTML standalone
marp SLIDES.md --html -o slides.html

# Gerar PDF
marp SLIDES.md --pdf -o slides.pdf

# Servir + auto-reload enquanto edita
marp -s SLIDES.md
```

Se preferir Google Slides ou PowerPoint: copie cada slide manualmente — são
8 só, 5 minutos de trabalho.
