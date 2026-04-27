"""Inspeciona chunks de um PDF."""

import os
import sys
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"


def main():
    pdf_id = sys.argv[1] if len(sys.argv) > 1 else None
    save = "--save" in sys.argv

    pwd = os.environ["DB_ADMIN_PASS"]
    conn = oracledb.connect(
        user="ADMIN", password=pwd, dsn="aneelrag_medium",
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS_FILE.read_text().strip(),
    )
    cur = conn.cursor()

    if not pdf_id:
        cur.execute("""
            SELECT pdf_id, COUNT(*),
                   SUM(CASE WHEN chunk_level=0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN chunk_level=1 THEN 1 ELSE 0 END)
            FROM chunks GROUP BY pdf_id
            ORDER BY MAX(ordem_doc) DESC FETCH FIRST 10 ROWS ONLY
        """)
        for pid, total, parents, kids in cur.fetchall():
            print(f"  {pid[:60]:60s} total={total:3d} parents={parents:2d} kids={kids:2d}")
        return

    cur.execute("""
        SELECT chunk_id, parent_chunk_id, chunk_level, chunk_type, ordem_doc,
               page_start, page_end, breadcrumb, num_chars, has_table, text_embed, text_raw
        FROM chunks WHERE pdf_id = :pid ORDER BY ordem_doc
    """, {"pid": pdf_id})

    out_lines = []
    for row in cur.fetchall():
        cid, pid2, lvl, ctype, ordem, ps, pe, bc, nc, has_tbl, te, tr = row
        te_str = te[:200] if te else "(None)"
        tr_str = tr.read() if tr else ""
        header = f"\n{'='*72}\n#{ordem} L{lvl} {ctype}  pg={ps}-{pe}  chars={nc}  tabela={has_tbl}\n  id={cid}\n  parent={pid2}\n  breadcrumb: {bc}\n  embed: {te_str}\n---RAW---\n{tr_str[:800]}\n"
        out_lines.append(header)
        if not save:
            print(header)
            if len(tr_str) > 800:
                print(f"[... truncated, total {len(tr_str)} chars]")

    if save:
        out = ROOT / "inspect" / f"chunks_{pdf_id}.txt"
        out.parent.mkdir(exist_ok=True)
        out.write_text("".join(out_lines), encoding="utf-8")
        print(f"Salvo em {out}")

    conn.close()


if __name__ == "__main__":
    main()
