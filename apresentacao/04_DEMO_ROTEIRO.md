# Roteiro de demo — RAG ANEEL

**Apresentação:** 2026-04-27, faculdade. Avaliador: cientista de dados.
**Duração estimada:** 12-15 min (5 min talk + 7 min demo + 3 min Q&A)
**URL ao vivo:** https://137-131-141-27.nip.io/

---

## 1. Abertura (1 min)

> "O agente é um RAG sobre **27.025 PDFs da legislação ANEEL** dos anos 2016, 2021
> e 2022. Stack inteira em Oracle Cloud — Autonomous Database 23ai com índice
> vetorial HNSW, OCI Generative AI pro Cohere Embed Multilingual e Cohere
> Command R+, e bge-reranker-v2-m3 local pra rerank. UI em Streamlit, deploy
> via systemd hardened atrás de Caddy com HTTPS automático."

**Mostrar tela inicial** + abrir nova aba: https://137-131-141-27.nip.io/health
> "Esse endpoint expõe status em JSON: 250 mil vetores no banco, latência do
> Oracle, latência do OCI Generative AI, e se o reranker BGE tá quente."

---

## 2. Demo ao vivo — 7 queries em ordem (7 min)

### Q1 — Off-topic puro (mostra guardrail de escopo, layer 2)

```
qual a altura do neymar
```

**Esperado:** recusa em ~600 ms (sem chamar LLM), mensagem amigável.

**Falar:**
> "O sistema tem 5 camadas de guardrails. A primeira pré-checa o ano (rejeita
> queries sobre 2024 ou 2010). A segunda pré-checa escopo: se a query não tem
> nenhum termo do domínio elétrico, recusa cedo — sem queimar token de LLM.
> Latência aqui foi 600 ms; queries válidas levam 30-40 s."

---

### Q2 — Coloquial sobre Tarifa Social (mostra query expansion)

```
tem desconto na conta de luz pra quem é pobre?
```

**Esperado:** resposta sobre Tarifa Social Baixa Renda, fonte REN 733/2016 ou
REH 2189/2016, com `[FONTE: ...]`.

**Falar:**
> "Usuário comum não fala 'subclasse residencial baixa renda' — ele fala 'pobre'.
> O sistema tem um glossário coloquial→técnico que **substitui** termos antes do
> retrieval e do rerank. 'Quem é pobre' vira 'baixa renda subclasse residencial
> tarifa social' antes do BGE pontuar relevância."

---

### Q3 — Liderança da ANEEL (mostra refinamento de prompt)

```
quem é o presidente da aneel?
```

**Esperado:** explica que ANEEL tem Diretor-Geral, cita Sandoval de Araújo
Feitosa Neto + André Pepitone, fonte DEC 2022.

**Falar:**
> "A ANEEL não tem 'presidente' — tem Diretor-Geral e 4 Diretores. Mas o usuário
> não sabe disso. Detectamos esse caso testando: o LLM recebia decretos que abrem
> com 'O PRESIDENTE DA REPÚBLICA resolve nomear...' e ficava confuso, achando que
> a pergunta era sobre o Bolsonaro. Reescrevemos o system prompt pra ensinar:
> 'presidente da ANEEL' = 'Diretor-Geral' + 'em decretos de nomeação, o nome
> relevante é o NOMEADO, não o signatário'."

---

### Q4 — Coloquial onde corpus tem limite (mostra honestidade)

```
qual a multa pra quem rouba luz?
```

**Esperado:** ou cita autos de infração contra distribuidoras (rerank ~0.16) com
ressalva, ou diz "Não encontrei informação suficiente".

**Falar:**
> "Aqui o sistema é **honesto**. O glossário expande 'rouba luz' → 'fraude furto
> energia ligação clandestina', mas o corpus 2016/2021/2022 só tem autos de
> infração contra distribuidoras (Light, etc) — a regulação de furto doméstico
> está na REN 414/2010, fora da janela temporal. O agente não inventa: ou diz
> 'não encontrei', ou cita só o que tem com clareza. Isso é desejável: hallucination
> é o pior risco de RAG, e a gente prefere recall menor a precisão falsa."

---

### Q5 — Conceitual factual (mostra qualidade base)

```
o que é microgeração distribuída?
```

**Esperado:** "Microgeração distribuída é a central geradora de energia elétrica
com potência instalada menor ou igual a 75 kW, conectada na rede de distribuição
[FONTE: REN 1000/2021, pg.275]. Pode utilizar fontes renováveis (solar, eólica,
hídrica, biomassa) ou cogeração qualificada [FONTE: REN 1000/2021, pg.275]."

**Atenção — não use "qual a definição de":** testado, retorna chunks pg.41/pg.22
(Capítulo II, Seção X) em vez da pg.275 onde está a definição literal, e o LLM
recusa por não achar match exato. Use "**o que é X?**" — o BGE casa "o que é X"
com chunks que começam por "X é...".

**Falar:**
> "Aqui é o caso 'happy path'. Vector search + BM25, fundidos via Reciprocal Rank
> Fusion, depois rerank do BGE local, depois Cohere Command R+ gera com streaming.
> O Streamlit já mostra os tokens chegando. Olha que ele cita a página exata onde
> está a definição: pg.275 da REN 1000/2021."

---

### Q6 — Reclamação (mostra recuperação de procedimento)

```
como reclamar da distribuidora?
```

**Esperado:** lista bullets sobre Ouvidoria → ANEEL como segunda instância, com
prazos e situações específicas.

**Falar:**
> "Ele não só responde 'reclame na ouvidoria' — ele recupera o procedimento
> completo da REN 1000/2021, com as condições de quando escalar pra ANEEL.
> Isso é a vantagem do Parent-Child retrieval: o chunk filho casa, mas a gente
> expande pro chunk pai antes de mandar pro LLM, então o contexto fica completo."

---

### Q7 — Sigla expandida via glossário (mostra ANEEL_GLOSSARY)

```
o que é PLD?
```

**Esperado:** "PLD é a sigla para Preço de Liquidação de Diferenças
[FONTE: REH 2994/2021, pg.1; REH 2190/2016, pg.1]. O PLD é um valor em R$/MWh...
e tem limites mínimo e máximo..."

**Falar:**
> "O agente tem um glossário interno de siglas ANEEL — quando detecta 'PLD' na
> query, expande pra 'PLD (Preço de Liquidação das Diferenças)' antes do
> retrieval. Embeddings e BM25 são fracos pra siglas curtas isoladas; com a
> expansão, o casamento com os documentos fica trivial. Mesma lógica vale pra
> AIR, REN, REH, CCEE, ONS, TUSD, e mais umas 20 siglas do setor."

**Não usar follow-up multi-turn na demo:** testado em "e quando ele varia muito?"
e a reescrita de pronome não disparou no path da `RAGAgent.answer()` — caiu em
off-topic. O recurso existe no streaming path, mas o comportamento não é estável.
Pra apresentação, mantém **single-turn por pergunta** e use o botão "Limpar
conversa" entre queries.

---

## 3. Discussão técnica (3 min — depende da Q&A)

**Pontos pra puxar conforme o avaliador perguntar:**

### Sobre a arquitetura
- **Por que Oracle 23ai?** Vector + BM25 + relacional num único banco — menos
  componentes, menos pontos de falha. Índice HNSW nativo, 1024 dim.
- **Por que Cohere Multilingual v3?** Português brasileiro forte, 1024 dim,
  hospedado na sa-saopaulo-1 — latência baixa.
- **Por que bge-reranker-v2-m3 local?** Grátis, multilíngue SOTA, ~600 MB,
  roda em CPU, evita custo da API Cohere Rerank em cada query.

### Sobre os guardrails (5 camadas)
1. **Temporal** (regex de ano) — recusa "o que mudou em 2024"
2. **Escopo** (domain terms) — recusa "altura do neymar"
3. **Gap detection pós-retrieval** — top-1 alta distância + gap pequeno = off-topic
4. **Rerank early-exit** — BGE score < 0.02 = off-topic
5. **Validação pós-LLM** — checa se resposta tem `[FONTE: ...]`, marca aviso senão

### Sobre métricas (eval suite, 25 queries)
- Refusal accuracy: **100%** (25/25 recusas corretas)
- Doc match (queries factuais): **75%** (6/8)
- Reference chunk em top-5: **67%** (4/6)
- Latência off-topic: **~1 s** (cedo)
- Latência fim a fim: **~30-40 s** (BGE reranker é gargalo de CPU)

### Sobre operação
- **Replicabilidade:** `Dockerfile` multi-stage com BGE pre-baixado;
  `docker-compose.yml` com volumes pra wallet + OCI config; `.dockerignore`
  exclui segredos. Reproduzir = `docker compose up --build`.
- **Deploy seguro:** systemd hardened (NoNewPrivileges, ProtectSystem=strict,
  PrivateTmp), SELinux Enforcing, basic-auth bcrypt, Caddy + Let's Encrypt
  automático, .env em /etc/ chmod 600.
- **Logs:** JSONL estruturado por request_id (rastreável grep).
- **Custo OCI:** ~R$ 285/mês com 1 k queries/dia (Cohere R+ é o maior peso).

---

## 4. Encerramento (30 s)

> "O repositório tem README de 350 linhas com setup completo, DEPLOY.md de
> 460 linhas com runbook de produção, e demo ao vivo em URL pública. O eval
> suite com 25 queries-gabarito está em scripts/eval_runner.py — podem rodar
> e validar as métricas. Posso compartilhar o repo se quiserem."

---

## 5. Backup — se uma query falhar

**Plano B se Streamlit cair durante a demo:**
- Mostrar `https://137-131-141-27.nip.io/health` (continua funcionando)
- Abrir CLI no terminal: `ssh opc@137.131.141.27 'cd rag-aneel && .venv/bin/python scripts/rag_agent.py "..."'`

**Plano C se internet cair:**
- Mostrar `scripts/eval_runner.py` rodando, métricas saindo no terminal
- Mostrar arquitetura no `README.md` (seção Arquitetura tem ASCII art)

**Plano D se Cohere R+ ficar fora:**
- Health endpoint mostra `oci_genai.ok=false` — apontar e explicar fallback
- Falar sobre os guardrails que continuam funcionando (recusa temporal,
  recusa de escopo, retrieval híbrido) sem LLM

---

## 6. Checklist 30 min antes da apresentação

- [ ] Abrir https://137-131-141-27.nip.io/ e fazer 1 query qualquer pra
      pré-aquecer o BGE (primeira query carrega o modelo do disco, ~5-10 s
      a mais que o normal — depois fica quente)
- [ ] Abrir health endpoint: confirmar `oracle.ok=true` e `oci_genai.ok=true`
- [ ] Limpar histórico do Chrome/Edge pra não vazar prints anteriores
- [ ] Ter o roteiro aberto em outra aba ou no celular
- [ ] Bateria do laptop > 50% e/ou plugado
