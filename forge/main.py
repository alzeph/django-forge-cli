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

from pathlib import Path
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
    template: Optional[str] = typer.Option(
        None,
        "--template",
        help=(
            "Blueprint de projet à appliquer (scaffold complet et pré-configuré). "
            "Ex: --template saas. Liste : 'forge templates'."
        ),
        metavar="BLUEPRINT",
    ),
    install: Optional[str] = typer.Option(
        None,
        "--install",
        help="Modules Forge à installer, séparés par des virgules. Ex: forge-auth",
        metavar="MODULES",
    ),
) -> None:
    """Initialise un nouveau projet Django Forge."""
    if template:
        from forge.commands.blueprint import run as run_blueprint

        run_blueprint(project_name=project_name, blueprint_name=template)
        return

    from forge.commands.init import run

    options = InitOptions(
        install=[m.strip() for m in install.split(",")] if install else [],
    )
    run(project_name=project_name, options=options)


# ---------------------------------------------------------------------------
# forge templates
# ---------------------------------------------------------------------------


@app.command(name="templates")
def templates_command() -> None:
    """Liste les blueprints de projet disponibles pour `forge init --template`."""
    from forge.commands.blueprint import print_catalog

    print_catalog()


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


@app.callback(invoke_without_command=True)
def passthrough_callback(
    ctx: typer.Context,
) -> None:
    """
    Intercepte toutes les commandes non reconnues par Forge et les redirige
    vers ``manage.py`` de Django.

    Exemples : ``forge makemigrations``, ``forge migrate``, ``forge shell``.
    """
    if ctx.invoked_subcommand is not None:
        return  # commande Forge reconnue → laisser Typer la gérer

    # Aucune sous-commande Forge → passe-plat vers Django
    args = ctx.args
    if not args:
        return  # affiche l'aide (no_args_is_help=True)

    from forge.core.engine import find_manage_py, run_django_command

    try:
        code = run_django_command(args)
        raise typer.Exit(code=code)
    except FileNotFoundError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Point d'entrée script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()