"""
Logique métier de `forge list`.

Affiche les modules Forge disponibles et indique ceux qui sont installés dans
le projet courant (d'après `forge.toml`).
"""

from __future__ import annotations

from pathlib import Path

import typer

from forge.commands._options import ListOptions
from forge.core.dependency_resolver import build_registry
from forge.core.state import load_state

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_APPS_DIR = _TEMPLATES_DIR / "apps"


def run(options: ListOptions, project_dir: Path | None = None) -> None:
    """Exécute `forge list`."""
    registry = build_registry(_APPS_DIR)
    state = load_state(project_dir or _project_root())

    names = sorted(registry)
    if options.installed:
        names = [name for name in names if name in state.modules]
        if not names:
            typer.echo("Aucun module installé dans ce projet.")
            return

    width = max(len(name) for name in names)
    typer.echo(f"{'MODULE':<{width}}  {'ETAT':<10}  {'VERSION':<10}  PAQUET PYPI")
    for name in names:
        manifest = registry[name]
        record = state.modules.get(name)
        status = "installé" if record else "disponible"
        version = record.version if record else manifest.version
        package = ", ".join(manifest.python_packages)
        typer.echo(f"{name:<{width}}  {status:<10}  {version:<10}  {package}")


def _project_root() -> Path:
    """Racine du projet courant, ou le dossier courant hors projet Django."""
    from forge.core.engine import find_manage_py

    try:
        return find_manage_py().parent
    except FileNotFoundError:
        return Path.cwd()
