# 🐛 Troubleshooting

Problemas comuns ao rodar o RAG ANEEL e como resolver.

**Antes de tudo:** ter Python 3.11+ instalado, rodar `make smoke` pra
validar que o código compila sem credenciais.

---

## 1. Erros de instalação

### `pip install` baixa torch CUDA (2 GB) e meu disco lota

**Causa:** o `requirements.txt` lista `torch==2.11.0`, e o pip por padrão
pega a wheel CUDA. Mas o agente roda **CPU-only** (rerank em CPU, embedding/LLM
via API).

**Fix:** instale torch via wheel CPU-only **primeiro**:

```bash
pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Ou simplesmente rode `make install` que já faz isso.

### `ModuleNotFoundError: No module named 'oracledb'`

**Causa:** dependências não instaladas ou venv não ativado.

**Fix:**
```bash
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### `python3.11: command not found`

**Causa:** sistema sem Python 3.11.

**Fix Ubuntu/Debian:**
```bash
sudo apt install python3.11 python3.11-venv
```

**Fix macOS (Homebrew):**
```bash
brew install python@3.11
```

**Fix Windows:** baixe instalador em https://www.python.org/downloads/

---

## 2. Erros de credenciais Oracle

### `DPY-3015: invalid wallet password`

**Causa:** arquivo `.secrets/wallet.pass` está com senha errada ou ausente.

**Fix:**
```bash
echo "<senha-do-wallet>" > .secrets/wallet.pass
chmod 600 .secrets/wallet.pass
```

### `ORA-12154: TNS:could not resolve the connect identifier`

**Causa:** wallet não descompactado em `.secrets/wallet/` ou DSN errado.

**Fix:**
1. Confirma estrutura:
   ```
   .secrets/wallet/
   ├── cwallet.sso
   ├── ewallet.p12
   ├── tnsnames.ora
   └── sqlnet.ora
   ```
2. O DSN esperado é `aneelrag_medium` (definido em `rag_agent.py:38`).
   Se seu wallet usar nome diferente, edite o arquivo `tnsnames.ora` ou a
   constante `DSN`.

### `oracledb.exceptions.DatabaseError: ORA-01017: invalid username/password`

**Causa:** `DB_ADMIN_PASS` errado ou ausente.

**Fix:**
```bash
cp .env.example .env
# Edite .env e defina DB_ADMIN_PASS=<senha-do-ADMIN-do-ATP>
source .env
```

### `ORA-12506: TNS:listener rejected connection based on service ACL filtering`

**Causa:** seu IP não está na ACL do Autonomous DB.

**Fix:** acesse o console OCI → Autonomous DB → Network → Add Access Control
Rule → adicione seu IP público (descubra com `curl ifconfig.me`).

---

## 3. Erros de OCI Generative AI

### `oci.exceptions.ConfigFileNotFound: ~/.oci/config not found`

**Causa:** OCI CLI não configurada.

**Fix:**
```bash
oci setup config
# responde tenancy OCID, user OCID, escolhe RSA-2048, etc.
```

### `oci.exceptions.ServiceError: 401 - NotAuthenticated`

**Causa:** chave privada referenciada em `~/.oci/config` está errada ou expirada.

**Fix:**
1. Confira o caminho em `~/.oci/config` campo `key_file=`
2. Confira se a public key correspondente está cadastrada em
   OCI Console → User Settings → API Keys

### `Cohere model not found in compartment`

**Causa:** OCI Generative AI **não está disponível na sua região** (sa-saopaulo-1
tem, mas só recentemente — outras regiões podem não ter).

**Fix:** verifica em Console OCI → Generative AI → Models. Se não aparecer
nenhum modelo Cohere, mude pra região onde está disponível (us-chicago-1,
eu-frankfurt-1, ou sa-saopaulo-1).

---

## 4. Erros do bge-reranker

### Primeira query demora ~3 minutos

**Causa:** primeira execução baixa o modelo `bge-reranker-v2-m3` (~600 MB)
do Hugging Face. Subsequentes são cache hit.

**Fix:** aguarde uma vez. Pre-aqueça antes de demos importantes:

```bash
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=256)"
```

### `OSError: [Errno 28] No space left on device` durante carregamento BGE

**Causa:** `~/.cache/huggingface/` cheio.

**Fix:** mova cache pra disco com mais espaço:
```bash
export HF_HOME=/path/com/espaco
```

---

## 5. Streamlit / UI

### "Connection refused" em `localhost:8501`

**Causa:** Streamlit não subiu, ou subiu numa porta diferente.

**Fix:** veja o output do `make run` — ele mostra a URL exata. Se subir em
`0.0.0.0` mas não em `localhost`, force:

```bash
streamlit run scripts/app_streamlit.py --server.address=127.0.0.1
```

### UI sobe mas todas as queries retornam erro

**Causa:** credenciais OCI/Oracle ausentes — UI sobe sem pré-checagem.

**Fix:** rode `make health` antes de subir UI. Ele retorna OK só se Oracle
e OCI Generative AI estão respondendo.

---

## 6. Deploy em produção (Caddy + systemd)

### `502 Bad Gateway` no nip.io

**Causa:** Streamlit não está rodando.

**Fix na VM:**
```bash
sudo systemctl status rag-streamlit
sudo systemctl restart rag-streamlit
sudo journalctl -u rag-streamlit -n 100 --no-pager
```

### Caddy não consegue obter cert Let's Encrypt

**Causa:** porta 80/443 bloqueada no Security List da subnet OCI.

**Fix:** Console OCI → VCN → Security Lists → Ingress → adicionar regras
TCP 80 e 443 abertas pro mundo.

### SELinux bloqueia escrita em `logs/agent.jsonl`

**Causa:** contexto SELinux do diretório `logs/` errado.

**Fix:**
```bash
sudo chcon -R -t var_log_t /home/opc/rag-aneel/logs/
```

---

## 7. Eval

### `eval_runner.py` falha com `KeyError: 'expected_doc_pattern'`

**Causa:** corpo da query no `eval_dataset.py` foi editado e perdeu campo.

**Fix:** todas as queries `factual_*` precisam de `expected_doc_pattern` e
`expected_keywords`. Veja exemplo em `scripts/eval_dataset.py`.

### Eval termina mas `inspect/eval_results.json` está vazio

**Causa:** diretório `inspect/` não existe.

**Fix:**
```bash
mkdir -p inspect
make eval
```

---

## 8. Performance / latência

### Cada query leva >40 segundos

**Causa esperada:** ~30s é normal — gargalo é o LLM (Cohere R+ streaming).
~10s a mais pode ser BGE cold start ou rede lenta pro OCI.

**Diagnóstico:**
```bash
make logs | grep stream_complete
# olhe campo "elapsed_ms" — quebra entre embedding/retrieval/rerank/llm
```

**Otimização:** use `make health` antes de demos pra confirmar latências
de Oracle e OCI estão normais (~100ms cada).

### Cache não está pegando

**Cache LRU é por `(query_normalizada, hash_histórico)`.** Mesma query com
histórico DIFERENTE = cache miss. Isso é proposital — meta-conversa precisa
do histórico atual.

Pra forçar cache hit numa demo: limpe a conversa antes de re-rodar a mesma
query.

---

## 9. Não encontrou seu problema?

- Abra issue: https://github.com/Lucas-byte123/RAG-Desafio-ANEEL/issues
- Inclua:
  - Output completo do erro (com traceback)
  - Saída de `make health` (se aplicável)
  - SO + Python version (`python --version`)
  - Últimas 10 linhas de `logs/agent.jsonl` (se aplicável)
