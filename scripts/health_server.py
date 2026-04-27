"""health_server.py — endpoint HTTP /health pra load-balancer/monitoring.

Roda em paralelo ao Streamlit (porta separada). Resposta JSON:
{
  "status": "ok" | "degraded" | "down",
  "oracle": {"ok": bool, "latency_ms": int, "error": str|null},
  "oci_genai": {"ok": bool, "latency_ms": int, "error": str|null},
  "bge_warm": bool,
  "uptime_s": int,
  "version": "..."
}

Uso:
    python scripts/health_server.py
    curl http://localhost:8502/health
    curl http://localhost:8502/ready    # 200 se tudo ok, 503 senao
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI, Response
import oracledb

VERSION = "1.0.0"
START_TIME = time.time()
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"
DSN = "aneelrag_medium"
USER = "ADMIN"

app = FastAPI(title="RAG ANEEL Health", version=VERSION)


def check_oracle() -> dict:
    t0 = time.time()
    try:
        conn = oracledb.connect(
            user=USER,
            password=os.environ["DB_ADMIN_PASS"],
            dsn=DSN,
            config_dir=str(WALLET_DIR),
            wallet_location=str(WALLET_DIR),
            wallet_password=WALLET_PASS_FILE.read_text().strip(),
        )
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM chunk_vectors")
            (n,) = cur.fetchone()
        finally:
            cur.close()
        conn.close()
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                "vectors": n, "error": None}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "vectors": None, "error": f"{type(e).__name__}: {e}"}


def check_oci_genai() -> dict:
    t0 = time.time()
    try:
        import oci
        cfg = oci.config.from_file()
        cfg["region"] = os.environ.get("OCI_REGION", "sa-saopaulo-1")
        client = oci.generative_ai.GenerativeAiClient(cfg)
        # ping leve: lista modelos do compartment
        models = client.list_models(compartment_id=cfg["tenancy"]).data.items
        active = sum(1 for m in models if m.lifecycle_state == "ACTIVE")
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                "active_models": active, "error": None}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "active_models": None, "error": f"{type(e).__name__}: {e}"}


def check_bge_warm() -> bool:
    """Bge ja foi carregado em memoria? (sem forcar carregamento aqui)."""
    try:
        import rag_agent
        return rag_agent._bge_reranker_cache is not None
    except Exception:
        return False


@app.get("/health")
def health():
    oracle = check_oracle()
    oci_genai = check_oci_genai()
    bge_warm = check_bge_warm()

    if oracle["ok"] and oci_genai["ok"]:
        status = "ok"
    elif oracle["ok"] or oci_genai["ok"]:
        status = "degraded"
    else:
        status = "down"

    return {
        "status": status,
        "oracle": oracle,
        "oci_genai": oci_genai,
        "bge_warm": bge_warm,
        "uptime_s": int(time.time() - START_TIME),
        "version": VERSION,
    }


@app.get("/ready")
def ready(response: Response):
    """Liveness probe: 200 se ambas dependencias OK, 503 senao."""
    h = health()
    if h["status"] == "ok":
        return {"ready": True}
    response.status_code = 503
    return {"ready": False, "status": h["status"]}


@app.get("/")
def root():
    return {"service": "RAG ANEEL Health", "endpoints": ["/health", "/ready"]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("HEALTH_PORT", "8502"))
    # Default 127.0.0.1: produção atrás de proxy reverso (Caddy/nginx) é o caso comum
    # e mais seguro. Docker/dev definem HEALTH_HOST=0.0.0.0 explicitamente.
    host = os.environ.get("HEALTH_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")
