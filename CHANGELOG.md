# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
e este projeto adere ao [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [0.2.0] - 2026-07-18

### Corrigido

- **`run` e `transform` saíam com código 0 mesmo em falha de pipeline** — os blocos
  `except` imprimiam o traceback mas não faziam `raise typer.Exit(1)` (ao contrário de
  `run-path`/`load`). Agora propagam código de saída ≠ 0, permitindo detecção de erro em
  CI/automação.
- `Config.__str__` deixava a senha em texto plano — agora mascarada, consistente com o
  `config list`.

### Adicionado

- `py.typed` (marcador de pacote tipado) + classifier `Typing :: Typed`.
- Metadados PEP 639 de licença (`license = "MIT"` + `license-files`).
- Configuração de `ruff` (`E/F/I/UP/B`) e `pytest`.
- Workflows de CI (teste com `uv` + `ruff` + `pytest`) e de publicação via
  Trusted Publishing (OIDC).
- README reescrito ao nível do `sidra-sql` (arquitetura, esquema das 4 tabelas,
  versionamento de revisões, uso, formato TOML, fluxo de dados).
- Teste de regressão do código de saída de `run`/`transform`.

### Notas

- A dependência de `bcb-sgs-fetcher` continua via `git+https@v0.4.0` até o fetcher ser
  publicado no PyPI; então deve virar `bcb-sgs-fetcher>=0.5.0` (ver TODO no `pyproject.toml`).
