# bcb-sgs-sql

Carrega séries temporais do **BCB SGS** (Sistema Gerenciador de Séries
Temporais) em **PostgreSQL**. É a camada SQL/ETL sobre o
[`bcb-sgs-fetcher`](https://github.com/Quantilica/bcb-sgs-fetcher),
análoga ao `sidra-sql` para o IBGE/SIDRA.

## O que carrega

- **`series_metadata`** — catálogo de séries (nome, frequência, unidade,
  fonte, datas, metadados completos em JSONB).
- **`series_data`** — observações, com *soft-versioning*: revisões inserem
  uma nova linha e marcam a anterior `ativo = FALSE` (o histórico de
  revisões é preservado sem tabela de auditoria).
- **`theme`** — hierarquia de temas (árvore auto-referente).

## Duas formas de carregar

- **Fetch-and-load** (envolve o `bcb-sgs-fetcher`): busca da API e carrega.

  ```bash
  bcb-sgs-sql run std precos        # roda um pipeline do plugin padrão
  bcb-sgs-sql run-path ./meu/pipeline
  ```

- **Load-from-files** (sem rede): carrega Parquet/JSON já baixados.

  ```bash
  bcb-sgs-sql load ./data/bcb-sgs --kind auto
  ```

## Configuração

```bash
bcb-sgs-sql config set database.host     <host>
bcb-sgs-sql config set database.port     5432
bcb-sgs-sql config set database.user     <user>
bcb-sgs-sql config set database.password <password>
bcb-sgs-sql config set database.dbname   <dbname>
bcb-sgs-sql config set database.schema   <schema>
bcb-sgs-sql config set storage.data_dir  <path>
```

## Pipelines declarativos

Pipelines moram em repositórios git (ex.: `bcb-sgs-pipelines`), instalados
via `bcb-sgs-sql plugin install <git-url>`. Cada pipeline é um diretório
com `fetch.toml` (seleção de séries) + `transform.toml`/`.sql`
(materialização de views wide/pivot).
