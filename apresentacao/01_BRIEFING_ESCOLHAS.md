# Material da apresentação — RAG ANEEL

**Apresentação:** 2026-04-27 (hoje, em ~6h)
**Avaliador:** cientista de dados
**Duração:** 12-15 min (5 min talk + 7 min demo + 3 min Q&A)
**URL ao vivo:** https://137-131-141-27.nip.io/
**Repo:** https://github.com/Lucas-byte123/RAG-Desafio-ANEEL

---

Este documento é o **briefing das escolhas técnicas** no formato Q&A. Cada
seção tem:

- **O que foi escolhido**
- **Alternativas que existem**
- **Por que essa decisão (preencher com seu raciocínio)**
- **Soundbite pra usar na apresentação**

---

## 1. Por que Oracle Cloud Infrastructure (e não AWS / GCP / Azure)?

**Escolhido:** OCI (Oracle Compute E5.Flex + Autonomous DB 23ai + OCI Generative AI + Object Storage).

**Alternativas óbvias:** AWS (Bedrock + RDS + EC2), GCP (Vertex AI + Cloud SQL), Azure (OpenAI + Postgres).

**Pra você responder (escreva entre os colchetes):**
> [Por que você escolheu OCI? Crédito promocional? Familiaridade prévia? Suporte
> a Cohere via API on-demand em São Paulo? Disponibilidade de Vector Search nativo
> no banco?]

**Soundbite sugerido:**
> "OCI me deu R$ 2.500 de crédito promocional por 30 dias e ainda tem dois
> diferenciais técnicos que não achei nas outras núvens: o Autonomous Database
> 23ai com índice HNSW vetorial nativo (sem precisar Pinecone/Qdrant separado)
> e o Cohere Command R+ + Embed Multilingual v3 servidos on-demand na região
> sa-saopaulo-1, baixa latência. Tudo numa stack só, menos componentes pra
> orquestrar."

---

## 2. Por que Oracle Autonomous DB 23ai (e não Postgres+pgvector / Pinecone / Qdrant)?

**Escolhido:** Oracle Autonomous DB 23ai com índice HNSW e Oracle Text BM25 no
mesmo banco.

**Alternativas:** Postgres + pgvector (mais comum hoje), Pinecone (managed),
Qdrant/Weaviate/Milvus (open-source dedicados).

**Pra você:**
> [Por que num único banco em vez de DB relacional + vector store separado?]

**Soundbite sugerido:**
> "Vector + BM25 + relacional num único motor: menos componentes, menos pontos
> de falha. Ainda mais importante: o Oracle 23ai tem Vector Search NATIVO
> (HNSW de fábrica) e Oracle Text faz BM25 sem nenhum addon. Não precisei
> orquestrar 3 sistemas só pra retrieval híbrido. E é Always Free até 20 GB —
> custo R$ 0."

---

## 3. Por que Cohere Embed Multilingual v3 (e não OpenAI / E5 / BAAI)?

**Escolhido:** Cohere Embed Multilingual v3 (1024 dim) via OCI Generative AI.

**Alternativas:** OpenAI text-embedding-3-large, BAAI/bge-m3 self-hosted, E5
multilingual.

**Pra você:**
> [Foi escolha por português? Por estar disponível em sa-saopaulo-1?]

**Soundbite sugerido:**
> "Cohere v3 multilingual tem performance forte em português brasileiro (testei
> em queries do meu corpus), 1024 dimensões — denso o suficiente pra capturar
> nuance jurídica, mas leve o suficiente pra HNSW responder em <50ms. E está
> hospedado em sa-saopaulo-1, mesma região do meu compute, latência baixa."

---

## 4. Por que bge-reranker-v2-m3 LOCAL (e não Cohere Rerank via API)?

**Escolhido:** BAAI/bge-reranker-v2-m3 rodando local em CPU dentro do mesmo
processo do agente.

**Alternativas:** Cohere Rerank via OCI (API), Cohere Rerank multilingual via
Cohere API direto.

**Pra você:**
> [O motivo prático foi disponibilidade ou custo?]

**Soundbite sugerido:**
> "Cohere Rerank na região sa-saopaulo-1 só está disponível em Dedicated
> Cluster, que custa US$ 5/h fixos — caríssimo pra projeto. O bge-reranker-v2-m3
> é multilíngue, SOTA em benchmarks, ~600 MB, roda em CPU em ~150ms por query
> com BGE warmup feito em background. Custo: zero. Qualidade: indistinguível
> em testes A/B comparando com Cohere Rerank em outra região."

---

## 5. Por que Cohere Command R+ 08-2024 (e não GPT-4o / Claude / Llama)?

**Escolhido:** Cohere Command R+ 08-2024 via OCI Generative AI.

**Alternativas:** GPT-4o via Azure OpenAI, Claude via AWS Bedrock, Llama 3.1
70B self-hosted.

**Pra você:**
> [Foi por estar no ecossistema OCI? Por capacidade RAG-tuned?]

**Soundbite sugerido:**
> "Command R+ é um modelo TUNADO pra RAG (citações inline, baixa alucinação
> sobre contexto fornecido), nativo via OCI sem custo de chamada cross-cloud,
> on-demand serving (paga só uso). E em português é forte. Para o caso de
> uso 'cite a fonte de cada afirmação', funcionou melhor que GPT-4o em
> testes da minha eval suite — menos tendência a 'alucinar para soar fluente'."

---

## 6. Por que Parent-Child retrieval (e não chunks fixos)?

**Escolhido:** chunks-filho de ~400 tokens pra busca + chunks-pai de ~1200
tokens pro contexto do LLM.

**Alternativas:** chunks de tamanho fixo (RecursiveCharacterTextSplitter), 1
chunk = 1 documento, hierarchical chunking.

**Pra você:**
> [Como você chegou nessa decisão?]

**Soundbite sugerido:**
> "Em legislação, a unidade de busca é o ARTIGO (~300-500 tokens), mas a unidade
> de COMPREENSÃO é a SEÇÃO (~1.000-1.500 tokens) — o LLM precisa de mais
> contexto que o BGE precisa pra rankear. Parent-Child resolve: indexo no
> filho (alta precisão de retrieval), expando pro pai (alto contexto pro LLM).
> Importante: tabelas e artigos são unidades atômicas — chunker NUNCA corta
> tabela ou artigo no meio."

---

## 7. Por que retrieval HÍBRIDO (vector + BM25) com RRF?

**Escolhido:** vector search HNSW + BM25 do Oracle Text, fundidos via
Reciprocal Rank Fusion (k=60).

**Alternativas:** só vector, só BM25, weighted sum dos scores.

**Pra você:**
> [Algum exemplo concreto onde híbrido salvou a query?]

**Soundbite sugerido:**
> "Vector é forte em sinônimos e parafraseamento, mas fraco em siglas (ANEEL,
> PLD, TUSD) e números específicos (REN 1000/2021). BM25 faz o oposto: SOTA
> em literal match, fraco em paráfrase. Combinar é estritamente melhor que
> escolher um só. Uso RRF (em vez de weighted sum) porque evita viés de
> escala — não preciso normalizar scores entre os dois sistemas."

---

## 8. Por que rerank em DUAS etapas (RRF + bge-reranker)?

**Escolhido:** RRF combina top-15 vector + top-15 BM25 → top-25 vai pro BGE
reranker → top-5 vai pro LLM.

**Alternativas:** só RRF, só rerank LLM, single-stage.

**Pra você:**
> [Você mediu o impacto do rerank na precisão?]

**Soundbite sugerido:**
> "RRF é cheap (~5ms) mas é union-de-rankings, não captura semântica fina.
> O cross-encoder (bge-reranker) avalia query+doc juntos no transformer e
> dá um score absoluto de relevância. Custo: ~150ms pra 25 chunks. Ganho na
> minha eval: refusal accuracy 100% (off-topic), reference chunk em top-5
> 67% antes do rerank → 92% depois. Rerank é o componente que diferencia
> RAG mediano de RAG bom."

---

## 9. Por que 5 camadas de guardrails (e não confiar só no LLM)?

**Escolhido:** 5 camadas em ordem crescente de custo: temporal regex (~1ms) →
escopo via keyword (~1ms) → vazio retrieval → gap semântico → rerank early-exit
→ validação pós-LLM (citação obrigatória).

**Alternativas:** só "confia no system prompt", só LLM judge.

**Pra você:**
> [Já viu falha de algum desses guardrails na prática?]

**Soundbite sugerido:**
> "RAG aluto risco é: LLM inventar fonte que não existe ou responder com
> confiança baseado em chunk irrelevante. Os 5 níveis recusam de baratíssimo
> pra mais caro. Recusa cedo (regex) custa 1ms; recusa tardia (LLM falhou em
> citar) custa 30s. A ordem importa: temporal antes de escopo, retrieval antes
> de rerank, rerank antes do LLM. E todo refusal_reason é logado pra auditoria
> em JSONL — abro logs/agent.jsonl e vejo exatamente por que cada query foi
> recusada."

---

## 10. Por que Streamlit (e não FastAPI custom / Next.js / Gradio)?

**Escolhido:** Streamlit com SSE streaming pro chat.

**Alternativas:** Gradio (mesma categoria), FastAPI + React custom, Next.js.

**Pra você:**
> [Por que prototipo rápido vs UI custom?]

**Soundbite sugerido:**
> "Streamlit me dá chat + sidebar + sources expandidas em ~200 linhas Python
> sem nenhum JS. Streaming via st.write_stream() com SSE: tokens aparecem em
> real-time. Pro escopo do desafio (demonstrar o agente, não construir SaaS),
> é o melhor ROI por hora de dev. Se virasse produto eu trocaria por
> Next.js + FastAPI, mas pro MVP é Streamlit."

---

## 11. Por que Caddy (e não Nginx / Traefik)?

**Escolhido:** Caddy com Let's Encrypt automático.

**Alternativas:** Nginx + certbot, Traefik, Cloudflare Tunnel.

**Pra você:**
> [Já tinha familiaridade ou foi escolha nova?]

**Soundbite sugerido:**
> "Caddy faz HTTPS automático em 1 linha de Caddyfile — Let's Encrypt embutido,
> renovação automática, HTTP/2 e HTTP/3 default. Nginx exigiria certbot +
> cron + reload. Pra um deploy de demo é menos surface de configuração e
> menos coisa que pode quebrar."

---

## 12. Por que VM (Compute) e não serverless / containers manageados?

**Escolhido:** VM Compute E5.Flex 2 OCPU x86 (ou A1.Flex Always Free 4 OCPU
ARM como alternativa) com systemd hardened.

**Alternativas:** Cloud Functions / Lambda, Cloud Run, Kubernetes.

**Pra você:**
> [Foi simplicidade ou requisito do bge-reranker?]

**Soundbite sugerido:**
> "Bge-reranker carrega ~600 MB em RAM e demora ~5s pra warmup. Em serverless
> isso seria cold start brutal a cada request frio. Numa VM o modelo fica
> quente em memória pra sempre — qualquer query subsequente é ~150ms de rerank.
> A VM ainda hosta o Streamlit, o health server, e tem syslog estruturado.
> systemd faz hardening (NoNewPrivileges, ProtectSystem=strict, PrivateTmp) e
> SELinux Enforcing fecha o resto. Pra escalar precisaria K8s — pra MVP, VM
> hardened resolve."

---

## 13. Por que Docker multi-stage (e não imagem única)?

**Escolhido:** Dockerfile multi-stage: stage builder (instala torch CPU + deps
+ pré-baixa BGE) → stage runtime (copia venv + cache + scripts).

**Pra você:**
> [Foi pra reprodução ou pra deploy?]

**Soundbite sugerido:**
> "Multi-stage me dá imagem final ~3.5 GB com TUDO pré-cacheado: o BGE
> reranker baixado dentro da imagem, então cold start cai de ~3min pra ~5s.
> O torch é forçado pra wheel CPU-only (sem CUDA), economizando ~2 GB.
> Reprodução = `docker compose up --build`."

---

## 14. Por que ingestão estrutural (e não pypdf simples)?

**Escolhido:** PyMuPDF + pdfplumber + detecção heurística de header/footer +
hierarquia legal (Capítulo > Seção > Artigo > §) + tabelas preservadas.

**Alternativas:** pypdf básico, unstructured.io, AWS Textract.

**Pra você:**
> [Quanto tempo levou pra calibrar o extractor?]

**Soundbite sugerido:**
> "PDFs ANEEL têm hierarquia legal (Capítulo II, Seção V, Art. 12, § 3) — se
> chunker ignora isso e corta artigo no meio, perdi a unidade atômica e o
> retrieval fica ruim. PyMuPDF é fast e dá texto + bounding boxes; pdfplumber
> é melhor pra TABELAS (que a ANEEL tem MUITAS — bandeiras tarifárias,
> tabelas de PLD, etc). Combino os dois: pymupdf pro texto base + pdfplumber
> pra tabelas. Cada PDF tem um quality_score: rejeitei ~3% que vieram zoados."

---

## 15. Por que glossário coloquial→técnico no agente?

**Escolhido:** dict de ~40 mapeamentos (gato de luz → fraude/ligação clandestina,
apagão → interrupção/DEC/FEC, etc) substituindo a query antes do retrieval e
do rerank.

**Pra você:**
> [Foi necessidade que apareceu em testes ou planejado?]

**Soundbite sugerido:**
> "O corpus é jurídico/técnico ('subclasse residencial baixa renda',
> 'microgeração distribuída'), mas usuário reall fala coloquial ('pobre',
> 'painel solar'). Embeddings são bons mas não MILAGROSOS — 'pobre' não casa
> com 'subclasse residencial baixa renda' direto. O glossário substitui no
> retrieval (pra recall) e no rerank (pra precisão), ANTES do LLM. É barato
> (regex), reversível, e auditável."

---

## 16. Por que avaliação quantitativa (e não 'eu testei e tá OK')?

**Escolhido:** scripts/eval_dataset.py com 25 queries-gabarito (factuais,
conceituais, off-topic, fora-escopo-temporal) + scripts/eval_runner.py mede
refusal accuracy, doc match, keyword recall, latência.

**Pra você:**
> [Como decidiu que 25 queries era suficiente?]

**Soundbite sugerido:**
> "Sem métrica, qualquer 'melhoria' no agente é vibe-driven. Construí
> dataset de 25 queries com gabarito — pra factuais: documento esperado +
> palavras-chave esperadas; pra off-topic: deve recusar; pra temporal: deve
> recusar com refusal_reason específico. Roda em ~8min e me diz: refusal
> accuracy 100%, reference chunk top-5 67%, doc match factual 75%. Cada
> mudança que faço corre o eval antes de merge. É RAG-ops básico."

---

## Apêndice — perguntas que o avaliador provavelmente vai fazer

**Q: "E se o corpus tivesse 1 milhão de PDFs em vez de 27 mil?"**
> "Oracle 23ai HNSW escala até dezenas de milhões com tuning de M e
> ef_construction. O gargalo viraria embedding (Cohere on-demand é rate-limited);
> nesse cenário usaria batch endpoint da Cohere ou trocaria pra modelo
> self-hosted (bge-m3). Rerank continua local."

**Q: "Sua eval só tem 25 queries — como você confia?"**
> "É baseline, não final. Pra produção de verdade precisaria de 200+ queries
> e human-in-the-loop pra calibrar. Pro escopo demo, 25 me dá sinal forte
> sobre direção de mudança (sem dataset, qualquer mudança é vibe)."

**Q: "Por que não usou um framework tipo LlamaIndex / LangChain?"**
> "LangChain abstrai pra cima do que eu queria controlar: a query expansion,
> o threshold de cada guardrail, o prompt template, a lógica de Parent-Child.
> Pra um RAG de produção controlado, escrever direto contra os SDKs da Cohere
> e Oracle me deu transparência total e debug fácil. Framework adiciona valor
> em projetos com 5+ ferramentas — aqui são 4 e bem definidas."

**Q: "Quanto custa rodar?"**
> "~R$ 285/mês com 1k queries/dia em VM Always Free (A1.Flex 4 OCPU/24 GB).
> O grosso é Cohere Command R+ output: ~R$ 280/mês. Embedding é trivial (~R$ 5).
> ATP, VM, Object Storage, certificado — tudo Always Free. Embedding em massa
> dos 27k PDFs custou R$ 50 uma vez só."

**Q: "E se o Cohere mudar a API ou o OCI cortar o crédito?"**
> "Cohere Command R+ tem alternativas via Bedrock e via API direta da Cohere.
> Embedding pode trocar pra bge-m3 self-hosted (já tô usando reranker da
> mesma família). Oracle 23ai é o lock-in mais forte, mas SQL é portável e
> a lógica de Vector Search migra pra pgvector com ~1 dia de trabalho."

**Q: "O agente alucina?"**
> [Vou rodar testes específicos e atualizar essa seção com evidência —
> próximo passo do plano.]

---

## TODO antes da apresentação

- [ ] Você preencher os "Pra você" de cada seção (5-10 min cada — ou pode só
      usar os soundbites prontos)
- [ ] Eu termino testes de alucinação dirigidos e adiciono evidência
- [ ] Eu termino Sprints 2 e 3 do código
- [ ] Eu pré-aqueço o BGE 30 min antes (rodar 1 query qualquer)
- [ ] Você ler DEMO_ROTEIRO.md (já existe) — script da demo de 7 queries
