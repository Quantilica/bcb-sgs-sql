import json
import logging
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import platformdirs

logger = logging.getLogger(__name__)

APP_NAME = "bcb-sgs-sql"
DEFAULT_PLUGIN_URL = "https://github.com/Quantilica/bcb-sgs-pipelines.git"
DEFAULT_PLUGIN_ALIAS = "std"


@dataclass
class PipelineDef:
    id: str
    description: str
    path: Path


@dataclass
class PluginManifest:
    name: str
    description: str
    version: str
    pipelines: list[PipelineDef]


class PluginRegistry:
    def __init__(self):
        self.config_dir = Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))
        self.data_dir = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
        self.plugins_dir = self.data_dir / "plugins"
        self.registry_file = self.config_dir / "registry.json"

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        if not self.registry_file.exists():
            self._save_registry({})

    def _load_registry(self) -> dict:
        with open(self.registry_file, encoding="utf-8") as f:
            return json.load(f)

    def _save_registry(self, data: dict):
        with open(self.registry_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def get_plugins(self) -> dict:
        return self._load_registry()

    def get_plugin_path(self, alias: str) -> Path:
        return self.plugins_dir / alias

    def register_plugin(self, alias: str, url: str):
        registry = self._load_registry()
        registry[alias] = {"url": url}
        self._save_registry(registry)

    def remove_plugin(self, alias: str):
        registry = self._load_registry()
        if alias in registry:
            del registry[alias]
            self._save_registry(registry)


class PluginManager:
    def __init__(self):
        self.registry = PluginRegistry()

    def _check_git(self):
        """Verifica se o Git está instalado."""
        if shutil.which("git") is None:
            raise RuntimeError(
                "Git não encontrado. O Git é necessário para gerenciar e "
                "baixar plugins. Por favor, instale o Git "
                "(https://git-scm.com/) e tente novamente."
            )

    def install(self, url: str, alias: str | None = None):
        self._check_git()
        if not alias:
            alias = url.rstrip("/").split("/")[-1]
            if alias.endswith(".git"):
                alias = alias[:-4]

        plugin_path = self.registry.get_plugin_path(alias)
        if plugin_path.exists():
            raise ValueError(f"Plugin with alias '{alias}' is already installed.")

        logger.info("Cloning %s into %s", url, plugin_path)
        subprocess.run(["git", "clone", url, str(plugin_path)], check=True)
        self.registry.register_plugin(alias, url)
        logger.info("Plugin '%s' installed successfully.", alias)

    def update(self, alias: str | None = None):
        self._check_git()
        plugins = self.registry.get_plugins()
        target_aliases = [alias] if alias else list(plugins.keys())

        for target in target_aliases:
            if target not in plugins:
                logger.warning("Plugin '%s' not found in registry.", target)
                continue

            plugin_path = self.registry.get_plugin_path(target)
            if not plugin_path.exists():
                logger.warning("Plugin directory for '%s' is missing.", target)
                continue

            logger.info("Updating plugin '%s'", target)
            subprocess.run(["git", "pull"], cwd=plugin_path, check=True)

    def remove(self, alias: str):
        plugin_path = self.registry.get_plugin_path(alias)
        if plugin_path.exists():
            logger.info("Removing directory %s", plugin_path)

            def handle_remove_readonly(func, path, exc):
                import os
                import stat

                os.chmod(path, stat.S_IWRITE)
                func(path)

            shutil.rmtree(plugin_path, onerror=handle_remove_readonly)

        self.registry.remove_plugin(alias)
        logger.info("Plugin '%s' removed successfully.", alias)

    def ensure_defaults(self):
        """Garante que o plugin padrão esteja instalado."""
        plugins = self.registry.get_plugins()
        if DEFAULT_PLUGIN_ALIAS not in plugins:
            try:
                self._check_git()
                logger.info("Instalando pipelines padrão...")
                self.install(DEFAULT_PLUGIN_URL, alias=DEFAULT_PLUGIN_ALIAS)
            except Exception as e:
                logger.debug("Falha ao instalar pipelines padrão: %s", e)

    def read_manifest(self, alias: str) -> PluginManifest:
        plugin_path = self.registry.get_plugin_path(alias)
        manifest_path = plugin_path / "manifest.toml"

        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Manifest not found for plugin '{alias}' at {manifest_path}"
            )

        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)

        pipelines = []
        for p in data.get("pipeline", []):
            pipelines.append(
                PipelineDef(
                    id=p["id"],
                    description=p.get("description", ""),
                    path=plugin_path / p["path"],
                )
            )

        return PluginManifest(
            name=data.get("name", alias),
            description=data.get("description", ""),
            version=data.get("version", "unknown"),
            pipelines=pipelines,
        )

    def list_pipelines(self) -> list[tuple[str, str, PipelineDef]]:
        plugins = self.registry.get_plugins()
        all_pipelines = []

        for alias in plugins:
            try:
                manifest = self.read_manifest(alias)
                for p in manifest.pipelines:
                    all_pipelines.append((alias, manifest.name, p))
            except Exception as e:
                logger.warning("Could not load manifest for plugin '%s': %s", alias, e)

        return all_pipelines

    def get_pipeline(self, alias: str, pipeline_id: str) -> PipelineDef:
        manifest = self.read_manifest(alias)
        for p in manifest.pipelines:
            if p.id == pipeline_id:
                return p
        raise ValueError(f"Pipeline '{pipeline_id}' not found in plugin '{alias}'")
