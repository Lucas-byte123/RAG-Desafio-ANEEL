"""
downloader.py — baixa os 27.025 PDFs da ANEEL com:
  - httpx async com semáforo (5 conexões padrão)
  - jitter entre 200ms-800ms, backoff exponencial em 429/503/504
  - User-Agent de browser real, Referer plausível
  - verificação tripla: HTTP 200 + sha256 + pypdf abre com pages > 0
  - upload direto pro Object Storage (sem disco)
  - update do manifesto no Autonomous DB em tempo real
  - resume automático: lê só WHERE status_download != 'success'

Variáveis de ambiente requeridas:
    DB_ADMIN_PASS       senha do user ADMIN do Autonomous DB
    WALLET_PASS         senha da wallet
    OCI_NAMESPACE       namespace do Object Storage (ex: grgdsxx4khc6)
    CONCURRENCY         número de workers paralelos (default 5)
    MAX_PDFS            limite p/ teste (default sem limite)

Uso na VM:
    cd ~ && source venv/bin/activate
    export DB_ADMIN_PASS=...
    export WALLET_PASS=...
    export OCI_NAMESPACE=grgdsxx4khc6
    python downloader.py
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import oci
import oracledb
from pypdf import PdfReader

# ---- Config ----
HOME = Path.home()
WALLET_DIR = HOME / "wallet"

DB_USER = "ADMIN"
DB_DSN = "aneelrag_medium"
BUCKET_NAME = "aneel-rag"

CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
MAX_PDFS = int(os.environ.get("MAX_PDFS", "0")) or None  # 0 = sem limite
WATCHDOG_IDLE_SECONDS = int(os.environ.get("WATCHDOG_IDLE_SECONDS", "300"))  # sem progresso por N seg = abort

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/pdf,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://biblioteca.aneel.gov.br/",
}
JITTER_RANGE = (0.2, 0.8)
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
MAX_RETRIES = 5
LOG_EVERY = 50


def env_or_die(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"ERRO: variável de ambiente {name} não definida")
    return v


DB_PASS = env_or_die("DB_ADMIN_PASS")
WALLET_PASS = env_or_die("WALLET_PASS")
NAMESPACE = env_or_die("OCI_NAMESPACE")


def make_oci_client():
    """Object Storage client síncrono — chamado via run_in_executor."""
    config = oci.config.from_file()  # ~/.oci/config
    return oci.object_storage.ObjectStorageClient(config)


def upload_pdf(client, content: bytes, bucket_path: str):
    """Sync upload chamado de thread pool."""
    client.put_object(
        namespace_name=NAMESPACE,
        bucket_name=BUCKET_NAME,
        object_name=bucket_path,
        put_object_body=content,
        content_type="application/pdf",
    )


def verify_pdf_content(content: bytes) -> int:
    """Retorna número de páginas. Lança se inválido/zero."""
    reader = PdfReader(io.BytesIO(content), strict=False)
    n = len(reader.pages)
    if n == 0:
        raise ValueError("PDF tem 0 páginas")
    return n


SQL_SELECT_PENDING = """
    SELECT pdf_id, url, bucket_path
    FROM manifest
    WHERE status_download != 'success'
    ORDER BY ano, pdf_id
"""

SQL_UPDATE = """
    UPDATE manifest SET
        status_download = :status,
        http_status     = :http_status,
        file_bytes      = :file_bytes,
        sha256          = :sha,
        pdf_pages       = :pages,
        last_error      = SUBSTR(:err, 1, 2000),
        attempts        = attempts + 1,
        downloaded_at   = CASE WHEN :status = 'success' THEN :ts ELSE downloaded_at END
    WHERE pdf_id = :pdf_id
"""


RETRYABLE_DB_ERRORS = ("DPY-4011", "DPY-1001", "DPY-1010", "DPY-6005", "DPY-4068")


async def update_status(pool, pdf_id, status, http_status=None,
                        file_bytes=None, sha=None, pages=None, err=None):
    """Atualiza status no DB com retry em erros transitórios de conexão."""
    last_exc = None
    for attempt in range(4):
        try:
            async with pool.acquire() as conn:
                cur = conn.cursor()
                await cur.execute(SQL_UPDATE, {
                    "status": status,
                    "http_status": http_status,
                    "file_bytes": file_bytes,
                    "sha": sha,
                    "pages": pages,
                    "err": err,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "pdf_id": pdf_id,
                })
                await conn.commit()
            return
        except oracledb.DatabaseError as e:
            last_exc = e
            err_str = str(e)
            if any(code in err_str for code in RETRYABLE_DB_ERRORS):
                await asyncio.sleep(0.5 + attempt * 1.5)
                continue
            raise
    raise last_exc if last_exc else RuntimeError("update_status falhou")


async def watchdog(stats, idle_seconds):
    """Mata o processo se não houver progresso (success+failed+invalid) por idle_seconds.
    O supervisor.sh detecta exit != 0 e reinicia, descartando qualquer task asyncio em deadlock."""
    import os as _os
    check_interval = 30
    last_total = -1
    idle_elapsed = 0
    while True:
        await asyncio.sleep(check_interval)
        done = stats["success"] + stats["failed"] + stats["invalid"]
        if done == last_total:
            idle_elapsed += check_interval
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] [WATCHDOG] sem progresso há {idle_elapsed}s "
                  f"(limite: {idle_seconds}s) total={done}", flush=True)
            if idle_elapsed >= idle_seconds:
                print(f"[{ts}] [WATCHDOG] FATAL: {idle_elapsed}s sem progresso, "
                      f"abortando pro supervisor reiniciar.", flush=True)
                _os._exit(1)
        else:
            last_total = done
            idle_elapsed = 0


async def worker(worker_id, queue, http_client, oci_client, pool, stats):
    loop = asyncio.get_running_loop()
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            return
        pdf_id, url, bucket_path = item
        try:
            await asyncio.sleep(random.uniform(*JITTER_RANGE))
            attempt = 0
            success = False
            last_err = None
            while attempt < MAX_RETRIES and not success:
                attempt += 1
                try:
                    r = await http_client.get(url, follow_redirects=True)
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    last_err = f"net: {type(e).__name__}: {e}"
                    backoff = min(60.0, 2 ** attempt) + random.uniform(0, 5)
                    await asyncio.sleep(backoff)
                    continue

                if r.status_code in (429, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    backoff = min(120.0, 2 ** attempt) + random.uniform(0, 5)
                    await asyncio.sleep(backoff)
                    continue

                if r.status_code != 200:
                    await update_status(pool, pdf_id, f"failed_http_{r.status_code}",
                                        http_status=r.status_code, err=f"HTTP {r.status_code}")
                    stats["failed"] += 1
                    break

                content = r.content
                file_bytes = len(content)
                if file_bytes < 100:
                    await update_status(pool, pdf_id, "failed_too_small",
                                        http_status=200, file_bytes=file_bytes,
                                        err=f"arquivo muito pequeno ({file_bytes} bytes)")
                    stats["invalid"] += 1
                    break

                try:
                    pages = verify_pdf_content(content)
                except Exception as e:
                    await update_status(pool, pdf_id, "failed_invalid_pdf",
                                        http_status=200, file_bytes=file_bytes,
                                        err=f"invalid pdf: {e}")
                    stats["invalid"] += 1
                    break

                sha = hashlib.sha256(content).hexdigest()

                try:
                    await loop.run_in_executor(None, upload_pdf, oci_client, content, bucket_path)
                except Exception as e:
                    last_err = f"upload: {e}"
                    backoff = min(30.0, 2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

                await update_status(pool, pdf_id, "success",
                                    http_status=200, file_bytes=file_bytes,
                                    sha=sha, pages=pages)
                stats["success"] += 1
                success = True

                if stats["success"] % LOG_EVERY == 0:
                    elapsed = time.time() - stats["start"]
                    rate = stats["success"] / elapsed
                    eta_sec = (stats["total"] - stats["success"]) / rate if rate > 0 else 0
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] ok={stats['success']:,} falha={stats['failed']:,} "
                          f"inválido={stats['invalid']:,} taxa={rate:.1f}/s "
                          f"eta={eta_sec/60:.0f}min", flush=True)

            if not success and not last_err:
                await update_status(pool, pdf_id, "failed_max_retries",
                                    err=f"esgotadas {MAX_RETRIES} tentativas")
                stats["failed"] += 1
            elif not success:
                await update_status(pool, pdf_id, "failed_max_retries",
                                    err=f"{MAX_RETRIES} tentativas: {last_err}")
                stats["failed"] += 1
        except Exception as e:
            err_str = str(e)
            print(f"[ERRO worker {worker_id} pdf_id={pdf_id}]: {e}", flush=True)
            # Se foi erro de DB, deixa pending pro próximo run em vez de marcar failed
            if any(code in err_str for code in RETRYABLE_DB_ERRORS):
                stats["db_error"] = stats.get("db_error", 0) + 1
            else:
                try:
                    await update_status(pool, pdf_id, "failed_exception",
                                        err=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
                stats["failed"] += 1
        finally:
            queue.task_done()


async def main():
    # Fetch inicial é síncrono pra evitar timeout em 27k rows com async cursor.
    print(f"[INFO] Buscando PDFs pendentes (conexão sync)...")
    sync_conn = oracledb.connect(
        user=DB_USER,
        password=DB_PASS,
        dsn=DB_DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS,
    )
    cur = sync_conn.cursor()
    cur.arraysize = 2000
    cur.prefetchrows = 2001
    sql = SQL_SELECT_PENDING
    if MAX_PDFS:
        sql += f" FETCH FIRST {MAX_PDFS} ROWS ONLY"
    cur.execute(sql)
    rows = cur.fetchall()
    sync_conn.close()
    print(f"[INFO] {len(rows):,} PDFs pendentes")

    print(f"[INFO] Abrindo pool async pra updates ({CONCURRENCY + 2} conexões)...")
    pool = oracledb.create_pool_async(
        user=DB_USER,
        password=DB_PASS,
        dsn=DB_DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS,
        min=2,
        max=CONCURRENCY + 2,
        increment=1,
    )
    if not rows:
        print("[OK] Nada a fazer.")
        await pool.close()
        return

    queue = asyncio.Queue()
    for r in rows:
        await queue.put(r)
    for _ in range(CONCURRENCY):
        await queue.put(None)

    print(f"[INFO] Iniciando {CONCURRENCY} workers...")
    stats = {"success": 0, "failed": 0, "invalid": 0,
             "total": len(rows), "start": time.time()}

    oci_client = make_oci_client()
    watchdog_task = asyncio.create_task(watchdog(stats, WATCHDOG_IDLE_SECONDS))
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS,
                                 http2=False, limits=httpx.Limits(max_connections=CONCURRENCY * 2)) as http_client:
        workers = [
            asyncio.create_task(worker(i, queue, http_client, oci_client, pool, stats))
            for i in range(CONCURRENCY)
        ]
        await asyncio.gather(*workers)
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass

    elapsed = time.time() - stats["start"]
    print(f"\n[FIM] sucesso={stats['success']:,} falha={stats['failed']:,} "
          f"inválido={stats['invalid']:,}")
    print(f"[FIM] tempo: {elapsed/60:.1f} min, "
          f"taxa: {stats['success']/elapsed:.1f}/s")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
