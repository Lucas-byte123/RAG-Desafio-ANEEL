#!/bin/bash
# supervisor.sh — reinicia o downloader.py se ele cair, e para quando 0 PDFs pendentes.
# Variáveis de ambiente requeridas: DB_ADMIN_PASS, WALLET_PASS, OCI_NAMESPACE, CONCURRENCY

LOG=/tmp/download.log
ITER=0

while true; do
    ITER=$((ITER + 1))
    echo "[$(date '+%F %T')] === supervisor: iniciando iteração $ITER ===" >> $LOG
    python3 /home/opc/downloader.py >> $LOG 2>&1
    EXIT=$?
    echo "[$(date '+%F %T')] === supervisor: downloader exit=$EXIT ===" >> $LOG

    # Verifica quantos PDFs ainda estão pendentes
    PENDING=$(python3 - <<'PY' 2>/dev/null
import os, oracledb
conn = oracledb.connect(
    user="ADMIN",
    password=os.environ["DB_ADMIN_PASS"],
    dsn="aneelrag_medium",
    config_dir="/home/opc/wallet",
    wallet_location="/home/opc/wallet",
    wallet_password=os.environ["WALLET_PASS"],
)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM manifest WHERE status_download != 'success'")
print(cur.fetchone()[0])
conn.close()
PY
)

    echo "[$(date '+%F %T')] supervisor: pending=$PENDING" >> $LOG
    if [ "$PENDING" = "0" ]; then
        echo "[$(date '+%F %T')] supervisor: TODOS BAIXADOS, saindo do loop." >> $LOG
        break
    fi

    # Limite de segurança: para se já passou 50 iterações (loop infinito provavelmente)
    if [ $ITER -gt 50 ]; then
        echo "[$(date '+%F %T')] supervisor: 50+ iterações, parando preventivamente." >> $LOG
        break
    fi

    echo "[$(date '+%F %T')] supervisor: aguardando 30s antes de retry..." >> $LOG
    sleep 30
done
