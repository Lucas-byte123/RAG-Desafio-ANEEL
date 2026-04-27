#!/usr/bin/env bash
# RAG ANEEL — script de bootstrap pra Oracle Compute (Linux)
# Uso na VM:
#   chmod +x start.sh
#   ./start.sh install   # primeira vez (cria venv, instala deps)
#   ./start.sh run       # iniciar streamlit em foreground
#   ./start.sh health    # rodar health_check no Oracle
#   ./start.sh eval      # rodar eval suite (25 queries)
#
# Para deploy de producao com HTTPS + auth + systemd hardening: ver DEPLOY.md

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3.11}"
VENV="$ROOT/.venv"

cmd="${1:-help}"

case "$cmd" in
  install)
    echo "==> Criando virtualenv em $VENV"
    "$PYTHON" -m venv "$VENV"
    source "$VENV/bin/activate"
    echo "==> Instalando deps"
    pip install --upgrade pip
    # Torch CPU-only (rerank é CPU; wheel CUDA padrão são ~2 GB inúteis)
    pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
    pip install -r "$ROOT/requirements.txt"

    echo "==> Pre-baixando bge-reranker-v2-m3 (~600 MB) em background..."
    # warm-up: baixa o modelo agora, em vez de na 1a query
    nohup python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=256)" > "$ROOT/bge_warmup.log" 2>&1 &
    echo "    log: $ROOT/bge_warmup.log"

    echo ""
    echo "==> Pronto. Proximos passos:"
    echo "    1. cp .env.example .env  &&  edite com a senha do DB"
    echo "    2. Coloque o wallet OCI em .secrets/wallet/ + .secrets/wallet.pass"
    echo "    3. Configure ~/.oci/config (oci setup config)"
    echo "    4. ./start.sh run"
    ;;

  run)
    source "$VENV/bin/activate"
    if [ -f "$ROOT/.env" ]; then
      echo "==> Carregando .env"
      set -a; source "$ROOT/.env"; set +a
    fi
    if [ -z "${DB_ADMIN_PASS:-}" ]; then
      echo "ERRO: DB_ADMIN_PASS nao definido. Edite .env" >&2
      exit 1
    fi
    cd "$ROOT"
    echo "==> Iniciando Streamlit em 0.0.0.0:8501"
    exec python -m streamlit run scripts/app_streamlit.py \
      --server.address=0.0.0.0 \
      --server.port=8501 \
      --server.headless=true \
      --server.fileWatcherType=none \
      --browser.gatherUsageStats=false
    ;;

  service)
    cat <<EOF
==> 'service' foi descontinuado.

   O deploy de producao usa 2 servicos systemd hardened (rag-streamlit + rag-health)
   atras de Caddy reverse proxy com HTTPS automatico, basic auth bcrypt, .env em
   /etc com chmod 600, e contextos SELinux corretos.

   Veja o passo a passo completo em: DEPLOY.md
EOF
    exit 1
    ;;

  health)
    source "$VENV/bin/activate"
    if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
    cd "$ROOT"
    python scripts/health_check.py
    ;;

  eval)
    source "$VENV/bin/activate"
    if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
    cd "$ROOT"
    python scripts/eval_runner.py
    ;;

  help|*)
    cat <<EOF
RAG ANEEL — comandos:
  ./start.sh install   Cria venv, instala deps, pre-baixa bge
  ./start.sh run       Roda Streamlit em foreground (porta 8501)
  ./start.sh health    Roda health_check no Oracle
  ./start.sh eval      Roda eval suite (25 queries)

Para deploy seguro em producao (HTTPS + auth + systemd hardening):
  ver DEPLOY.md
EOF
    ;;
esac
