"""
forge/commands/blueprint.py
===========================
Système de *blueprints* de projet — presets qui scaffoldent un projet Django
complet et pré-configuré en une seule commande.

Un blueprint est un dossier ``forge/templates/blueprints/<nom>/`` contenant un
fichier ``blueprint.json`` qui décrit, de façon déclarative :

- ``apps``      : applications locales à créer (``forge add``) ;
- ``install``   : modules Forge à installer (``forge install``) ;
- ``configure`` : services à configurer (``forge configure``).

L'orchestration **réutilise** intégralement la logique métier des commandes
existantes : un blueprint ne fait qu'enchaîner ``init → add → install →
configure`` dans un ordre déterministe. Ajouter un blueprint = déposer un
dossier avec un ``blueprint.json``, sans toucher au code.

    forge init monapp --template saas      # scaffold complet
    forge templates                        # liste les blueprints disponibles
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import typer

from forge.commands._options import (
    AddOptions,
    ConfigureOptions,
    InitOptions,
    InstallOptions,
)

# Racine des blueprints embarqués (packagés via `forge/**/*.json`).
BLUEPRINTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "blueprints"


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigureStep:
    """Une étape ``forge configure`` déclarée dans un blueprint."""

    service: str
    postgis: bool = False
    dev: str | None = None


@dataclass(frozen=True)
class Blueprint:
    """Représentation en mémoire d'un ``blueprint.json``."""

    name: str
    description: str = ""
    apps: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)
    configure: list[ConfigureStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Blueprint":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            apps=list(data.get("apps", [])),
            install=list(data.get("install", [])),
            configure=[
                ConfigureStep(
                    service=step["service"],
                    postgis=step.get("postgis", False),
                    dev=step.get("dev"),
                )
                for step in data.get("configure", [])
            ],
        )


# ---------------------------------------------------------------------------
# Chargement / catalogue
# ---------------------------------------------------------------------------


def list_blueprints() -> list[Blueprint]:
    """Retourne tous les blueprints disponibles, triés par nom."""
    if not BLUEPRINTS_DIR.is_dir():
        return []

    blueprints: list[Blueprint] = []
    for child in sorted(BLUEPRINTS_DIR.iterdir()):
        manifest = child / "blueprint.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            blueprints.append(Blueprint.from_dict(data))
    return blueprints


def load_blueprint(name: str) -> Blueprint:
    """Charge un blueprint par son nom, ou sort en erreur s'il est inconnu."""
    manifest = BLUEPRINTS_DIR / name / "blueprint.json"
    if not manifest.is_file():
        available = ", ".join(b.name for b in list_blueprints()) or "(aucun)"
        typer.echo(
            f"✗ Blueprint '{name}' introuvable.\n"
            f"  Blueprints disponibles : {available}",
            err=True,
        )
        raise typer.Exit(code=1)

    data = json.loads(manifest.read_text(encoding="utf-8"))
    return Blueprint.from_dict(data)


def print_catalog() -> None:
    """Affiche la liste des blueprints (logique de ``forge templates``)."""
    blueprints = list_blueprints()
    if not blueprints:
        typer.echo("Aucun blueprint de projet disponible.")
        return

    typer.echo("Blueprints de projet disponibles :\n")
    width = max(len(b.name) for b in blueprints)
    for bp in blueprints:
        typer.echo(f"  • {bp.name:<{width}}  {bp.description}")
    typer.echo("\nUtilisation : forge init <nom_projet> --template <blueprint>")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    project_name: str,
    blueprint_name: str,
    output_dir: Path | None = None,
) -> None:
    """
    Exécute ``forge init <project_name> --template <blueprint_name>``.

    Enchaîne, dans l'ordre : création du projet, création des apps locales,
    installation des modules, puis configuration des services. Cet ordre
    garantit les synergies (ex. ``forge-auth`` installé avant ``configure drf``
    active l'authentification JWT).
    """
    # Imports paresseux : réutilise la logique métier des commandes existantes
    # et facilite le mock dans les tests.
    from forge.commands import add as add_cmd
    from forge.commands import configure as configure_cmd
    from forge.commands import init as init_cmd
    from forge.commands import install as install_cmd

    blueprint = load_blueprint(blueprint_name)
    output_dir = output_dir or Path.cwd()
    project_root = output_dir / project_name

    typer.echo(f"→ Projet '{project_name}' — blueprint '{blueprint.name}'")

    init_cmd.run(project_name=project_name, options=InitOptions(), output_dir=output_dir)

    for app_name in blueprint.apps:
        add_cmd.run(app_name=app_name, options=AddOptions(), project_root=project_root)

    for module_name in blueprint.install:
        install_cmd.run(
            module_name=module_name,
            options=InstallOptions(),
            project_dir=project_root,
            project_name=project_name,
        )

    for step in blueprint.configure:
        configure_cmd.run(
            service=step.service,
            options=ConfigureOptions(postgis=step.postgis, dev=step.dev),
            project_root=project_root,
        )

    typer.echo(f"✓ Projet '{project_name}' prêt (blueprint '{blueprint.name}').")
