"""
inspect_extraction.py — imprime uma extração (markdown ou JSON) pra revisão visual.

Uso:
    python scripts/inspect_extraction.py                           # lista últimas 10 extrações
    python scripts/inspect_extraction.py <pdf_id>                  # mostra markdown
    python scripts/inspect_extraction.py <pdf_id> --json           # mostra JSON estruturado
    python scripts/inspect_extraction.py <pdf_id> --save           # salva em ./inspect/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"
INSPECT_DIR = ROOT / "inspect"

DSN = "aneelrag_medium"
USER = "ADMIN"


def connect():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS.")
    return oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS_FILE.read_text().strip(),
    )


def main():
    args = sys.argv[1:]
    show_json = "--json" in args
    save = "--save" in args
    pdf_ids = [a for a in args if not a.startswith("--")]

    conn = connect()
    cur = conn.cursor()

    if not pdf_ids:
        print("=== Últimas 10 extrações ===")
        cur.execute("""
            SELECT pdf_id, num_pages, num_blocks, num_articles, num_tables,
                   num_suspicious_tables, quality_score, needs_docling
            FROM extractions
            WHERE last_error IS NULL
            ORDER BY extracted_at DESC
            FETCH FIRST 10 ROWS ONLY
        """)
        for row in cur.fetchall():
            pid, np_, nb, na, nt, ns, q, nd = row
            flag = "->DOCLING" if nd else ""
            print(f"  {pid[:50]:50s} p={np_} b={nb} art={na} t={nt} susp={ns} q={q:.2f} {flag}")
        conn.close()
        return

    for pdf_id in pdf_ids:
        cur.execute("""
            SELECT extracted_markdown, extracted_json,
                   num_pages, num_blocks, num_articles, num_tables,
                   quality_score, needs_docling, header_pattern, footer_pattern
            FROM extractions WHERE pdf_id = :pid
        """, {"pid": pdf_id})
        r = cur.fetchone()
        if not r:
            print(f"[!] {pdf_id} não encontrado.")
            continue
        md_clob, js_clob, np_, nb, na, nt, q, nd, hp, fp = r
        md = md_clob.read() if md_clob else ""
        js = js_clob.read() if js_clob else ""

        print(f"\n{'='*72}")
        print(f"  {pdf_id}")
        print(f"{'='*72}")
        print(f"  pages={np_} blocks={nb} artigos={na} tabelas={nt} quality={q:.2f} "
              f"{'[->DOCLING]' if nd else ''}")
        print(f"  header removido: {repr(hp)}")
        print(f"  footer removido: {repr(fp)}")
        print()

        content = js if show_json else md
        if save:
            INSPECT_DIR.mkdir(exist_ok=True)
            ext = "json" if show_json else "md"
            out_path = INSPECT_DIR / f"{pdf_id}.{ext}"
            out_path.write_text(content, encoding="utf-8")
            print(f"  salvo em {out_path}")
        else:
            if show_json:
                parsed = json.loads(content)
                print(json.dumps(parsed, indent=2, ensure_ascii=False)[:5000])
                if len(content) > 5000:
                    print(f"\n... (truncated, total {len(content):,} chars — use --save pra ver tudo)")
            else:
                print(content[:8000])
                if len(content) > 8000:
                    print(f"\n... (truncated, total {len(content):,} chars — use --save pra ver tudo)")

    conn.close()


if __name__ == "__main__":
    main()
