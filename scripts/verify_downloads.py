"""
verify_downloads.py — diagnóstico de integridade pós-download.

Roda 3 camadas de verificação:
  1. Status agregado no DB (success / failed_* / pending)
  2. Estatísticas semânticas (pages, bytes, sha256 distintos, top tipos com falha)
  3. Reconciliação com Object Storage (lista bucket e cruza com DB)

Pré-requisitos:
    - Wallet extraída em .secrets/wallet/
    - Senha da wallet em .secrets/wallet.pass
    - DB_ADMIN_PASS em env var

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/verify_downloads.py
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"
OCI_CONFIG = Path.home() / ".oci" / "config"

DSN = "aneelrag_medium"
USER = "ADMIN"
BUCKET = "aneel-rag"


def fatal(msg: str):
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def hr(title: str):
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def main():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        fatal("defina DB_ADMIN_PASS antes de rodar.")
    if not WALLET_PASS_FILE.exists():
        fatal(f"wallet.pass não encontrado em {WALLET_PASS_FILE}")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()

    print(f"[INFO] Conectando em {DSN}...")
    conn = oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )
    cur = conn.cursor()

    # ---- CAMADA 1: status agregado ----
    hr("CAMADA 1 — Status agregado do download")
    cur.execute("""
        SELECT status_download, COUNT(*) AS n
        FROM manifest
        GROUP BY status_download
        ORDER BY n DESC
    """)
    status_counts = {s: n for s, n in cur.fetchall()}
    total = sum(status_counts.values())
    success = status_counts.get("success", 0)
    pending = status_counts.get("pending", 0)
    failed_total = total - success - pending

    for s, n in sorted(status_counts.items(), key=lambda x: -x[1]):
        pct = 100 * n / total
        bar = "#" * int(pct / 2)
        print(f"  {s:26s} {n:>7,}  {pct:5.1f}%  {bar}")
    print(f"  {'TOTAL':26s} {total:>7,}")
    print(f"\n  Resumo: {success:,} ok | {failed_total:,} falhas | {pending:,} pendentes")

    # ---- CAMADA 2: integridade semântica ----
    hr("CAMADA 2 — Integridade semântica dos PDFs baixados")

    cur.execute("""
        SELECT MIN(pdf_pages), MAX(pdf_pages), AVG(pdf_pages),
               MIN(file_bytes), MAX(file_bytes), AVG(file_bytes)
        FROM manifest WHERE status_download = 'success'
    """)
    r = cur.fetchone()
    if r and r[0] is not None:
        min_p, max_p, avg_p, min_b, max_b, avg_b = r
        print(f"  Páginas: min={min_p}  max={max_p}  média={avg_p:.1f}")
        print(f"  Bytes:   min={min_b:,}  max={max_b:,}  média={avg_b:,.0f}")
    else:
        print("  (sem linhas success — pulando)")

    cur.execute("""
        SELECT COUNT(*) FROM manifest
        WHERE status_download = 'success' AND pdf_pages = 1
    """)
    (single_page,) = cur.fetchone()
    print(f"  PDFs de 1 página (potenciais formulários vazios): {single_page:,}")

    cur.execute("""
        SELECT COUNT(DISTINCT sha256), COUNT(*)
        FROM manifest
        WHERE status_download = 'success'
    """)
    uniq_sha, total_sha = cur.fetchone()
    dup_sha = total_sha - uniq_sha
    print(f"  sha256 únicos: {uniq_sha:,} de {total_sha:,}  (duplicados por conteúdo: {dup_sha:,})")

    cur.execute("""
        SELECT sha256, COUNT(*) AS n
        FROM manifest
        WHERE status_download = 'success' AND sha256 IS NOT NULL
        GROUP BY sha256 HAVING COUNT(*) > 1
        ORDER BY n DESC FETCH FIRST 5 ROWS ONLY
    """)
    top_dups = cur.fetchall()
    if top_dups:
        print(f"  Top sha256 duplicados (mesmo conteúdo em múltiplas URLs):")
        for sha, n in top_dups:
            print(f"    {sha[:12]}...  x{n}")

    if failed_total > 0:
        print(f"\n  Breakdown de falhas:")
        cur.execute("""
            SELECT status_download, COUNT(*) FROM manifest
            WHERE status_download LIKE 'failed%'
            GROUP BY status_download ORDER BY COUNT(*) DESC
        """)
        for s, n in cur.fetchall():
            print(f"    {s:30s} {n:>6,}")

        print(f"\n  Top 5 erros observados:")
        cur.execute("""
            SELECT SUBSTR(last_error, 1, 80) AS err, COUNT(*) AS n
            FROM manifest
            WHERE status_download LIKE 'failed%' AND last_error IS NOT NULL
            GROUP BY SUBSTR(last_error, 1, 80)
            ORDER BY n DESC FETCH FIRST 5 ROWS ONLY
        """)
        for err, n in cur.fetchall():
            print(f"    x{n:<5}  {err}")

    print(f"\n  Sucesso por ano:")
    cur.execute("""
        SELECT ano,
               SUM(CASE WHEN status_download='success' THEN 1 ELSE 0 END) AS ok,
               COUNT(*) AS tot
        FROM manifest GROUP BY ano ORDER BY ano
    """)
    for ano, ok, tot in cur.fetchall():
        pct = 100 * ok / tot if tot else 0
        print(f"    {ano}: {ok:,}/{tot:,}  ({pct:.1f}%)")

    # ---- CAMADA 3: reconciliação com Object Storage ----
    hr("CAMADA 3 — Reconciliação com Object Storage")

    try:
        import oci
        if not OCI_CONFIG.exists():
            print(f"  (pulando: {OCI_CONFIG} não existe)")
        else:
            cfg = oci.config.from_file()
            osc = oci.object_storage.ObjectStorageClient(cfg)
            ns_resp = osc.get_namespace()
            namespace = ns_resp.data
            print(f"  Namespace: {namespace}  |  Bucket: {BUCKET}")
            print(f"  Listando bucket (pode levar 1-3 min p/ 27k objetos)...")

            t0 = time.time()
            bucket_shas = {}
            bucket_paths = set()
            next_start = None
            page = 0
            while True:
                resp = osc.list_objects(
                    namespace_name=namespace,
                    bucket_name=BUCKET,
                    fields="name,size,md5",
                    limit=1000,
                    start=next_start,
                )
                for o in resp.data.objects:
                    bucket_paths.add(o.name)
                next_start = resp.data.next_start_with
                page += 1
                if not next_start:
                    break
                if page % 5 == 0:
                    print(f"    ...{len(bucket_paths):,} objetos listados")
            elapsed = time.time() - t0
            print(f"  [OK] {len(bucket_paths):,} objetos no bucket ({elapsed:.1f}s)")

            cur.execute("""
                SELECT bucket_path FROM manifest
                WHERE status_download = 'success'
            """)
            db_paths = {row[0] for row in cur.fetchall()}

            in_db_not_bucket = db_paths - bucket_paths
            in_bucket_not_db = bucket_paths - db_paths

            print(f"\n  DB diz success: {len(db_paths):,}")
            print(f"  Bucket tem:     {len(bucket_paths):,}")
            print(f"  No DB mas não no bucket: {len(in_db_not_bucket):,}  {'<- PROBLEMA' if in_db_not_bucket else 'OK'}")
            print(f"  No bucket mas não no DB: {len(in_bucket_not_db):,}  (esperado: manifest/ + extras)")

            if in_db_not_bucket:
                print(f"\n  Primeiros 10 missing:")
                for p in list(in_db_not_bucket)[:10]:
                    print(f"    {p}")

            if in_bucket_not_db:
                extras = [p for p in in_bucket_not_db if not p.startswith("manifest/")]
                if extras:
                    print(f"  Extras no bucket (não em manifest/): {len(extras):,}")
                    for p in extras[:5]:
                        print(f"    {p}")

    except ImportError:
        print("  (pulando: biblioteca 'oci' não instalada localmente)")
    except Exception as e:
        print(f"  [ERRO] reconciliação falhou: {type(e).__name__}: {e}")

    # ---- VEREDITO ----
    hr("VEREDITO")
    target = 27025
    if success == target and pending == 0 and failed_total == 0:
        print(f"  [PERFEITO] {success:,}/{target:,} baixados, 0 falhas, 0 pendentes.")
    elif pending > 0:
        print(f"  [INCOMPLETO] {pending:,} ainda pending. Rodar supervisor.sh na VM de novo.")
    elif failed_total > 0 and success >= target * 0.97:
        print(f"  [OK COM RESSALVAS] {success:,} ok, {failed_total:,} falharam "
              f"(urls mortas/formato errado — aceitável)")
    else:
        print(f"  [REVISAR] {success:,} ok, {failed_total:,} falhas, {pending:,} pendentes")

    conn.close()


if __name__ == "__main__":
    main()
