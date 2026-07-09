"""
Logique métier de `forge init <project_name>`.

Responsabilité
--------------
1. Appeler `django-admin startproject` via le moteur.
2. Remplacer le `settings.py` généré par le squelette Forge.
3. Installer les modules passés via `--install` si présents.

Ce module ne contient aucune référence à Typer — il est appelable
directement en Python et entièrement testable sans CLI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import typer

from forge.commands._options import InitOptions
from forge.core.config_manager import add_to_installed_apps
from forge.core.dependency_resolver import build_registry, resolve

# Chemin vers les templates embarqués dans le package
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_PROJECT_BASE_DIR = _TEMPLATES_DIR / "project_base"
_APPS_DIR = _TEMPLATES_DIR / "apps"

# Services disponibles — utilisés uniquement pour un message d'info, pas de
# validation stricte ici (la validation appartient à configure.py).
_AVAILABLE_SERVICES = {"redis", "celery", "drf", "channels", "pgsql", "mysql"}


# ---------------------------------------------------------------------------
# Point d'entrée de la commande
# ---------------------------------------------------------------------------


def run(project_name: str, options: InitOptions, output_dir: Path | None = None) -> None:
    """
    Exécute `forge init`.

    Parameters
    ----------
    project_name:
        Nom du projet Django à créer (doit être un identifiant Python valide).
    options:
        Options de la commande (voir :class:`~forge.commands._options.InitOptions`).
    output_dir:
        Répertoire cible. Défaut : répertoire courant. Paramètre principalement
        utilisé dans les tests pour isoler les effets de bord.
    """
    cwd = output_dir or Path.cwd()
    project_dir = cwd / project_name

    _validate_project_name(project_name)
    _validate_target_directory(project_dir)

    typer.echo(f"→ Initialisation du projet '{project_name}'...")

    _run_django_startproject(project_name, cwd)
    _apply_forge_settings_overlay(project_dir, project_name)

    typer.echo(f"✓ Projet '{project_name}' créé.")

    if options.install:
        _install_modules(options.install, project_dir, project_name)


# ---------------------------------------------------------------------------
# Étapes internes
# ---------------------------------------------------------------------------


def _validate_project_name(name: str) -> None:
    """Lève une erreur si `name` n'est pas un identifiant Python valide."""
    if not name.isidentifier():
        typer.echo(
            f"✗ '{name}' n'est pas un nom de projet valide "
            "(doit être un identifiant Python : lettres, chiffres, underscores).",
            err=True,
        )
        raise typer.Exit(code=1)


def _validate_target_directory(project_dir: Path) -> None:
    """Lève une erreur si le dossier cible existe déjà."""
    if project_dir.exists():
        typer.echo(
            f"✗ Le répertoire '{project_dir}' existe déjà. "
            "Supprimez-le ou choisissez un autre nom.",
            err=True,
        )
        raise typer.Exit(code=1)


def _run_django_startproject(project_name: str, cwd: Path) -> None:
    """Lance `django-admin startproject` dans `cwd`."""
    result = subprocess.run(
        [sys.executable, "-m", "django", "startproject", project_name, str(cwd / project_name)],
        cwd=cwd,
    )
    if result.returncode != 0:
        typer.echo("✗ django-admin startproject a échoué.", err=True)
        raise typer.Exit(code=result.returncode)


def _apply_forge_settings_overlay(project_dir: Path, project_name: str) -> None:
    """
    Copie les blueprints Forge (`settings.py`, `urls.py`) dans le package
    Django et substitue `{{project_name}}` par la valeur réelle.

    Si un blueprint est absent, le fichier Django natif est conservé.
    """
    package_dir = project_dir / project_name

    for filename in ("settings.py", "urls.py", "welcome.py"):
        blueprint = _PROJECT_BASE_DIR / filename
        if not blueprint.exists():
            continue

        content = blueprint.read_text(encoding="utf-8")
        content = content.replace("{{project_name}}", project_name)
        target = package_dir / filename
        target.write_text(content, encoding="utf-8")
        typer.echo(f"  • {filename} Forge appliqué.")

    # Créer le .env initial si absent
    _create_initial_dotenv(project_dir)


def _register_forge_test(project_dir: Path, project_name: str) -> None:
    """
    Installe `forge_test` dans `INSTALLED_APPS` — requis par le système
    de dépendances (toute chaîne dépend ultimement de forge-test).
    """
    settings_path = project_dir / project_name / "settings.py"
    modified = add_to_installed_apps(settings_path, "forge_test")
    if modified:
        typer.echo("  • forge_test ajouté à INSTALLED_APPS.")


def _create_initial_dotenv(project_dir: Path) -> None:
    """
    Génère un `.env` minimal à la racine du projet si absent.
    Contient les clés nécessaires au blueprint settings.py.
    """
    env_file = project_dir / ".env"
    if env_file.exists():
        return

    import secrets
    secret_key = secrets.token_urlsafe(50)

    content = f"""\
SECRET_KEY={secret_key}
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
"""
    env_file.write_text(content, encoding="utf-8")
    typer.echo("  • .env initial généré.")


def _install_modules(
    module_names: list[str],
    project_dir: Path,
    project_name: str,
) -> None:
    """
    Résout et installe chaque module de `module_names` via le resolver.

    Délègue à `install.run()` pour éviter la duplication de logique.
    Import local pour éviter la dépendance circulaire au niveau module.
    """
    from forge.commands.install import run as install_run
    from forge.commands._options import InstallOptions

    for module_name in module_names:
        typer.echo(f"\n→ Installation de '{module_name}'...")
        install_run(
            module_name=module_name,
            options=InstallOptions(),
            project_dir=project_dir,
            project_name=project_name,
        )