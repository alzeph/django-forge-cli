"""
Logique métier de `forge remove <module>`.

Défait ce que `forge install` a posé pour un module :

1. Refuser si un autre module installé en dépend.
2. Retirer ses `installed_apps` et `middleware` de `settings.py`.
3. Retirer les réglages qu'il a posés (`settings` du manifeste).
4. Retirer son branchement dans le `urls.py` principal.
5. Désinstaller ses paquets de l'environnement du projet.
6. L'effacer de `forge.toml`.

Ne touche ni aux migrations déjà appliquées, ni au fichier `.env`, ni aux
dépendances du module (d'autres modules peuvent encore en avoir besoin).
"""

from __future__ import annotations

from pathlib import Path

import typer

from forge.commands._options import RemoveOptions
from forge.core import environment
from forge.core.config_manager import remove_from_list_setting, remove_setting
from forge.core.dependency_resolver import Manifest, build_registry
from forge.core.state import load_state, save_state
from forge.core.urls_manager import unwire_url_include

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_APPS_DIR = _TEMPLATES_DIR / "apps"


def run(
    module_name: str,
    options: RemoveOptions,
    project_dir: Path | None = None,
    project_name: str | None = None,
) -> None:
    """Exécute `forge remove`."""
    from forge.commands.install import _detect_project_package
    from forge.core.engine import find_manage_py

    root = project_dir or find_manage_py().parent
    pkg_name = project_name or _detect_project_package(root)

    registry = build_registry(_APPS_DIR)
    if module_name not in registry:
        typer.echo(
            f"Erreur : module '{module_name}' introuvable dans le registre Forge.\n"
            f"  Modules disponibles : {', '.join(sorted(registry))}",
            err=True,
        )
        raise typer.Exit(code=1)

    state = load_state(root)
    if module_name not in state.modules:
        typer.echo(f"Erreur : '{module_name}' n'est pas installé dans ce projet.", err=True)
        raise typer.Exit(code=1)

    dependents = _installed_dependents(module_name, state.modules, registry)
    if dependents:
        typer.echo(
            f"Erreur : impossible de retirer '{module_name}', "
            f"il est requis par : {', '.join(dependents)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    manifest = registry[module_name]
    _print_plan(manifest)

    if options.dry_run:
        typer.echo("\n[dry-run] Aucune modification effectuée.")
        return

    if not options.yes and not typer.confirm("\nConfirmer le retrait ?", default=False):
        typer.echo("Annulé.")
        raise typer.Exit(code=1)

    settings_path = root / pkg_name / "settings.py"
    _remove_configuration(manifest, root, settings_path)

    names = [environment.distribution_name(spec) for spec in manifest.python_packages]
    if names and not environment.uninstall_packages(names, root):
        typer.echo(
            f"  ! Désinstallation impossible, retirez à la main : {', '.join(names)}",
            err=True,
        )

    state.forget_module(module_name)
    save_state(root, state)

    typer.echo(f"\nOK : '{module_name}' retiré.")


def _installed_dependents(name: str, installed: dict, registry: dict[str, Manifest]) -> list[str]:
    """Modules installés (hors `name`) qui déclarent `name` en dépendance."""
    return sorted(
        other
        for other in installed
        if other != name and other in registry and name in registry[other].dependencies
    )


def _print_plan(manifest: Manifest) -> None:
    typer.echo(f"\nRetrait de '{manifest.name}' :")
    if manifest.installed_apps:
        typer.echo(f"  INSTALLED_APPS : {', '.join(manifest.installed_apps)}")
    if manifest.middleware:
        typer.echo(f"  MIDDLEWARE : {', '.join(manifest.middleware)}")
    if manifest.settings:
        typer.echo(f"  Réglages : {', '.join(manifest.settings)}")
    if manifest.urls:
        typer.echo(f"  URLs : {manifest.urls['include']}")
    if manifest.python_packages:
        typer.echo(f"  Paquets : {', '.join(manifest.python_packages)}")
    if "AUTH_USER_MODEL" in manifest.settings:
        typer.echo(
            "  ATTENTION : retirer AUTH_USER_MODEL après avoir appliqué des migrations "
            "casse le schéma de la base."
        )


def _remove_configuration(manifest: Manifest, root: Path, settings_path: Path) -> None:
    for app_label in manifest.installed_apps:
        if remove_from_list_setting(settings_path, "INSTALLED_APPS", app_label):
            typer.echo(f"  - '{app_label}' retiré de INSTALLED_APPS.")

    for middleware in manifest.middleware:
        if remove_from_list_setting(settings_path, "MIDDLEWARE", middleware):
            typer.echo(f"  - '{middleware}' retiré de MIDDLEWARE.")

    for key in manifest.settings:
        if remove_setting(settings_path, key):
            typer.echo(f"  - {key} retiré de settings.py.")

    if manifest.urls and unwire_url_include(root, manifest.urls["include"]):
        typer.echo(f"  - {manifest.urls['include']} retiré du routeur principal.")
