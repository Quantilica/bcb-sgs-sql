"""Hierarchical pipeline runner.

Walks a pipeline directory depth-first, post-order:
each child subtree (any subdirectory containing ``fetch.toml`` or
``transform.toml``) runs to completion before the parent's own
``fetch.toml`` / ``transform.toml`` execute. This lets a parent's
SQL transform consume the materialized outputs of its children.
"""

import logging
import time
from pathlib import Path

from rich.console import Console

from .config import Config
from .toml_runner import TomlScript
from .transform_runner import TransformRunner

logger = logging.getLogger(__name__)


def _is_pipeline_dir(p: Path) -> bool:
    return p.is_dir() and (
        (p / "fetch.toml").exists() or (p / "transform.toml").exists()
    )


def run_subtree(
    config: Config,
    path: Path,
    force_metadata: bool = False,
    force_load: bool = False,
    console: Console | None = None,
):
    """Run sub-pipelines under ``path`` post-order, then ``path`` itself."""
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Pipeline directory not found: {path}")

    for child in sorted(path.iterdir()):
        if _is_pipeline_dir(child):
            run_subtree(config, child, force_metadata, force_load, console)

    fetch_path = path / "fetch.toml"
    transform_path = path / "transform.toml"

    if fetch_path.exists():
        if console:
            console.rule(
                f"[bold cyan]fetch[/bold cyan]  {path.name}", style="cyan dim"
            )
        t0 = time.monotonic()
        TomlScript(
            config,
            fetch_path,
            force_metadata=force_metadata,
            force_load=force_load,
            console=console,
        ).run()
        if console:
            elapsed = time.monotonic() - t0
            console.print(
                f"  [green]✓[/green] fetch concluído em "
                f"[bold]{elapsed:.1f}s[/bold]"
            )

    if transform_path.exists():
        if console:
            console.rule(
                f"[bold magenta]transform[/bold magenta]  {path.name}",
                style="magenta dim",
            )
        t0 = time.monotonic()
        TransformRunner(config, transform_path, console=console).run()
        if console:
            elapsed = time.monotonic() - t0
            console.print(
                f"  [green]✓[/green] transform concluído em "
                f"[bold]{elapsed:.1f}s[/bold]"
            )
