# Deploy seguro em produção (Oracle Cloud)

Guia completo pra subir o agente RAG numa VM Oracle Compute com HTTPS,
autenticação, SELinux Enforcing e systemd hardening.

**Stack final:**
```
Internet ─[443/80]─► Caddy (HTTPS + Basic Auth + Let's Encrypt)
                       │
                       ├─► 127.0.0.1:8501  Streamlit  (rag-streamlit.service)
                       └─► 127.0.0.1:8502  Health     (rag-health.service)
```

Apenas portas **22, 80, 443** ficam abertas externamente. Streamlit e Health
ficam **bindados em 127.0.0.1** — defesa em profundidade.

---

## 0. Pré-requisitos

- VM Oracle Compute (testado em **Oracle Linux 9.7**, qualquer shape ≥ 1 OCPU / 6 GB RAM)
- Domínio (ou usar **nip.io** grátis: `<IP-com-tracos>.nip.io`)
- Acesso `sudo` na VM
- Pipeline de ingestão já rodado (Oracle ATP populado — ver `README.md` § "Rebuildar do zero")

---

## 1. Security List (firewall OCI)

Apenas SSH/HTTP/HTTPS abertas externamente. ICMP pra Path MTU Discovery.

`.secrets/security-list-rules.json` (template):

```json
[
  { "description": "SSH",
    "protocol": "6", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": false,
    "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}} },
  { "description": "HTTP (Let's Encrypt + redirect)",
    "protocol": "6", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": false,
    "tcpOptions": {"destinationPortRange": {"min": 80, "max": 80}} },
  { "description": "HTTPS (Caddy)",
    "protocol": "6", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": false,
    "tcpOptions": {"destinationPortRange": {"min": 443, "max": 443}} },
  { "description": "ICMP Path MTU",
    "protocol": "1", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": false,
    "icmpOptions": {"type": 3, "code": 4} }
]
```

Aplicar:

```bash
oci network security-list update \
  --security-list-id <OCID> \
  --ingress-security-rules file://.secrets/security-list-rules.json \
  --force
```

---

## 2. firewalld na VM

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all   # verificar: services: dhcpv6-client http https ssh
```

---

## 3. Instalar runtime

```bash
# Python 3.11 (Oracle Linux 9 vem com 3.9 — não basta)
sudo dnf install -y python3.11 python3.11-devel python3.11-pip

# Caddy via COPR
sudo dnf install -y dnf-command\(copr\)
sudo dnf copr enable -y @caddy/caddy
sudo dnf install -y caddy
```

---

## 4. Subir o app

```bash
# Como user opc
mkdir -p ~/rag-aneel && cd ~/rag-aneel
# scp do projeto inteiro (ou git clone) pra cá

bash start.sh install   # cria .venv, instala deps, pre-baixa bge-reranker (~600 MB)
```

Copiar wallet OCI:

```bash
mkdir -p .secrets/wallet
# unzip Wallet_aneelrag.zip pra .secrets/wallet/
echo "<senha-do-wallet>" > .secrets/wallet.pass
chmod 600 .secrets/wallet.pass
```

Configurar OCI CLI (`~/.oci/config` + `~/.oci/oci_api_key.pem`):

```bash
oci setup config
# ou: scp ~/.oci do seu local pra VM
```

---

## 5. Credenciais em /etc (NÃO em /home)

> ⚠️ **Não use `/home/opc/rag-aneel/.env`** — SELinux bloqueia systemd
> de ler arquivos com contexto `user_home_t`. Veja Troubleshooting § "Permission denied".

```bash
sudo bash -c 'umask 077; cat > /etc/rag-aneel.env <<EOF
DB_ADMIN_PASS=<senha-do-ADMIN-do-ATP>
HF_HOME=/home/opc/.cache/huggingface
EOF'
sudo chown root:root /etc/rag-aneel.env
sudo chmod 600 /etc/rag-aneel.env
sudo restorecon -v /etc/rag-aneel.env
```

---

## 6. systemd units (com hardening)

`/etc/systemd/system/rag-streamlit.service`:

```ini
[Unit]
Description=RAG ANEEL - Streamlit UI (localhost-only, behind Caddy)
After=network.target

[Service]
Type=simple
User=opc
Group=opc
WorkingDirectory=/home/opc/rag-aneel
EnvironmentFile=/etc/rag-aneel.env
ExecStart=/home/opc/rag-aneel/.venv/bin/python -m streamlit run scripts/app_streamlit.py \
  --server.address=127.0.0.1 --server.port=8501 --server.headless=true \
  --server.fileWatcherType=none --browser.gatherUsageStats=false \
  --server.enableXsrfProtection=false --server.enableCORS=false
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/opc/rag-aneel
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/rag-health.service`:

```ini
[Unit]
Description=RAG ANEEL - Health Endpoint (localhost-only, behind Caddy)
After=network.target

[Service]
Type=simple
User=opc
Group=opc
WorkingDirectory=/home/opc/rag-aneel
EnvironmentFile=/etc/rag-aneel.env
Environment=HEALTH_PORT=8502
ExecStart=/home/opc/rag-aneel/.venv/bin/python scripts/health_server.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/opc/rag-aneel
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
```

Notas:
- `enableXsrfProtection=false` + `enableCORS=false`: necessário porque Streamlit
  roda atrás de proxy reverso. CSRF é mitigado pelo Basic Auth no Caddy.
- `ProtectHome=read-only` + `ReadWritePaths=/home/opc/rag-aneel`: home invisível
  exceto o diretório do app.
- Logs vão pro **journald** (não pra arquivo) pra evitar problemas de SELinux
  ao escrever em `/home`.

Aplicar contextos SELinux corretos:

```bash
sudo restorecon -v /etc/systemd/system/rag-streamlit.service /etc/systemd/system/rag-health.service
sudo chcon -R -t bin_t /home/opc/rag-aneel/.venv/bin/   # libera systemd executar python do venv
sudo systemctl daemon-reload
```

---

## 7. Caddy

Gerar password basic auth (via stdin pra **não vazar** em `ps -ef` nem em
`~/.bash_history`):

```bash
PWD=$(openssl rand -base64 18)
HASH=$(printf %s "$PWD" | caddy hash-password)
echo "Password: $PWD"
echo "Hash: $HASH"
unset PWD   # remover da memoria do shell apos copiar
```

`/etc/caddy/Caddyfile` (substitua o domínio e o hash):

```
{
    email admin@<seu-dominio>
}

<seu-dominio> {
    encode gzip

    # Health publico (sem auth)
    handle /health* { reverse_proxy 127.0.0.1:8502 }
    handle /ready*  { reverse_proxy 127.0.0.1:8502 }

    # UI Streamlit - basic auth
    handle {
        basic_auth {
            rag <HASH-bcrypt-do-passo-acima>
        }
        reverse_proxy 127.0.0.1:8501
    }

    log {
        output file /var/log/caddy/access.log {
            roll_size 50mb
            roll_keep 5
        }
        format json
    }
}
```

> ⚠️ Escreva o Caddyfile **localmente** e use `scp` pra mandar pra VM.
> Heredoc via SSH come os caracteres `$` do hash bcrypt e gera erro `illegal base64`.

```bash
# do seu local:
scp Caddyfile opc@VM:/tmp/
ssh opc@VM 'sudo mv /tmp/Caddyfile /etc/caddy/Caddyfile && sudo restorecon /etc/caddy/Caddyfile'
```

Logs do Caddy:

```bash
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy
sudo restorecon -Rv /var/log/caddy
```

Validar antes de subir:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

---

## 8. SELinux: bool de proxy

```bash
sudo setsebool -P httpd_can_network_connect on
sudo getsebool httpd_can_network_connect   # → on
```

---

## 9. Subir tudo

```bash
sudo systemctl enable --now rag-health rag-streamlit caddy

# Status
sudo systemctl is-active rag-health rag-streamlit caddy
# → active active active

# Verificar bind correto
sudo ss -tlnp | grep -E ":(80|443|8501|8502)"
# 127.0.0.1:8501 → streamlit
# 127.0.0.1:8502 → health
# *:80, *:443    → caddy

# Smoke test local
curl http://127.0.0.1:8502/health
```

Smoke test externo (esperar 30s pra Let's Encrypt provisionar cert):

```bash
curl https://<seu-dominio>/health           # JSON, sem auth
curl -u rag:<senha> https://<seu-dominio>/  # 200
curl -I http://VM-IP:8501                   # timeout (porta fechada externa) ✓
```

---

## 10. Troubleshooting

### `Failed to load environment files: Permission denied`
SELinux bloqueia systemd de ler `EnvironmentFile` em `/home`.
**Solução:** mover `.env` pra `/etc/rag-aneel.env` (root:root, 600) e rodar
`sudo restorecon /etc/rag-aneel.env`.

### `Failed to set up standard output: Permission denied`
SELinux bloqueia systemd de escrever logs em `/home`.
**Solução:** trocar `StandardOutput=append:/home/...` por `StandardOutput=journal`.
Ler logs com `sudo journalctl -u rag-streamlit -f`.

### `Failed to locate executable .../venv/bin/python: Permission denied`
SELinux bloqueia exec de binários em `/home` (contexto `user_home_t`).
**Solução:**
```bash
sudo chcon -R -t bin_t /home/opc/rag-aneel/.venv/bin/
```

### `Unit rag-X.service does not exist` mesmo com arquivo presente
Arquivo unit foi copiado de `/tmp` e ficou com contexto `user_tmp_t`.
**Solução:**
```bash
sudo chown root:root /etc/systemd/system/rag-*.service
sudo restorecon -v /etc/systemd/system/rag-*.service
sudo systemctl daemon-reload
```

### Caddy: `open /etc/caddy/Caddyfile: permission denied`
Caddyfile herdou contexto `user_tmp_t` ao ser movido de `/tmp`.
**Solução:** `sudo restorecon -Rv /etc/caddy/`.

### Caddy: `open /var/log/caddy/access.log: permission denied`
Diretório existe mas sem contexto correto, ou arquivo foi criado por root.
**Solução:**
```bash
sudo rm -f /var/log/caddy/access.log
sudo chown -R caddy:caddy /var/log/caddy
sudo restorecon -Rv /var/log/caddy
```

### `caddy hash-password` com erro `illegal base64 data at input byte 2`
Hash bcrypt enviado por heredoc SSH teve `$` interpolados pelo shell remoto.
**Solução:** escrever Caddyfile local, usar `scp`, depois `sudo mv`.

### `No space left on device` no rsync/cp
VM Always Free Ampere A1 tem só ~46 GB. PyTorch + CUDA libs ocupam ~10 GB.
**Solução:** evitar duplicar `.venv`. Usar `chcon` no diretório original em vez
de copiar pra `/opt`.

### Cert Let's Encrypt não provisiona
1. Verificar DNS: `dig +short <seu-dominio>` deve retornar o IP da VM.
2. Verificar porta 80 acessível externamente: `curl http://<dominio>/.well-known/acme-challenge/test`.
3. Logs: `sudo journalctl -u caddy --since "5 min ago"`.

---

## 11. Manutenção

```bash
# Reiniciar serviços
sudo systemctl restart rag-streamlit rag-health caddy

# Ver logs ao vivo
sudo journalctl -u rag-streamlit -f
sudo journalctl -u rag-health -f
sudo journalctl -u caddy -f
tail -f /var/log/caddy/access.log

# Atualizar código
cd ~/rag-aneel
git pull   # ou rsync do local
sudo systemctl restart rag-streamlit rag-health

# Trocar senha do basic auth
NEW=$(openssl rand -base64 18)
HASH=$(printf %s "$NEW" | caddy hash-password)
sudo sed -i "s|rag .*|rag $HASH|" /etc/caddy/Caddyfile
sudo systemctl reload caddy
echo "Nova senha: $NEW"
unset NEW

# Rotacionar senha do DB (após apresentação)
# 1. Trocar via OCI Console em Autonomous DB → DB Connection → Reset password
# 2. sudo sed -i 's/^DB_ADMIN_PASS=.*/DB_ADMIN_PASS=NOVA/' /etc/rag-aneel.env
# 3. sudo systemctl restart rag-streamlit rag-health
```

---

## 12. Checklist final de segurança

- [ ] Portas externas: apenas 22, 80, 443 (verificar com nmap externo)
- [ ] Streamlit/Health bind 127.0.0.1 (não 0.0.0.0)
- [ ] `/etc/rag-aneel.env` com `chmod 600` e owner `root:root`
- [ ] Wallet OCI fora do git (`.gitignore`)
- [ ] Basic auth com bcrypt cost ≥ 12
- [ ] Cert Let's Encrypt válido (`openssl s_client -connect dominio:443 < /dev/null | openssl x509 -dates`)
- [ ] SELinux Enforcing (`getenforce` → `Enforcing`)
- [ ] systemd units com `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`
- [ ] Senha do DB exposta em chat → rotacionar pós-projeto

---

## 13. Hardening adicional (opcional, pós-apresentação)

**Rate limiting no Caddy** (mitigar brute-force do basic auth):

```bash
# Caddy core não tem rate limit nativo — usar plugin oficial:
sudo dnf install -y caddy-plugin-rate-limit  # OU compilar com xcaddy
```

E no Caddyfile, dentro do `handle` que tem basic_auth:

```
rate_limit {
    zone auth_zone {
        key {remote_host}
        events 5
        window 1m
    }
}
```

**fail2ban pra SSH** (já vem em Oracle Linux 9, basta habilitar):

```bash
sudo dnf install -y fail2ban
sudo systemctl enable --now fail2ban
```

**Atualizações automáticas de segurança:**

```bash
sudo dnf install -y dnf-automatic
sudo systemctl enable --now dnf-automatic.timer
```
