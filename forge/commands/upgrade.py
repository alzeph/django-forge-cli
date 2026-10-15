"""
Logique métier de `forge upgrade [module]`.

Aligne le projet sur les manifestes de la version courante de la CLI et met à
jour les paquets PyPI correspondants :

1. Réappliquer la configuration minimale du module (idempotent) : une nouvelle
   version de la CLI peut déclarer une app ou un middleware de plus.
2. Mettre à jour les paquets dans l'environnement du projet.
3. Réécrire l'entrée du module dans `forge.toml`.

Sans argument, tous les modules installés sont traités.
"""

from __future__ import annotations

from pathlib import Path

import typer
from pycatch import Err, Ok

from forge.core import environment
from forge.core.dependency_resolver import build_registry, resolve_safe
from forge.core.state import cli_version, load_state, save_state

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_APPS_DIR = _TEMPLATES_DIR / "apps"


def run(
    module_name: str | None = None,
    project_dir: Path | None = None,
    project_name: str | None = None,
) -> None:
    """Exécute `forge upgrade`."""
    from forge.commands.install import _apply_manifest_config, _detect_project_package
    from forge.core.engine import find_manage_py

    root = project_dir or find_manage_py().parent
    pkg_name = project_name or _detect_project_package(root)

    registry = build_registry(_APPS_DIR)
    state = load_state(root)

    if module_name is not None and module_name not in state.modules:
        typer.echo(f"Erreur : '{module_name}' n'est pas installé dans ce projet.", err=True)
        raise typer.Exit(code=1)

    targets = [module_name] if module_name else sorted(state.modules)
    if not targets:
        typer.echo("Aucun module installé.")
        return

    settings_path = root / pkg_name / "settings.py"
    packages: list[str] = []

    for name in targets:
        if name not in registry:
            typer.echo(f"  ! '{name}' inconnu de cette version de la CLI, ignoré.", err=True)
            continue

        match resolve_safe(name, registry):
            case Ok(plan):
                _apply_manifest_config(plan, root, settings_path, pkg_name)
            case Err(error):
                typer.echo(f"  ! {error}", err=True)
                continue

        manifest = registry[name]
        state.record_module(name, manifest.version, manifest.python_packages)
        for spec in manifest.python_packages:
            if spec not in packages:
                packages.append(spec)

    if packages:
        environment.install_packages(packages, root, upgrade=True)

    state.forge_version = cli_version()
    save_state(root, state)

    typer.echo("\nVersions installées :")
    for spec in packages:
        name = environment.distribution_name(spec)
        typer.echo(f"  {name} {environment.installed_version(name, root) or 'inconnue'}")
