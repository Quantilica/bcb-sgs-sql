import subprocess
import tomllib
from pathlib import Path


def _slugify(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def _fetch_toml_template() -> str:
    return (
        "# Seleciona séries do BCB SGS para carregar.\n"
        "# Encontre IDs em https://www3.bcb.gov.br/sgspub\n"
        "#\n"
        "# Cada [[series]] precisa de 'ids' OU 'themes'.\n"
        "#   ids       — lista de IDs de séries (ex: [433, 13522])\n"
        "#   themes    — filtra o catálogo por tema (exige catálogo já\n"
        "#               carregado por uma execução anterior)\n"
        "#   frequency — opcional: D, S, M, T, Qd, A (restringe 'themes')\n"
        "\n"
        "[[series]]\n"
        "ids = [433, 13522]        # substitua pelos IDs das séries SGS\n"
        "\n"
        "# [[series]]\n"
        '# themes    = ["Índices de preços"]\n'
        '# frequency = "M"\n'
    )


def _transform_toml_template(slug: str) -> str:
    return (
        "# Cada [[table]] declara uma saída do pipeline.\n"
        "# Para múltiplas saídas, adicione mais blocos [[table]] e crie um\n"
        "# arquivo .sql correspondente para cada um.\n"
        "\n"
        "[[table]]\n"
        f'name        = "{slug}"\n'
        'schema      = "analytics"\n'
        'strategy    = "replace"        # "replace" ou "view"\n'
        f'sql         = "{slug}.sql"\n'
        'description = "Descrição da tabela de saída"\n'
    )


def _transform_sql_template() -> str:
    return (
        "-- Painel wide: uma coluna por série, indexado por data.\n"
        "-- Tabelas normalizadas disponíveis:\n"
        "--   series_data      — observações (soft-versioned; filtre ativo)\n"
        "--   series_metadata  — catálogo de séries\n"
        "--   theme            — hierarquia de temas\n"
        "\n"
        "SELECT\n"
        "    sd.date,\n"
        "    MAX(CASE WHEN sd.series_id = 433   THEN sd.value END)"
        " AS serie_433,\n"
        "    MAX(CASE WHEN sd.series_id = 13522 THEN sd.value END)"
        " AS serie_13522\n"
        "FROM series_data sd\n"
        "WHERE sd.series_id IN (433, 13522)  -- substitua pelos IDs\n"
        "  AND sd.ativo = TRUE\n"
        "GROUP BY sd.date\n"
        "ORDER BY sd.date;\n"
    )


class PluginScaffolder:
    def __init__(
        self,
        name: str,
        description: str,
        version: str,
        output_dir: Path,
        git_init: bool,
    ):
        self.name = name
        self.slug = _slugify(name)
        self.description = description
        self.version = version
        self.plugin_dir = Path(output_dir) / name
        self.git_init = git_init

    def create(self) -> Path:
        if self.plugin_dir.exists():
            raise FileExistsError(
                f"Directory '{self.plugin_dir}' already exists."
            )

        self.plugin_dir.mkdir(parents=True)
        pipeline_dir = self.plugin_dir / self.slug
        pipeline_dir.mkdir()

        self._write(self.plugin_dir / "manifest.toml", self._manifest())
        self._write(self.plugin_dir / "README.md", self._readme())
        self._write(pipeline_dir / "fetch.toml", _fetch_toml_template())
        self._write(
            pipeline_dir / "transform.toml",
            _transform_toml_template(self.slug),
        )
        self._write(
            pipeline_dir / f"{self.slug}.sql", _transform_sql_template()
        )

        if self.git_init:
            self._write(self.plugin_dir / ".gitignore", self._gitignore())
            self._run_git_init()

        return self.plugin_dir

    def _write(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def _manifest(self) -> str:
        return (
            f'name        = "{self.name}"\n'
            f'description = "{self.description}"\n'
            f'version     = "{self.version}"\n'
            "\n"
            "[[pipeline]]\n"
            f'id          = "{self.slug}"\n'
            'description = "Descrição do pipeline"\n'
            f'path        = "{self.slug}"\n'
        )

    def _readme(self) -> str:
        return (
            f"# {self.name}\n"
            "\n"
            f"{self.description or 'Descrição do plugin.'}\n"
            "\n"
            "## Instalação\n"
            "\n"
            "```bash\n"
            "bcb-sgs-sql plugin install <git-url>\n"
            "```\n"
            "\n"
            "## Pipelines\n"
            "\n"
            "| ID | Descrição | Path |\n"
            "|---|---|---|\n"
            f"| {self.slug} | Descrição do pipeline | {self.slug}/ |\n"
            "\n"
            "## Desenvolvimento\n"
            "\n"
            "1. Encontre as séries em https://www3.bcb.gov.br/sgspub\n"
            f"2. Edite `{self.slug}/fetch.toml` com os IDs das séries\n"
            f"3. Ajuste `{self.slug}/{self.slug}.sql` para a transformação\n"
            f"4. Atualize `{self.slug}/transform.toml` com o nome da saída\n"
            "5. Adicione mais pipelines em `manifest.toml` conforme preciso\n"
            "\n"
            "### Frequências (acrônimos)\n"
            "\n"
            "| Acrônimo | Frequência |\n"
            "|---|---|\n"
            "| D | Diária |\n"
            "| S | Semanal |\n"
            "| M | Mensal |\n"
            "| T | Trimestral |\n"
            "| Qd | Quadrimestral |\n"
            "| A | Anual |\n"
        )

    def _gitignore(self) -> str:
        return "__pycache__/\n*.py[cod]\n.env\n.DS_Store\nconfig.ini\n"

    def _run_git_init(self) -> None:
        cwd = str(self.plugin_dir)
        try:
            subprocess.run(
                ["git", "init"], cwd=cwd, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "add", "."], cwd=cwd, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", "chore: initial scaffold"],
                cwd=cwd,
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "git não encontrado. Instale o Git ou use --no-git-init."
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                "Falha ao inicializar repositório Git: "
                f"{e.stderr.decode().strip()}"
            ) from e


class PipelineAdder:
    def __init__(
        self,
        pipeline_id: str,
        description: str,
        path: str,
        plugin_dir: Path,
    ):
        self.pipeline_id = pipeline_id
        self.slug = _slugify(pipeline_id)
        self.description = description
        self.path = path or self.slug
        self.plugin_dir = Path(plugin_dir)
        self.manifest_path = self.plugin_dir / "manifest.toml"
        self.pipeline_dir = self.plugin_dir / self.path

    def add(self) -> Path:
        if not self.manifest_path.exists():
            raise FileNotFoundError(
                f"manifest.toml não encontrado em '{self.plugin_dir}'. "
                "Execute dentro do diretório do plugin ou use --plugin-dir."
            )

        with open(self.manifest_path, "rb") as f:
            manifest = tomllib.load(f)
        existing_ids = {p["id"] for p in manifest.get("pipeline", [])}
        if self.pipeline_id in existing_ids:
            raise ValueError(
                f"Pipeline '{self.pipeline_id}' já existe no manifest.toml."
            )

        if self.pipeline_dir.exists():
            raise FileExistsError(
                f"Diretório '{self.pipeline_dir}' já existe."
            )

        self.pipeline_dir.mkdir(parents=True)
        self.pipeline_dir.joinpath("fetch.toml").write_text(
            _fetch_toml_template(), encoding="utf-8"
        )
        self.pipeline_dir.joinpath("transform.toml").write_text(
            _transform_toml_template(self.slug), encoding="utf-8"
        )
        self.pipeline_dir.joinpath(f"{self.slug}.sql").write_text(
            _transform_sql_template(), encoding="utf-8"
        )

        self._append_to_manifest()
        return self.pipeline_dir

    def _append_to_manifest(self) -> None:
        entry = (
            "\n"
            "[[pipeline]]\n"
            f'id          = "{self.pipeline_id}"\n'
            f'description = "{self.description}"\n'
            f'path        = "{self.path}"\n'
        )
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(entry)
