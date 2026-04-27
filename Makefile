# Makefile do RAG ANEEL — comandos one-liner pra desenvolvimento, eval e deploy.
# Uso: make <target>. Sem argumentos = lista de targets.
# Funciona em Linux/Mac/WSL. Windows nativo: usar via Git Bash ou PowerShell + GnuWin.

.DEFAULT_GOAL := help
.PHONY: help smoke install eval health demo run docker docker-down logs clean

# Detecta python: prioriza venv local, depois python3.11, depois python3
PYTHON := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3.11 2>/dev/null || echo python3)

help: ## Lista targets disponíveis
	@echo "RAG ANEEL — comandos disponíveis:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "URL ao vivo: https://137-131-141-27.nip.io/"

smoke: ## Smoke test sem credenciais (sintaxe + estrutura, ~10s)
	@echo "==> py_compile dos scripts críticos"
	@$(PYTHON) -m py_compile scripts/rag_agent.py
	@$(PYTHON) -m py_compile scripts/app_streamlit.py
	@$(PYTHON) -m py_compile scripts/health_server.py
	@$(PYTHON) -m py_compile scripts/eval_runner.py
	@echo "==> verifica eval dataset"
	@$(PYTHON) -c "import sys; sys.path.insert(0, 'scripts'); from eval_dataset import EVAL_DATASET; from collections import Counter; print(f'  Total: {len(EVAL_DATASET)} queries'); print(f'  Categorias:', dict(Counter(q[\"category\"] for q in EVAL_DATASET)))"
	@echo "==> ✓ Smoke OK"

install: ## Cria venv + instala deps (torch CPU-only)
	@test -d .venv || python3.11 -m venv .venv
	@.venv/bin/pip install --upgrade pip
	@.venv/bin/pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
	@.venv/bin/pip install -r requirements.txt
	@echo "==> ✓ Deps instaladas. Próximo: configure .env e rode 'make health'"

health: ## Roda health_check.py (precisa credenciais OCI configuradas)
	@$(PYTHON) scripts/health_check.py

eval: ## Roda eval suite — 25 queries-gabarito (~8 min, precisa credenciais)
	@$(PYTHON) scripts/eval_runner.py

run: ## Sobe Streamlit local em http://localhost:8501 (precisa credenciais)
	@$(PYTHON) -m streamlit run scripts/app_streamlit.py \
		--server.address=127.0.0.1 \
		--server.port=8501 \
		--server.headless=true \
		--browser.gatherUsageStats=false

demo: ## Abre a demo ao vivo no navegador
	@echo "Abrindo https://137-131-141-27.nip.io/ ..."
	@command -v xdg-open > /dev/null && xdg-open https://137-131-141-27.nip.io/ \
		|| command -v open > /dev/null && open https://137-131-141-27.nip.io/ \
		|| command -v start > /dev/null && start https://137-131-141-27.nip.io/ \
		|| echo "Abra manualmente: https://137-131-141-27.nip.io/"

docker: ## Sobe via docker compose (Streamlit + health endpoint)
	@docker compose up --build

docker-down: ## Derruba containers e remove
	@docker compose down -v

logs: ## Acompanha logs estruturados JSONL em tempo real
	@tail -f logs/agent.jsonl 2>/dev/null | $(PYTHON) -m json.tool 2>/dev/null \
		|| echo "logs/agent.jsonl ainda não criado — rode uma query primeiro"

clean: ## Remove caches Python e artefatos temporários
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@rm -f bge_warmup.log
	@echo "==> ✓ Caches limpos"
