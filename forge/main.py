"""
forge/main.py
=============
Point d'entrée unique de la CLI Django Forge.

Rôle strict : déclarer les commandes Typer et translater les paramètres
CLI vers les dataclasses Options, puis déléguer à la logique métier.

Ce fichier ne contient aucune logique métier.
Pour modifier le comportement d'une commande → éditer son fichier dans
``forge/commands/``.
Pour ajouter une option → éditer ``forge/commands/_options.py`` + le fichier
de commande concerné + la signature Typer ici.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer

from forge.commands._options import (
    AddOptions,
    ConfigureOptions,
    InitOptions,
    InstallOptions,
)
from forge.commands.configure import AVAILABLE_SERVICES

app = typer.Typer(
    name="forge",
    help="Django Forge — méta-framework CLI pour Django.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)


# ---------------------------------------------------------------------------
# forge init
# ---------------------------------------------------------------------------


@app.command(name="init")
def init_command(
    project_name: str = typer.Argument(..., help="Nom du projet Django à créer."),
    install: Optional[str] = typer.Option(
        None,
        "--install",
        help="Modules Forge à installer, séparés par des virgules. Ex: forge-auth,forge-notification",
        metavar="MODULES",
    ),
) -> None:
    """Initialise un nouveau projet Django Forge."""
    from forge.commands.init import run

    options = InitOptions(
        install=[m.strip() for m in install.split(",")] if install else [],
    )
    run(project_name=project_name, options=options)


# ---------------------------------------------------------------------------
# forge add
# ---------------------------------------------------------------------------


@app.command(name="add")
def add_command(
    app_name: str = typer.Argument(..., help="Nom de l'application Django à créer."),
    no_urls: bool = typer.Option(
        False,
        "--no-urls",
        help="Désactive la création et le branchement du fichier urls.py.",
    ),
    templates: Optional[str] = typer.Option(
        None,
        "--templates",
        help=(
            "Génère l'arborescence templates. "
            "Sans valeur : crée le dossier vide. "
            "Avec valeur : génère les fichiers HTML listés. "
            "Ex: --templates=index.html,detail.html"
        ),
        metavar="FILES",
    ),
) -> None:
    """Crée une application Django auto-configurée."""
    from forge.commands.add import run

    # --templates sans valeur = liste vide (arborescence créée, pas de fichiers)
    # --templates=index.html,detail.html = fichiers listés
    # absent = None (pas de templates)
    if templates is not None:
        template_files = [f.strip() for f in templates.split(",") if f.strip()]
    else:
        template_files = None

    options = AddOptions(
        no_urls=no_urls,
        templates=template_files,
    )
    run(app_name=app_name, options=options)


# ---------------------------------------------------------------------------
# forge install
# ---------------------------------------------------------------------------


@app.command(name="install")
def install_command(
    module_name: str = typer.Argument(
        ...,
        help="Nom du module Forge à installer. Ex: forge-auth",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Affiche le plan d'installation sans modifier le projet.",
    ),
) -> None:
    """Installe un module Forge et sa chaîne de dépendances."""
    from forge.commands.install import run

    options = InstallOptions(dry_run=dry_run)
    run(module_name=module_name, options=options)


# ---------------------------------------------------------------------------
# forge configure
# ---------------------------------------------------------------------------


@app.command(name="configure")
def configure_command(
    service: str = typer.Argument(
        ...,
        help=f"Service à configurer. Disponibles : {', '.join(AVAILABLE_SERVICES)}",
    ),
    postgis: bool = typer.Option(
        False,
        "--postgis",
        help="Active le support PostGIS (uniquement avec le service pgsql).",
    ),
    dev: Optional[str] = typer.Option(
        None,
        "--dev",
        help=(
            "Service de base de données alternatif pour l'environnement DEBUG. "
            "Ex: --dev=sqlite génère un bloc if DEBUG / else dans settings.py."
        ),
        metavar="SERVICE",
    ),
) -> None:
    """Configure un service dans settings.py (redis, celery, drf, channels, pgsql, mysql)."""
    from forge.commands.configure import run

    options = ConfigureOptions(postgis=postgis, dev=dev)
    run(service=service, options=options)


# ---------------------------------------------------------------------------
# Mode passe-plat Django
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Mode passe-plat Django
# ---------------------------------------------------------------------------


def _forge_command_names() -> set[str]:
    """Noms des commandes Forge natives, dérivés dynamiquement de l'app Typer.

    Ajouter une commande via ``@app.command(name=...)`` l'inclut
    automatiquement — aucune liste à maintenir en double.
    """
    names: set[str] = set()
    for info in app.registered_commands:
        names.add(info.name or info.callback.__name__)
    return names


def main() -> None:
    """Point d'entrée de la CLI ``forge``.

    Route l'invocation avant que Typer ne prenne la main :

    - commande Forge native (``init``, ``add``, ``install``, ``configure``),
      option globale (``--help``, ``--install-completion``) ou aucun argument
      → délégué à l'application Typer ;
    - toute autre commande (``migrate``, ``makemigrations``, ``runserver``,
      ``shell``, ...) → passe-plat vers le ``manage.py`` du projet Django.

    Ce routage est fait ici — et non dans un ``@app.callback`` — car Typer/Click
    résout le premier argument comme un nom de sous-commande et échoue *avant*
    d'exécuter le callback si la commande est inconnue.
    """
    argv = sys.argv[1:]

    # Premier jeton positionnel (on ignore les options globales type --help).
    first_positional = next((arg for arg in argv if not arg.startswith("-")), None)

    if first_positional is None or first_positional in _forge_command_names():
        app()
        return

    # Passe-plat : commande inconnue de Forge → Django natif.
    from forge.core.engine import run_django_command

    try:
        raise SystemExit(run_django_command(argv))
    except FileNotFoundError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Point d'entrée script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()