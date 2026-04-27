# Política de segurança

## Reportar vulnerabilidades

Encontrou uma falha de segurança no RAG ANEEL? **Não abra issue público.**

Reporte privado abrindo um GitHub Security Advisory:
https://github.com/Lucas-byte123/RAG-Desafio-ANEEL/security/advisories/new

Vou responder em até 48h e coordenar disclosure responsável.

## Escopo

Este projeto é uma demo educacional/de portfólio. Não recebe dados
sensíveis de usuários reais. Mesmo assim, áreas relevantes pra security:

- **Credenciais OCI/Oracle expostas em código** — crítico
- **SQL injection** nos campos de query (mitigado via bind parameters)
- **Prompt injection** que faça o LLM ignorar guardrails ou citação
- **Path traversal** nos endpoints/inputs
- **DoS** via queries que estouram contexto/tokens

## O que NÃO é considerado vulnerabilidade

- Resposta "errada" do LLM em tema obscuro (RAG não é determinístico)
- Latência alta (gargalo conhecido é o LLM, ~30s)
- Demo URL ficar offline (válida só até 2026-05-25)

## Práticas de segurança aplicadas

- **Wallet OCI** isolado em `.secrets/` (gitignored, chmod 600 esperado)
- **`.env`** com `DB_ADMIN_PASS` gitignored
- **CI grep** contra OCIDs e senhas hardcoded em `scripts/`
- **Bind parameters** em todas as queries SQL (ver `scripts/rag_agent.py`)
- **systemd hardening** em produção: `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`
- **SELinux Enforcing** na VM Oracle Linux
- **HTTPS** obrigatório via Caddy + Let's Encrypt
- **Health endpoint** em porta separada (8502), por padrão localhost-only
  (`HEALTH_HOST=127.0.0.1`)
- **5 camadas de guardrails** no agent (temporal, escopo, rerank, citação)

## Auditoria de dependências

Revise periodicamente:

```bash
pip list --outdated
pip-audit  # se instalado
```

CVEs críticas serão patched em pacote pinado no `requirements.txt`.
