# syntax=docker/dockerfile:1.7
#
# RAG ANEEL — imagem multi-stage com BGE reranker pre-baixado.
# Cold start: ~5s (vs ~3min sem o BGE pre-cacheado).
# Tamanho final: ~3.5 GB (torch CPU + sentence-transformers + bge-reranker-v2-m3).

# ───────── Stage 1: builder ─────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

# Torch CPU-only: a wheel CUDA padrão tem ~3 GB e nada disso é usado aqui
# (rerank roda em CPU, embedding/LLM são via API OCI).
RUN pip install --upgrade pip && \
    pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu && \
    pip install -r requirements.txt

# Pre-baixa o reranker (~600 MB) pra evitar cold start na primeira query.
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=256)"


# ───────── Stage 2: runtime ─────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    HEALTH_HOST=0.0.0.0 \
    HEALTH_PORT=8502

RUN useradd -m -u 1000 rag

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf-cache /opt/hf-cache

COPY scripts/ ./scripts/

RUN chown -R rag:rag /app /opt/hf-cache

USER rag

EXPOSE 8501 8502

# Default: Streamlit. docker-compose sobrescreve pro container do health server.
CMD ["python", "-m", "streamlit", "run", "scripts/app_streamlit.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--server.fileWatcherType=none", \
     "--browser.gatherUsageStats=false"]
