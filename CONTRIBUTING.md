# Contribuindo

Obrigado pelo interesse em contribuir com o RAG ANEEL!

## Setup de desenvolvimento

```bash
git clone https://github.com/Lucas-byte123/RAG-Desafio-ANEEL.git
cd RAG-Desafio-ANEEL
make install     # cria venv + instala deps
make smoke       # valida sintaxe
```

Pra desenvolvimento com banco real, ver [`README.md`](README.md) seção
"Como rodar" e [`AVALIACAO.md`](AVALIACAO.md) seção 3.2.

## Como contribuir

1. **Abra uma issue** descrevendo o que quer fazer (bug, feature, docs)
2. **Fork + branch**: `git checkout -b feat/minha-melhoria`
3. **Faça o commit pequeno** com mensagem descritiva
4. **Rode `make smoke` antes de push** — CI valida o mesmo
5. **Rode `make eval` se afetar comportamento do agente** — não regrida
   métricas
6. **Abra PR** apontando pra `main`. Use o template

## Estilo de código

- **Python 3.11+** (usa type hints novos: `list[str]`, `int | None`)
- Type hints em assinaturas públicas
- Docstrings em funções não-óbvias
- Nomes em **inglês** pra código, **português** pra mensagens user-facing
- Sem `from x import *`
- Imports stdlib > 3rd-party > local, separados por linha em branco

## Convenções de commit

Sigo Conventional Commits simples:

- `feat(escopo): ...` — feature nova
- `fix(escopo): ...` — bugfix
- `perf(escopo): ...` — performance
- `docs(escopo): ...` — só docs
- `chore(escopo): ...` — infra, deps, configs

Escopos comuns: `agent`, `ui`, `eval`, `repo`, `deploy`, `docs`.

## O que NÃO commitar

Já está no `.gitignore`, mas vale lembrar:

- `.env` (segredos)
- `.secrets/` (wallet, chave SSH, senhas)
- `~/.oci/` (config OCI)
- `*.pem` (chaves privadas)
- `logs/` (gerado em runtime)
- `__pycache__/`, `*.pyc`
- Screenshots fora de `docs/screenshots/` (PNGs grandes não-relevantes)

**SE COMMITAR SEGREDO POR ENGANO:** rode imediatamente:

```bash
git rm --cached <arquivo>
git commit -m "remove leaked secret"
git push --force-with-lease  # se ainda não foi público
# Rotacione o segredo no provider (OCI/Cohere/etc)
```

## Reportar bugs

Use o template `.github/ISSUE_TEMPLATE/bug_report.md`. Inclua:

- Versão do Python (`python --version`)
- SO
- Output completo (stack trace)
- `make health` (quando aplicável)
- Últimas 20 linhas de `logs/agent.jsonl`

## Testes

CI roda automático em cada push:
- `py_compile` de todos os scripts
- Verificação de sintaxe via `ast.parse`
- Assert de tamanho do eval dataset
- Grep contra senhas/OCIDs hardcoded

**Não temos pytest formal** — `scripts/test_*.py` são scripts de validação
manual com banco real (precisam credenciais).
