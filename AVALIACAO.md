# 🎯 Guia rápido para o avaliador

Este documento é pra **você que está avaliando o projeto** sem necessariamente
clonar o repo nem rodar nada localmente.

**Tempo estimado de avaliação:** 5-15 min, dependendo do nível de detalhe.

---

## 1. Avaliação em 2 minutos (sem clonar nada)

**Acesse a demo ao vivo:** https://137-131-141-27.nip.io/

Sugestões de queries pra avaliar:

| Query | O que demonstra |
|---|---|
| Clique em **"O que é a tarifa branca?"** | Resposta com múltiplas fontes citadas via `[FONTE: ...]` |
| Clique em **"Quem é o Diretor-Geral da ANEEL?"** | System prompt refinado (decreto de nomeação interpretado corretamente) |
| Digite **"qual a altura do neymar?"** | Guardrail de escopo recusa em ~1s |
| Digite **"obrigado!"** | Classificador de intenção responde em 0ms (sem chamar pipeline) |
| Faça uma pergunta real, depois **"explica melhor"** | Meta-conversa usa só histórico (~3s, sem nova consulta à base) |

**Health endpoint** (status JSON em tempo real):
https://137-131-141-27.nip.io/health

Mostra latência de Oracle, latência de OCI Generative AI, número de vetores
no banco, status do reranker BGE.

---

## 2. Avaliação em 10 minutos (sem clonar nada)

Além do item 1, leia:

- **[README.md](README.md)** — visão geral, arquitetura, stack, métricas, como
  rodar. ~380 linhas, 5 min de leitura.
- **[scripts/eval_dataset.py](scripts/eval_dataset.py)** — 25 queries-gabarito
  curadas em 4 categorias (factual, conceitual, off-topic, fora-escopo-temporal).
- **[scripts/eval_runner.py](scripts/eval_runner.py)** — eval automatizado que
  roda contra essas 25 queries e mede refusal accuracy, doc match, latência.

**Métricas atuais** (eval suite, 25 queries):

| Métrica | Valor |
|---|---|
| Refusal accuracy (off-topic + temporal) | **25/25 = 100%** |
| Doc match em queries factuais | **6/8 = 75%** |
| Reference chunk em top-5 | **4/6 = 67%** |
| Citação de fonte em respostas válidas | **100%** |
| Latência off-topic (recusa cedo) | **~1 s** |
| Latência fim a fim (real question) | **~30-40 s** |
| Latência meta-conversa (chitchat fix) | **~3 s** |

---

## 3. Avaliação em 30 minutos (com clone)

Se quiser explorar o código:

```bash
git clone https://github.com/Lucas-byte123/RAG-Desafio-ANEEL.git
cd RAG-Desafio-ANEEL
```

**Arquivos chave** (ordem de importância):
- `scripts/rag_agent.py` (1500 linhas) — coração do agente: classificador de
  intenção, 5 guardrails, retrieval híbrido (vector + BM25 + RRF +
  bge-reranker), cache LRU, system prompt, streaming
- `scripts/app_streamlit.py` — UI com sugestões clicáveis, status diferenciado
  por intenção, fontes expansíveis
- `scripts/extract_text.py` + `scripts/chunker.py` — pipeline de ingestão
  estrutural com Parent-Child
- `scripts/eval_runner.py` + `scripts/eval_dataset.py` — eval reproduzível
- `DEPLOY.md` — runbook de produção (Caddy + systemd hardened + SELinux)

**Importante:** rodar **com banco populado** exige credenciais OCI próprias
(Autonomous Database 23ai com 250k vetores + chave de OCI Generative AI).
Pra avaliar funcionalidade, use a **demo ao vivo** acima.

### 3.1 — Comandos pra rodar SEM credenciais OCI (apenas validação de código)

```bash
# Smoke test de sintaxe + estrutura (não precisa de credenciais)
python -m py_compile scripts/rag_agent.py
python -m py_compile scripts/app_streamlit.py
python -m py_compile scripts/eval_runner.py

# Verifica que o eval dataset tem 25 queries em 4 categorias
python -c "
import sys; sys.path.insert(0, 'scripts')
from eval_dataset import EVAL_DATASET
from collections import Counter
print(f'Total: {len(EVAL_DATASET)} queries')
print('Por categoria:', dict(Counter(q['category'] for q in EVAL_DATASET)))
"
# Categorias possíveis: factual_numerica, definicao, processo, borderline, off_topic

# Verifica CI rodando no GitHub
# https://github.com/Lucas-byte123/RAG-Desafio-ANEEL/actions
```

### 3.2 — Comandos pra rodar COM credenciais OCI (validação completa)

Pré-requisitos:
- Python 3.11+
- Oracle Wallet do Autonomous DB descompactado em `.secrets/wallet/`
- Senha do wallet em `.secrets/wallet.pass`
- Config OCI em `~/.oci/config` (gerada por `oci setup config`)
- Banco ATP populado (rodar pipeline de ingestão — ~18h, ver README seção
  "Rebuildar o banco do zero")

```bash
# 1. Setup ambiente (~5 min)
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# 2. Setup variáveis
cp .env.example .env
# editar .env: setar DB_ADMIN_PASS=...

# 3. Health check (verifica conexão Oracle + OCI GenAI)
source .env
python scripts/health_check.py
# Output esperado: status=ok com latências de Oracle e OCI

# 4. Eval automático (25 queries, ~8 min, gera inspect/eval_results.json)
python scripts/eval_runner.py
# Output esperado:
# - Refusal accuracy: 25/25 (100%)
# - Doc match factual: 6/8 (75%)
# - Reference chunk top-5: 4/6 (67%)
# - Keyword recall: 48-80% por query
# - Latência média off-topic: ~1s
# - Latência média factual: ~30s

# 5. Query individual via CLI
python scripts/rag_agent.py "Quem é o Diretor-Geral da ANEEL?"

# 6. UI Streamlit local
python -m streamlit run scripts/app_streamlit.py
# Abre em http://localhost:8501

# 7. Inspecionar logs estruturados (qualquer momento)
tail -f logs/agent.jsonl | python -m json.tool
```

### 3.3 — Rodar via Docker (sem instalar Python local)

```bash
# Pré-requisito: mesmas credenciais OCI da seção 3.2
# .secrets/wallet/, .secrets/wallet.pass, ~/.oci/config

docker compose up --build
# Build inicial: ~10 min (torch CPU + bge cache pré-baixado)
# Subsequentes: ~30s

# Streamlit:        http://localhost:8501
# Health JSON:      http://localhost:8502/health
```

---

## 4. Pontos a observar na avaliação

### 4.1 — RAG com qualidade

- **Citação obrigatória:** todas as respostas válidas têm `[FONTE: doc, pg]`.
  Validação pós-geração via regex; se faltou, marca aviso.
- **Retrieval híbrido:** vector search HNSW + BM25 do Oracle Text + Reciprocal
  Rank Fusion + rerank com bge-reranker-v2-m3 local.
- **Parent-Child retrieval:** chunks-filho de ~400 tokens são indexados pra
  busca; chunks-pai de ~1200 tokens vão pro LLM. Precisão de retrieval +
  contexto de geração.

### 4.2 — Honestidade do agente (queries-trap)

- **"qual o conteúdo do artigo 999 da REN 1000/2021"** — artigo inexistente.
  Agente recusa sem inventar.
- **"diretor-geral da ANEEL em 1995"** — fora do corpus temporal. Recusa em 0ms.
- **"multa de R$17.483 à CEMIG em 2015"** — número específico em ano fora do
  corpus. Recusa em 0ms.

### 4.3 — UX consciente

- **Classificador de intenção** evita pipeline de 30s em mensagens
  conversacionais ("obrigado", "explica melhor"). Resposta em 0-3s.
- **Sugestões clicáveis** na tela inicial.
- **Status visual diferenciado** por tipo de mensagem (🔍 buscando, 💬
  conversa, 💬 baseado no histórico).

### 4.4 — Operação

- **Deploy hardened:** systemd com `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`. SELinux Enforcing.
- **HTTPS automático** via Caddy + Let's Encrypt.
- **Logging JSONL estruturado** por `request_id` em `logs/agent.jsonl`.
- **Health endpoint** `/health` `/ready` em FastAPI.
- **CI no GitHub Actions** (smoke test sintaxe + imports + secrets check).
- **Dockerfile multi-stage** com BGE pré-cacheado (cold start ~5s vs ~3min).

---

## 5. Limitações conhecidas (transparência)

Coisas que eu **NÃO** entreguei e que ficariam pra v2:

- **Pool de conexões Oracle** — hoje usa 1 conexão única; com 2+ usuários
  simultâneos pode dar erro de cursor (oracledb não é thread-safe na mesma
  conn).
- **Eval expandido** — 25 queries é baseline. Pra produção precisaria 200+
  queries e human-in-the-loop pra calibrar.
- **Modo offline / seed dataset** — quem clona precisa de credenciais OCI
  próprias. Não há subset de demo no repo.
- **Frontend de produção** — Streamlit é ótimo pra MVP, mas pra produto
  trocaria por Next.js + FastAPI.
- **Refactor:** `_do_answer` e `_do_answer_stream` são clones com 4
  divergências; documentado no roadmap, não bloqueia uso.

---

## 6. Como entrar em contato

- **Repo:** https://github.com/Lucas-byte123/RAG-Desafio-ANEEL
- **Demo ao vivo:** https://137-131-141-27.nip.io/ (válida até 2026-05-25)
- **Issues:** abrir issue no GitHub se encontrar bug ou tiver sugestão
