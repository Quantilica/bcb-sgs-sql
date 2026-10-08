# bcb-sgs-sql: Séries temporais do BCB SGS em PostgreSQL

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square) ![Python](https://img.shields.io/badge/python-3.12+-blue.svg?style=flat-square) ![PostgreSQL](https://img.shields.io/badge/postgresql-%E2%89%A515-blue.svg?style=flat-square)

**Motor ETL que baixa, normaliza e carrega séries do [BCB SGS](https://www3.bcb.gov.br/sgspub/) (Sistema Gerenciador de Séries Temporais) em PostgreSQL, com histórico de revisões preservado.**

É a camada SQL/ETL sobre o [`bcb-sgs-fetcher`](https://github.com/Quantilica/bcb-sgs-fetcher), análoga ao [`sidra-sql`](https://github.com/Quantilica/sidra-sql) para o IBGE/SIDRA.

---

## Por que usar este projeto?

- **Revisões preservadas:** o BCB revisa valores sem aviso. Em vez de sobrescrever, cada revisão insere uma nova linha e desativa a anterior (*soft-versioning*) — o histórico nunca é destruído.
- **Idempotência:** recarregar os mesmos dados não produz churn; a carga detecta mudanças e só escreve o que de fato mudou.
- **Carga em massa:** `COPY FROM STDIN` via `psycopg3` para inserção de alta performance, através de uma tabela de staging.
- **Declarativo:** cada conjunto de séries é descrito em TOML — sem código Python para adicionar novas séries.
- **Cache:** reaproveita do disco os snapshots JSON já baixados pelo `bcb-sgs-fetcher` (com TTL configurável).
- **Busca full-text:** catálogo com `tsvector` (português) indexado por GIN.

---

## Funcionalidades

| Funcionalidade | Detalhes |
|---|---|
| **Soft-versioning de revisões** | Revisões inserem nova linha e marcam a anterior `ativo = FALSE`; no máximo uma linha ativa por chave (índice único parcial) |
| **Carga idempotente** | Merge com detecção de mudança (`value IS DISTINCT FROM`) — recarga sem novidades é no-op |
| **Carga em massa** | `COPY FROM STDIN` → staging temporária → merge set-based |
| **Cache determinístico** | Snapshots `series_{id}@{ts}.json` do fetcher; TTL via `storage.cache_ttl_hours` |
| **Busca full-text** | Coluna gerada `search_vector TSVECTOR` (português), índice GIN |
| **Hierarquia de temas** | Tabela `theme` auto-referente (árvore) + `theme_hierarchy` desnormalizado (ARRAY, GIN) |
| **Pipelines declarativos** | `fetch.toml` (seleção de séries) + `transform.toml`/`.sql` (materialização wide/pivot) |
| **Plugins** | Pipelines moram em repositórios git instaláveis via `plugin install` |
| **Skip inteligente de carga** | `arquivo_carregado` rastreia arquivos já carregados; re-execuções pulam o trabalho |

---

## Arquitetura

```
BCB SGS
├── api.bcb.gov.br/dados/...   (JSON — valores)
└── www3.bcb.gov.br/sgspub     (HTML — metadados + catálogo)
        │
        ▼
┌──────────────────┐
│ bcb-sgs-fetcher  │  SgsDataClient (JSON) + ScraperClient (HTML)
└────────┬─────────┘
         │  snapshots series_{id}@{ts}.json  (cache em disco)
         ▼
┌──────────────┐     ┌──────────────────┐
│ bcb-sgs-sql  │◄────│ bcb-sgs-pipelines│  fetch.toml + transform.toml + .sql
│  Motor ETL   │     │  (plugin git)    │
└──────┬───────┘
       │  COPY FROM STDIN → staging → merge (soft-versioning)
       ▼
┌─────────────────────────────────────────────────────┐
│ theme · series_metadata · series_data ·             │
│ arquivo_carregado        (+ tabelas/views analytics)│
└─────────────────────────────────────────────────────┘
```

---

## Esquema do Banco de Dados

Quatro tabelas (o schema é selecionado pelo `search_path`, configurável em `database.schema`).

### `series_metadata` — catálogo de séries

Chave primária é o **`series_id` natural** do BCB (sem autoincrement). Guarda nomes (índice/abreviado/inglês), `theme_hierarchy` como `ARRAY(Text)` (GIN) **e** `theme_id` FK normalizado, frequência/unidade/fonte/datas, precisão, min/max, flags (`active`/`special`), fórmula, e quatro blobs `JSONB` de metadados completos (`full_provider_data`, `full_description`, `full_methodology`, `full_dissemination_formats`). Tem uma coluna gerada **`search_vector TSVECTOR`** (`to_tsvector('portuguese', ...)`, persistida, índice GIN) para busca full-text.

### `series_data` — fato de observações (soft-versioned)

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | `BIGINT` Identity | PK |
| `series_id` | `Integer` FK → `series_metadata` | |
| `date` | `Date` | início do período |
| `date_end` | `Date` NULL | fim do período (observações de intervalo) |
| `value` | `Numeric` NULL | valor |
| `loaded_at` | `timestamptz` | `now()` na inserção |
| `ativo` | `Boolean` | marca a linha vigente |

Índice único **parcial** `uq_series_data_active (series_id, date, date_end) WHERE ativo` com `NULLS NOT DISTINCT` garante no máximo uma linha ativa por chave (por isso **PostgreSQL ≥ 15**).

### `theme` — hierarquia de temas

Árvore auto-referente: `level` + `parent_id` (FK para si mesma), com check constraints (`positive_level`, `consistent_parent_level`) e `UNIQUE (name, level, parent_id) NULLS NOT DISTINCT`.

### `arquivo_carregado` — ledger de idempotência

PK `arquivo` (nome do arquivo), `series_id` opcional, `carregado_em`. Permite pular arquivos já carregados.

### Versionamento de revisões (soft-versioning)

O BCB revisa valores publicados sem aviso. Em vez de `UPDATE`, a carga:

1. Faz `COPY` das novas observações para uma tabela de staging temporária.
2. **Desativa** (`ativo = FALSE`) as linhas ativas cuja `value IS DISTINCT FROM` a nova.
3. **Insere** linhas novas para chaves inéditas ou valores mudados.

Assim o histórico completo de revisões é preservado sem tabela de auditoria separada, e uma recarga idêntica é um no-op (zero churn).

> **Limitação conhecida:** só há `loaded_at` (momento da inserção); o momento de *desativação* de uma linha superada não é registrado, então reconstrução as-of ("o que estava ativo na data T") ainda não é first-class.

---

## Pré-requisitos

- Python **3.12+**
- **PostgreSQL ≥ 15** (necessário para `NULLS NOT DISTINCT` no índice parcial)
- Acesso à internet para a API/portal do BCB (no modo fetch-and-load)

---

## Instalação

```bash
# Via CLI unificada (recomendado)
quantilica install bcb-sgs-sql

# Ou como biblioteca no seu projeto
uv add bcb-sgs-sql --index https://index.quantilica.com/simple/
```

> **Nota:** Versões anteriores a 0.2.2 permanecem no PyPI, congeladas; a partir de 0.2.2 a distribuição é feita exclusivamente pelo índice Quantilica.

---

## Configuração

`bcb-sgs-sql` lê um `config.ini` do diretório de trabalho e/ou do caminho global (`~/.config/quantilica/bcb-sgs-sql/config.ini`); o local sobrepõe o global.

```bash
bcb-sgs-sql config set database.host     localhost
bcb-sgs-sql config set database.port     5432
bcb-sgs-sql config set database.user     postgres
bcb-sgs-sql config set database.password suasenha
bcb-sgs-sql config set database.dbname   dados
bcb-sgs-sql config set database.schema   bcb_sgs
bcb-sgs-sql config set storage.data_dir  data
# opcional: TTL do cache de download em horas (default 24)
bcb-sgs-sql config set storage.cache_ttl_hours 24
```

Use `--global` para escrever no config global. `bcb-sgs-sql config list` mostra a configuração efetiva (a senha é mascarada).

Equivalente em `config.ini`:

```ini
[database]
host = localhost
port = 5432
user = postgres
password = suasenha
dbname = dados
schema = bcb_sgs

[storage]
data_dir = data
cache_ttl_hours = 24
```

---

## Uso

### 1. Gerenciar plugins

Pipelines moram em repositórios git ("plugins"). O plugin oficial [`bcb-sgs-pipelines`](https://github.com/Quantilica/bcb-sgs-pipelines) é instalado automaticamente (alias `std`) na primeira execução.

```bash
# Instalar um plugin via URL do Git
bcb-sgs-sql plugin install https://github.com/Quantilica/bcb-sgs-pipelines.git --alias std

# Listar plugins instalados e suas pipelines
bcb-sgs-sql plugin list

# Atualizar / remover
bcb-sgs-sql plugin update std
bcb-sgs-sql plugin remove std
```

### 2. Criar e desenvolver pipelines

```bash
# Estrutura completa de um novo plugin
bcb-sgs-sql plugin scaffold

# Adicionar um pipeline a um plugin existente
bcb-sgs-sql plugin add-pipeline <id> --description "..."

# Validar a estrutura antes de publicar
bcb-sgs-sql plugin validate <alias>
```

### 3. Executar pipelines

```bash
# Baixa (ou usa cache) e carrega um pipeline do plugin 'std'
bcb-sgs-sql run std juros

# Executa todos os pipelines de um plugin
bcb-sgs-sql run std

# Executa um pipeline diretamente por caminho, sem plugin registrado
bcb-sgs-sql run-path ./meu/pipeline

# Força metadados frescos / recarga ignorando o registro de arquivos
bcb-sgs-sql run std juros --force-metadata --force-load

# TTL do cache em horas (sobrepõe storage.cache_ttl_hours)
bcb-sgs-sql run std juros --max-age 6

# Apenas a etapa de transformação (sem fetch)
bcb-sgs-sql transform std juros
```

### 4. Carregar de arquivos (sem rede)

```bash
# Carrega JSON já baixados em disco
bcb-sgs-sql load ./data/bcb-sgs --kind auto      # detecta valores vs metadados
bcb-sgs-sql load ./data/bcb-sgs --kind json --with-data
```

---

## Formato TOML

### `fetch.toml` — seleção de séries

Cada `[[series]]` é um seletor: por `ids` explícitos e/ou por `themes` (consulta o catálogo), opcionalmente restrito por `frequency` (acrônimo `D`, `S`, `M`, `T`, `Qd`, `A`).

```toml
[[series]]
ids = [432, 11, 12, 226]     # Selic meta, Selic acum., CDI, TR

[[series]]
themes = ["Taxas de juros"]
frequency = "M"
```

### `transform.toml` — materialização

Cada `[[table]]` gera uma tabela ou view no schema `analytics`, via `strategy = "replace"` (`DROP` + `CREATE TABLE AS`) ou `"view"` (`CREATE OR REPLACE VIEW`). Suporta `primary_key` e `indexes`.

```toml
[[table]]
name = "juros_wide"
schema = "analytics"
strategy = "replace"
sql = "juros.sql"
primary_key = ["date"]

[[table.indexes]]
columns = ["date"]
```

O `.sql` correspondente pivota as séries em colunas (padrão `MAX(CASE WHEN series_id = ... THEN value END)`).

---

## Fluxo de Dados

1. **Fetch** — `fetch.toml` resolve os seletores em IDs de série; o `bcb-sgs-fetcher` busca metadados + observações (respeitando o cache/TTL) e grava snapshots JSON.
2. **Load** — `COPY FROM STDIN` para staging, depois merge soft-versioned em `series_data` (e upsert em `series_metadata`/`theme`).
3. **Transform** — `transform.toml` materializa tabelas/views wide/pivot no schema `analytics`, prontas para BI.

A orquestração (`run`) percorre o diretório do pipeline em pós-ordem (subdiretórios primeiro), então um transform de nível superior pode consumir a saída dos filhos.

---

## Desenvolvimento

```bash
git clone https://github.com/Quantilica/bcb-sgs-sql.git
cd bcb-sgs-sql
uv sync --group dev
uv run ruff check src/ tests/
uv run pytest
```

Os testes que tocam o banco exigem um PostgreSQL ≥ 15 real via variável de ambiente `BCB_SGS_SQL_TEST_DSN` (ex.: `postgresql+psycopg://user:pass@localhost:5432/dbname`); sem ela, esses testes são pulados.

---

## Estrutura do Repositório

```
src/bcb_sgs_sql/
├── cli.py               — CLI (typer): run, transform, load, config, plugin
├── config.py            — config.ini (local + global) via configparser
├── database.py          — engine, COPY/staging, merge soft-versioned
├── loader.py            — mapeamento snapshot → linhas + orquestração de load
├── models.py            — ORM SQLAlchemy (4 tabelas)
├── runner.py            — travessia pós-ordem do pipeline
├── sgs.py               — wrapper do bcb-sgs-fetcher (download concorrente + retry)
├── toml_runner.py       — etapa de fetch (fetch.toml)
├── transform_runner.py  — etapa de transform (transform.toml)
├── plugin_manager.py    — install/update/remove de plugins git
├── scaffold.py          — scaffold de plugins e pipelines
└── validator.py         — validação da estrutura de plugins
```

---

## Licença

MIT — veja [LICENSE](LICENSE).
