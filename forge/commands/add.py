"""
Logique métier de `forge add <app_name>`.

Responsabilité
--------------
1. Créer l'application Django via `django-admin startapp`.
2. Injecter l'app dans `INSTALLED_APPS`.
3. Créer et brancher `urls.py` dans le routeur principal (sauf `--no-urls`).
4. Générer l'arborescence `templates/<app_name>/` et les fichiers HTML
   demandés (option `--templates`).

Ce module ne contient aucune référence à Typer — il est appelable
directement en Python et entièrement testable sans CLI.
"""

from __future__ import annotations

from pathlib import Path

import typer

from forge.commands._options import AddOptions
from forge.core.config_manager import add_to_installed_apps
from forge.core.engine import find_manage_py, run_django_command

# Contenu minimal du urls.py local généré pour chaque nouvelle app
_LOCAL_URLS_TEMPLATE = '''\
"""URL configuration for the {app_name} application."""

from django.urls import path

from . import views

app_name = "{app_name}"

urlpatterns: list = []
'''

# Snippet d'inclusion injecté dans le urls.py principal du projet
_URL_INCLUDE_SNIPPET = (
    'path("{app_name}/", include("{app_name}.urls", namespace="{app_name}")),\n'
)

# Template HTML minimaliste généré pour chaque fichier demandé
_HTML_TEMPLATE = """\
{{% extends "base.html" %}}

{{% block content %}}
<h1>{page_title}</h1>
{{% endblock %}}
"""


# ---------------------------------------------------------------------------
# Point d'entrée de la commande
# ---------------------------------------------------------------------------


def run(app_name: str, options: AddOptions, project_root: Path | None = None) -> None:
    """
    Exécute `forge add`.

    Parameters
    ----------
    app_name:
        Nom de l'application Django à créer (identifiant Python valide).
    options:
        Options de la commande (voir :class:`~forge.commands._options.AddOptions`).
    project_root:
        Racine du projet (là où se trouve `manage.py`). Déduit
        automatiquement si `None`.
    """
    root = project_root or find_manage_py().parent

    _validate_app_name(app_name)
    _validate_app_does_not_exist(app_name, root)

    typer.echo(f"→ Création de l'application '{app_name}'...")

    _run_startapp(app_name, root)
    _register_in_installed_apps(app_name, root)

    if not options.no_urls:
        _create_local_urls(app_name, root)
        _wire_urls_in_project_router(app_name, root)

    if options.templates is not None:
        _create_template_tree(app_name, root, options.templates)

    typer.echo(f"✓ Application '{app_name}' créée et configurée.")


# ---------------------------------------------------------------------------
# Étapes internes
# ---------------------------------------------------------------------------


def _validate_app_name(name: str) -> None:
    if not name.isidentifier():
        typer.echo(f"✗ '{name}' n'est pas un nom d'application valide.", err=True)
        raise typer.Exit(code=1)


def _validate_app_does_not_exist(app_name: str, project_root: Path) -> None:
    if (project_root / app_name).exists():
        typer.echo(f"✗ Le dossier '{app_name}' existe déjà.", err=True)
        raise typer.Exit(code=1)


def _run_startapp(app_name: str, project_root: Path) -> None:
    code = run_django_command(["startapp", app_name], project_root=project_root)
    if code != 0:
        typer.echo("✗ django startapp a échoué.", err=True)
        raise typer.Exit(code=code)


def _register_in_installed_apps(app_name: str, project_root: Path) -> None:
    """Trouve settings.py et injecte l'app dans INSTALLED_APPS."""
    settings_path = _find_settings(project_root)
    modified = add_to_installed_apps(settings_path, app_name)
    if modified:
        typer.echo(f"  • '{app_name}' ajouté à INSTALLED_APPS.")


def _create_local_urls(app_name: str, project_root: Path) -> None:
    """Génère `<app_name>/urls.py` avec un routeur vide nommé."""
    urls_path = project_root / app_name / "urls.py"
    urls_path.write_text(
        _LOCAL_URLS_TEMPLATE.format(app_name=app_name),
        encoding="utf-8",
    )
    typer.echo(f"  • {app_name}/urls.py créé.")


def _wire_urls_in_project_router(app_name: str, project_root: Path) -> None:
    """
    Insère `path("<app_name>/", include(...))` dans `urlpatterns`.

    Stratégie : repère la ligne `urlpatterns = [` puis cherche le `]`
    fermant en comptant la profondeur des crochets — immunisé contre les
    crochets dans les commentaires ou les autres listes du fichier.
    """
    main_urls = _find_main_urls(project_root)
    if main_urls is None:
        typer.echo("  ⚠ urls.py principal introuvable — branchement ignoré.", err=True)
        return

    source = main_urls.read_text(encoding="utf-8")

    snippet = f'    path("{app_name}/", include("{app_name}.urls", namespace="{app_name}")),\n'

    if f'"{app_name}/"' in source:
        return  # déjà branché

    # Garantir que django.urls.include est importé
    if "include" not in source:
        source = source.replace(
            "from django.urls import path",
            "from django.urls import include, path",
        )

    # Trouver l'index du ] fermant urlpatterns en comptant la profondeur
    marker = "urlpatterns"
    marker_pos = source.find(marker)
    if marker_pos == -1:
        typer.echo("  ⚠ urlpatterns introuvable — branchement ignoré.", err=True)
        return

    # Avancer jusqu'au [ ouvrant de urlpatterns
    open_bracket = source.find("[", marker_pos)
    if open_bracket == -1:
        return

    depth = 0
    close_bracket = -1
    for i, ch in enumerate(source[open_bracket:], start=open_bracket):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                close_bracket = i
                break

    if close_bracket == -1:
        return

    source = source[:close_bracket] + snippet + source[close_bracket:]
    main_urls.write_text(source, encoding="utf-8")
    typer.echo(f"  • {app_name}.urls branché dans le routeur principal.")


def _create_template_tree(
    app_name: str,
    project_root: Path,
    html_files: list[str],
) -> None:
    """
    Crée `<app_name>/templates/<app_name>/` et génère les fichiers HTML.

    Si `html_files` est vide, l'arborescence est créée sans fichier.
    """
    template_dir = project_root / app_name / "templates" / app_name
    template_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"  • Arborescence templates/{app_name}/ créée.")

    for filename in html_files:
        if not filename.endswith(".html"):
            filename = filename + ".html"
        page_title = filename.replace(".html", "").replace("_", " ").title()
        (template_dir / filename).write_text(
            _HTML_TEMPLATE.format(page_title=page_title),
            encoding="utf-8",
        )
        typer.echo(f"  • templates/{app_name}/{filename} généré.")


# ---------------------------------------------------------------------------
# Helpers de localisation
# ---------------------------------------------------------------------------


def _find_settings(project_root: Path) -> Path:
    """
    Cherche récursivement `settings.py` dans `project_root`.
    Prend le premier trouvé (exclut les fichiers de test).
    """
    candidates = [
        p for p in project_root.rglob("settings.py")
        if "test" not in p.parts and "migrations" not in p.parts
    ]
    if not candidates:
        raise FileNotFoundError(f"settings.py introuvable dans {project_root}")
    return candidates[0]


def _find_main_urls(project_root: Path) -> Path | None:
    """
    Cherche le urls.py principal du projet (celui qui contient `urlpatterns`).
    Retourne `None` si introuvable.
    """
    for candidate in project_root.rglob("urls.py"):
        if "test" not in candidate.parts and "migrations" not in candidate.parts:
            content = candidate.read_text(encoding="utf-8")
            if "urlpatterns" in content:
                return candidate
    return None