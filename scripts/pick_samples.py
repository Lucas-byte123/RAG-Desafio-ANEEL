"""
pick_samples.py — seleciona PDFs representativos pra validar extração.

Escolhe 10 PDFs variados: 2 RENs grandes, 2 NTs, 1 Edital, 1 Despacho grande, 4 mistos.
Output: imprime pdf_ids separados por espaço (pronto pra passar pro extract_text.py).
"""

import os
import sys
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"


def connect():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS.")
    return oracledb.connect(
        user="ADMIN", password=pwd, dsn="aneelrag_medium",
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=WALLET_PASS_FILE.read_text().strip(),
    )


PLAN = [
    ("TEXTO_INTEGRAL",    3, "corpo legal principal (hierarquia formal)"),
    ("NOTA_TECNICA",      2, "tabelas numéricas densas"),
    ("ANEXO",             1, "tabelas especiais / material anexo"),
    ("VOTO",              2, "decisões de diretores (texto livre + citações)"),
    ("DECISAO",           1, "decisões"),
    ("DECISAO_JUDICIAL",  1, "edge case estruturado"),
]


def main():
    conn = connect()
    cur = conn.cursor()

    # Primeiro descobre os tipos realmente presentes
    cur.execute("""
        SELECT tipo_canonico, COUNT(*)
        FROM manifest
        WHERE status_download = 'success' AND file_bytes > 200000
        GROUP BY tipo_canonico
        ORDER BY COUNT(*) DESC
    """)
    print("Tipos disponíveis (PDFs > 200KB, downloaded ok):")
    available_tipos = {}
    for t, n in cur.fetchall():
        print(f"  {t:30s} {n:,}")
        available_tipos[t] = n
    print()

    all_pdf_ids = []
    for tipo, n_want, desc in PLAN:
        if tipo not in available_tipos:
            print(f"[skip] {tipo}: não há PDFs desse tipo")
            continue
        cur.execute("""
            SELECT pdf_id, file_bytes, pdf_pages, registro_titulo
            FROM manifest
            WHERE tipo_canonico = :tipo
              AND status_download = 'success'
              AND file_bytes > 200000
              AND pdf_pages >= 5
            ORDER BY DBMS_RANDOM.VALUE
            FETCH FIRST :n ROWS ONLY
        """, {"tipo": tipo, "n": n_want})
        rows = cur.fetchall()
        print(f"[{tipo}] {desc}")
        for pdf_id, fb, pp, titulo in rows:
            titulo_short = (titulo or "")[:60]
            print(f"  {pdf_id[:50]:50s} {fb//1024:>5}KB p={pp} {titulo_short}")
            all_pdf_ids.append(pdf_id)

    print("\n=== PDF_IDs selecionados (passar pro extract_text.py): ===")
    print(" ".join(all_pdf_ids))

    # Salva lista pra facilitar
    out = ROOT / "inspect" / "sample_pdf_ids.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(all_pdf_ids), encoding="utf-8")
    print(f"\n[salvos em {out}]")

    conn.close()


if __name__ == "__main__":
    main()
