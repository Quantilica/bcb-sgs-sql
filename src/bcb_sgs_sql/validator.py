import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass
class Issue:
    severity: Severity
    message: str


@dataclass
class SectionReport:
    title: str
    issues: list[Issue] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.issues.append(Issue(Severity.OK, msg))

    def warn(self, msg: str) -> None:
        self.issues.append(Issue(Severity.WARN, msg))

    def error(self, msg: str) -> None:
        self.issues.append(Issue(Severity.ERROR, msg))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARN]


@dataclass
class ValidationReport:
    sections: list[SectionReport] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(len(s.errors) for s in self.sections)

    @property
    def warning_count(self) -> int:
        return sum(len(s.warnings) for s in self.sections)

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0


class PluginValidator:
    def __init__(self, plugin_dir: Path):
        self.plugin_dir = plugin_dir

    def validate(self) -> ValidationReport:
        report = ValidationReport()

        manifest_section = SectionReport("manifest.toml")
        report.sections.append(manifest_section)

        manifest_path = self.plugin_dir / "manifest.toml"
        if not manifest_path.exists():
            manifest_section.error("manifest.toml não encontrado")
            return report

        try:
            with open(manifest_path, "rb") as f:
                manifest = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            manifest_section.error(f"TOML inválido: {e}")
            return report

        manifest_section.ok("TOML válido")

        if "name" not in manifest:
            manifest_section.warn("Campo 'name' ausente")
        if "version" not in manifest:
            manifest_section.warn("Campo 'version' ausente")

        pipelines = manifest.get("pipeline", [])
        if not pipelines:
            manifest_section.warn("Nenhum [[pipeline]] declarado")
        else:
            manifest_section.ok(f"{len(pipelines)} pipeline(s) declarado(s)")

        ids_seen: set[str] = set()
        valid_pipelines: list[dict] = []

        for i, p in enumerate(pipelines):
            entry = f"pipeline[{i}]"
            pid = p.get("id")
            ppath = p.get("path")

            if not pid:
                manifest_section.error(f"{entry}: campo 'id' ausente")
                continue
            if not ppath:
                manifest_section.error(
                    f"pipeline '{pid}': campo 'path' ausente"
                )
                continue
            if pid in ids_seen:
                manifest_section.error(f"ID duplicado: '{pid}'")
                continue

            ids_seen.add(pid)
            valid_pipelines.append(p)

        for p in valid_pipelines:
            section = SectionReport(p["path"])
            report.sections.append(section)
            self._validate_pipeline(p["id"], p["path"], section)

        return report

    def _validate_pipeline(
        self, pid: str, rel_path: str, section: SectionReport
    ) -> None:
        pipeline_dir = self.plugin_dir / rel_path

        if not pipeline_dir.exists():
            section.error(f"Diretório não encontrado: '{pipeline_dir}'")
            return

        has_fetch = (pipeline_dir / "fetch.toml").exists()
        has_transform = (pipeline_dir / "transform.toml").exists()

        if not has_fetch and not has_transform:
            section.error("Nenhum fetch.toml ou transform.toml encontrado")
            return

        if has_fetch:
            self._validate_fetch_toml(pipeline_dir, section)

        if has_transform:
            self._validate_transform_toml(pipeline_dir, section)

    def _validate_fetch_toml(
        self, pipeline_dir: Path, section: SectionReport
    ) -> None:
        fetch_path = pipeline_dir / "fetch.toml"
        try:
            with open(fetch_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            section.error(f"fetch.toml: TOML inválido: {e}")
            return

        series = data.get("series", [])
        if not series:
            section.error("fetch.toml: nenhuma [[series]] declarada")
            return

        for i, s in enumerate(series):
            if not s.get("ids") and not s.get("themes"):
                section.error(
                    f"fetch.toml: series[{i}] precisa de 'ids' ou 'themes'"
                )

        section.ok(f"fetch.toml válido ({len(series)} selector(es))")

    def _validate_transform_toml(
        self, pipeline_dir: Path, section: SectionReport
    ) -> None:
        transform_path = pipeline_dir / "transform.toml"
        try:
            with open(transform_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            section.error(f"transform.toml: TOML inválido: {e}")
            return

        tables = data.get("table")
        if isinstance(tables, dict):
            section.error(
                "transform.toml: use [[table]] (array) com campo 'sql'"
            )
            return
        if not isinstance(tables, list) or not tables:
            section.error("transform.toml: nenhum [[table]] declarado")
            return

        seen: set[tuple[str, str]] = set()
        any_error = False
        for i, t in enumerate(tables):
            entry = f"[[table]][{i}]"
            missing = [
                f
                for f in ("name", "schema", "strategy", "sql")
                if f not in t
            ]
            if missing:
                section.error(
                    f"transform.toml: {entry} sem campo(s) obrigatório(s): "
                    f"{', '.join(missing)}"
                )
                any_error = True
                continue

            strategy = t["strategy"]
            if strategy not in ("replace", "view"):
                section.error(
                    f"transform.toml: {entry} strategy inválido: "
                    f"{strategy!r} (esperado 'replace' ou 'view')"
                )
                any_error = True

            key = (t["schema"], t["name"])
            if key in seen:
                section.error(
                    f"transform.toml: saída duplicada {key[0]}.{key[1]}"
                )
                any_error = True
            seen.add(key)

            sql_path = pipeline_dir / t["sql"]
            if not sql_path.exists():
                section.error(
                    f"transform.toml: {entry} aponta para sql='{t['sql']}' "
                    "mas o arquivo não existe"
                )
                any_error = True

        if not any_error:
            section.ok(f"transform.toml válido ({len(tables)} saída(s))")
