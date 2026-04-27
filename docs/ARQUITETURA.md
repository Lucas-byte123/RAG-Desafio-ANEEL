# 🏗️ Arquitetura — deep dive técnico

Este documento aprofunda o que está no [`README.md`](../README.md) e cobre:

1. [Visão geral em camadas](#1-visão-geral-em-camadas)
2. [Schema do banco Oracle](#2-schema-do-banco-oracle)
3. [Pipeline de ingestão (rodou 1x)](#3-pipeline-de-ingestão-rodou-1x)
4. [Pipeline de runtime (por query)](#4-pipeline-de-runtime-por-query)
5. [Os 5 guardrails detalhados](#5-os-5-guardrails-detalhados)
6. [Classificador de intenção](#6-classificador-de-intenção)
7. [Cache LRU](#7-cache-lru)
8. [Validador de citações](#8-validador-de-citações)
9. [Configurações de tuning](#9-configurações-de-tuning)
10. [Decisões técnicas notáveis](#10-decisões-técnicas-notáveis)

---

## 1. Visão geral em camadas

```
┌─────────────────────────────────────────────────────────────┐
│  CAMADA DE APRESENTAÇÃO                                     │
│  ┌──────────────────────────┐  ┌────────────────────────┐  │
│  │ Streamlit (UI chat)       │  │ FastAPI /health /ready │  │
│  │ porta 8501                │  │ porta 8502             │  │
│  └──────────────────────────┘  └────────────────────────┘  │
│                                                             │
│  CAMADA DE BORDA                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Caddy reverse proxy + Let's Encrypt automático      │   │
│  │ HTTPS obrigatório                                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  CAMADA DE APLICAÇÃO                                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ rag_agent.py — orquestrador (1500 linhas)           │   │
│  │  - classificador intenção (chitchat/meta/real)      │   │
│  │  - 5 guardrails (temporal/escopo/vazio/gap/rerank)  │   │
│  │  - retrieval híbrido (vector + BM25 + RRF)          │   │
│  │  - rerank (bge-reranker-v2-m3 local CPU)            │   │
│  │  - geração (Cohere Command R+ streaming)            │   │
│  │  - validador de citações pós-LLM                    │   │
│  │  - cache LRU de respostas                           │   │
│  │  - logging estruturado JSONL                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  CAMADA DE DADOS                                            │
│  ┌─────────────────┐  ┌─────────────────────────────┐     │
│  │ Oracle 23ai     │  │ OCI Generative AI           │     │
│  │ (Autonomous DB) │  │ (Cohere Embed v3 + R+)      │     │
│  │ HNSW + BM25     │  │ sa-saopaulo-1               │     │
│  └─────────────────┘  └─────────────────────────────┘     │
│                                                             │
│  CAMADA DE OBSERVABILIDADE                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ logs/agent.jsonl — 1 linha por evento, request_id   │   │
│  │ logs/feedback.jsonl — feedback de usuário (futuro)  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Schema do banco Oracle

4 tabelas principais:

### `manifest` — 1 linha por PDF (27.025 linhas)

```sql
CREATE TABLE manifest (
    pdf_id              VARCHAR2(100) PRIMARY KEY,
    registro_titulo     VARCHAR2(500),
    tipo_canonico       VARCHAR2(50),    -- REN, REH, DSP, PRT, DEC, etc
    ano                 NUMBER(4),
    numero              VARCHAR2(50),
    url                 VARCHAR2(500),
    bucket_path         VARCHAR2(500),
    status_download     VARCHAR2(20),    -- success / failed / pending
    tamanho_bytes       NUMBER,
    sha256              VARCHAR2(64),
    download_ts         TIMESTAMP
);
```

### `extractions` — texto extraído por PDF (CLOB JSON)

```sql
CREATE TABLE extractions (
    pdf_id              VARCHAR2(100) PRIMARY KEY,
    extracted_json      CLOB,            -- JSON com pages, hierarchy, tables
    quality_score       NUMBER(3,2),
    extraction_ts       TIMESTAMP,
    FOREIGN KEY (pdf_id) REFERENCES manifest(pdf_id)
);
```

### `chunks` — chunks Parent-Child (~250.000 linhas)

```sql
CREATE TABLE chunks (
    chunk_id            VARCHAR2(220) PRIMARY KEY,
    parent_chunk_id     VARCHAR2(220),   -- nullable, aponta pro pai
    pdf_id              VARCHAR2(100),
    chunk_level         NUMBER(1),       -- 0=parent, 1=child
    chunk_type          VARCHAR2(20),    -- artigo / texto / tabela / cabecalho
    breadcrumb          VARCHAR2(500),   -- "REN 1000/2021 > CAP II > Art. 12"
    page_start          NUMBER(5),
    page_end            NUMBER(5),
    text_raw            CLOB,            -- texto pra LLM (com formatação)
    text_embed          VARCHAR2(4000),  -- texto pra BM25 (limpo)
    ano                 NUMBER(4),
    tipo_canonico       VARCHAR2(50),
    FOREIGN KEY (pdf_id) REFERENCES manifest(pdf_id)
);

-- Índice BM25 do Oracle Text
CREATE INDEX idx_chunks_text ON chunks(text_embed)
    INDEXTYPE IS CTXSYS.CONTEXT;
```

### `chunk_vectors` — embeddings 1024-dim (1:1 com chunks-filho)

```sql
CREATE TABLE chunk_vectors (
    chunk_id    VARCHAR2(220) PRIMARY KEY,
    embedding   VECTOR(1024, FLOAT32),
    embed_ts    TIMESTAMP,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
);

-- Índice HNSW
CREATE VECTOR INDEX idx_vec_hnsw ON chunk_vectors(embedding)
    ORGANIZATION INMEMORY NEIGHBOR GRAPH
    DISTANCE COSINE
    WITH TARGET ACCURACY 95;
```

---

## 3. Pipeline de ingestão (rodou 1x)

Tempos reais na **VM E5.Flex 2 OCPU x86 / 100 GB RAM, Oracle Linux 9**:

| Etapa | Script | Tempo | Output |
|---|---|---|---|
| Build manifest | `build_manifest.py` | ~30s | 27.025 linhas em `manifest` |
| Download | `downloader.py` (httpx async, 5 workers) | ~6h | PDFs no Object Storage; `status_download=success` |
| Extração | `extract_text.py` (PyMuPDF + pdfplumber) | ~8h | JSON em `extractions`, `quality_score` |
| Chunking | `chunker.py` (Parent-Child) | ~1h | ~250k linhas em `chunks` |
| Embedding | `embed_index.py` (Cohere v3 OCI batches=96) | ~3h | 250k linhas em `chunk_vectors` |
| Indexação | `build_indexes.py` | ~2 min | HNSW + Text BM25 criados |

**Total wall-clock: ~18h.**

### Idempotência

Cada script verifica o que já fez via LEFT JOIN ANTI:

```sql
-- Exemplo: embed_index.py só processa chunks SEM embedding
SELECT c.chunk_id FROM chunks c
LEFT JOIN chunk_vectors cv ON cv.chunk_id = c.chunk_id
WHERE cv.chunk_id IS NULL AND c.chunk_level = 1;
```

Se cair no meio, retoma do ponto exato.

### Por que Parent-Child?

- **Filho (~400 tokens)**: unidade de busca. Granular o suficiente pra precisão
  no retrieval (HNSW + BM25 acertam).
- **Pai (~1200 tokens)**: unidade de contexto pro LLM. Grande o suficiente pra
  manter o "espírito" do artigo/seção quando alimenta o prompt.

Tabelas e artigos são **unidades atômicas** — chunker NUNCA corta no meio,
mesmo que estoure o limite de tokens.

---

## 4. Pipeline de runtime (por query)

Sequência exata em `_do_answer_stream` (rag_agent.py:989):

```
1. recebe query (+ history opcional)
2. Cache LRU lookup → se hit, replay tokens em 0ms
3. Classificador intenção (regex):
   - chitchat puro → resposta canned, 0ms, return
   - meta-conversa → LLM com history sem retrieval, ~3s, return
   - real_question → continua
4. Reescrita de follow-up (se há history e palavra de referência)
5. Guardrail temporal (regex de ano)
6. Guardrail escopo (keyword domínio)
7. Extração de filtros (ano único só)
8. Expansão de query (glossário coloquial→técnico, +ANEEL_GLOSSARY siglas)
9. HyDE opcional (se query vaga + has_domain_terms)
10. Embedding query (Cohere v3 via OCI)
11. Vector search HNSW (top-15)
12. BM25 search (top-15)
13. RRF fusion → top-25 candidatos
14. Early-exit por gap semântico (só se BGE indisponível)
15. bge-reranker-v2-m3 (cross-encoder local, top-25 → top-5)
16. Early-exit por rerank score (< 0.02 → recusa)
17. Threshold de confiança via vector_dist
18. _expand_to_parents (1 query batch IN, top-5 children → top-N parents)
19. build_user_prompt (chunks + history + query)
20. Cohere Command R+ streaming (max_tokens=1000)
21. Validação pós-geração: has_citation + invalid_citations
22. _cache_set (LRU 64)
23. yield ("done", resp_final)
```

**Latência típica:** ~30s (90% no LLM streaming).

---

## 5. Os 5 guardrails detalhados

| # | Camada | Sinal | Ação | `refusal_reason` | Custo |
|---|---|---|---|---|---|
| 1 | **Temporal** | Query menciona ano fora de {2016, 2021, 2022} | Recusa com mensagem amigável | `fora_escopo_temporal` | ~1ms |
| 2 | **Escopo** | Query sem termo de domínio + palavras off-topic (futebol, política) | Recusa cedo | `fora_escopo_tematico` | ~1ms |
| 3 | **Retrieval vazio** | Vector + BM25 = 0 resultados | Recusa | `zero_resultados` | ~500ms |
| 4 | **Gap semântico** | top-1 mediano + gap pequeno até top-10 | Recusa (só se BGE indisponível) | `off_topic_provavel` | ~500ms |
| 5a | **Rerank score** | bge score top-1 < 0.02 | Recusa | `off_topic_rerank` | ~700ms |
| 5b | **Citação ausente** | Resposta gerada sem `[FONTE: ...]` | Marca aviso | `baixa_confianca` | ~30s |

**Ordem deliberada:** mais barato primeiro. Recusar cedo (regex 1ms) economiza
tokens caros (LLM 30s).

Cada `refusal_reason` é registrado em `logs/agent.jsonl` para auditoria.

---

## 6. Classificador de intenção

Antes do pipeline pesado, regex categoriza a mensagem:

```
intent = classify_intent(query, has_history)

CHITCHAT  ← saudação / agradecimento / OK / despedida
            (mensagens curtas, padrões fixos)
            → resposta canned, 0ms, sem LLM

META      ← "explica melhor", "pode repetir", "elabore"
            (só se há histórico E não menciona termo de domínio)
            → LLM com history, sem retrieval, ~3s

REAL      ← qualquer outra coisa
            → pipeline completo, ~30s
```

Códigos relevantes em `rag_agent.py:223-310` (`_CHITCHAT_PATTERNS`,
`_META_PATTERNS`, `classify_intent`, `chitchat_response`).

---

## 7. Cache LRU

`OrderedDict[64]` thread-safe com lock dedicado. Chave:

```python
cache_key = f"{query.lower().strip()}|{md5(history[-8:])[:8]}"
```

Hit retorna `AgentResponse` deepcopy + replay tokens em chunks de 80 chars
(mantém UX de streaming na UI).

**Importante:** mesma query com history DIFERENTE = cache miss (proposital,
porque meta-conversa precisa do contexto atual).

CLI test confirmado: 30547ms → 0ms (164898x speedup).

---

## 8. Validador de citações

Pós-LLM, captura cada `[FONTE: doc, pgX]` da resposta via regex e verifica
se o `doc` corresponde a algum chunk nas `sources` recuperadas.

```python
invalid = validate_citations(answer, sources)
# → ["REN 1000/2021"] se citou mas não recuperou
```

Tolerante a variações: "REN 1000/2021" bate com
"REN - RESOLUÇÃO NORMATIVA 1000/2021". Citações sem padrão `TIPO NUM/ANO`
são ignoradas (não validáveis).

Captura **alucinação real** de fonte — diferente do `has_citation` que só
verifica se a regex `[FONTE:` existe.

---

## 9. Configurações de tuning

Constantes em `rag_agent.py:41-53`:

| Constante | Valor | Significado |
|---|---|---|
| `ANOS_COBERTOS` | {2016, 2021, 2022} | Anos do corpus |
| `DIST_THRESHOLD_NO_CONFIDENCE` | 0.62 | Acima disso (cosine dist): recusa pós-rerank |
| `DIST_TOP1_OFFTOPIC` | 0.50 | Top-1 vetor pior que isso → suspeito |
| `GAP_THRESHOLD` | 0.05 | Gap top1→top10 < isso + top1 ruim → off-topic |
| `RERANK_OFFTOPIC_THRESHOLD` | 0.02 | Score BGE < isso → off-topic |
| `VECTOR_K` | 15 | Top-K vector search |
| `BM25_K` | 15 | Top-K BM25 search |
| `RRF_K_CONST` | 60 | Constante k do RRF |
| `RERANK_TOP_N` | 5 | Quantos chunks vão pro LLM após rerank |
| `RERANK_INPUT_K` | 25 | Quantos candidatos o reranker processa |
| `MAX_CONTEXT_CHARS` | 20000 | Budget de chars no prompt do LLM |

Calibração: subi `MAX_CONTEXT_CHARS` 12000→20000 e `max_tokens` 500→1000
após observar respostas truncadas. `RERANK_OFFTOPIC_THRESHOLD` foi de
0.05→0.02 após eval mostrar que estava recusando válidos.

---

## 10. Decisões técnicas notáveis

### Por que Parent-Child em vez de chunks fixos?
Em legislação, a unidade de busca é o ARTIGO (~400 tokens), mas a unidade
de COMPREENSÃO é a SEÇÃO (~1200 tokens). Parent-Child faz a separação certa.

### Por que RRF em vez de weighted sum?
RRF não precisa normalizar scores entre vector e BM25 (escalas diferentes).
Constante k=60 é o default da literatura.

### Por que bge-reranker LOCAL em CPU?
Cohere Rerank em sa-saopaulo-1 só está disponível em Dedicated Cluster
(US$ 5/h fixo). bge-reranker-v2-m3 é multilíngue SOTA, ~600 MB, 150ms em CPU,
custo zero.

### Por que VM em vez de serverless?
BGE precisa carregar 600 MB em RAM. Serverless teria cold start de ~5s a
cada query fria. Numa VM fica quente em memória pra sempre.

### Por que Streamlit em vez de FastAPI + React?
ROI por hora de dev: 200 linhas de Python entregam chat + sidebar + sources
expansíveis com SSE streaming. Pra MVP, é a escolha certa. Em produto real
trocaria por Next.js + FastAPI.

### Por que cache LRU em vez de Redis?
- 64 entradas em OrderedDict ocupam <100 KB de RAM
- Sem dependência externa
- Reset no restart é OK (cache é otimização, não correção)
- Em produto real, trocaria por Redis com TTL

### Por que glossário coloquial em código vs banco?
- 60+ mappings em dict Python: lookup O(1), zero latência
- Não muda em runtime
- Versionável via git
- Em produto real com glossário gigante, migraria pra tabela

---

## Referências

- [Oracle 23ai Vector Search docs](https://docs.oracle.com/en/database/oracle/oracle-database/23/vecse/index.html)
- [Cohere Embed v3](https://docs.cohere.com/docs/embed-v3)
- [Cohere Command R+](https://docs.cohere.com/docs/command-r-plus)
- [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [Reciprocal Rank Fusion (RRF) paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
- [Streamlit chat docs](https://docs.streamlit.io/library/api-reference/chat)
