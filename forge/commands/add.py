"""
Logique métier de `forge add <app_name>`.

Responsabilité
--------------
1. Créer l'application Django via `django-admin startapp`.
2. Injecter l'app dans `INSTALLED_APPS`.
3. Créer et brancher `urls.py` dans le routeur principal (sauf `--no-urls`).
4. Générer l'arborescence `templates/<app_name>/` et les fichiers HTML
   demandés (option `--templates`), ainsi que la vue et la route de chaque
   page pour qu'elle soit accessible immédiatement (sauf `--no-urls`).

Ce module ne contient aucune référence à Typer — il est appelable
directement en Python et entièrement testable sans CLI.
"""

from __future__ import annotations

from pathlib import Path

import typer

from forge.commands._options import AddOptions
from forge.core.config_manager import add_to_installed_apps
from forge.core.engine import find_manage_py, run_django_command
from forge.core.urls_manager import find_main_urls, wire_url_include

# Contenu minimal du urls.py local généré pour chaque nouvelle app
_LOCAL_URLS_TEMPLATE = '''\
"""URL configuration for the {app_name} application."""

from django.urls import path

from . import views

app_name = "{app_name}"

urlpatterns: list = []
'''

# Variante générée quand `--templates` fournit des pages : une route par vue,
# pour que chaque page soit accessible immédiatement.
_LOCAL_URLS_WITH_ROUTES_TEMPLATE = '''\
"""URL configuration for the {app_name} application."""

from django.urls import path

from . import views

app_name = "{app_name}"

urlpatterns: list = [
{routes}]
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

# Vue générée pour chaque page de `--templates`, ajoutée à <app_name>/views.py
_VIEW_FUNCTION_TEMPLATE = '''

def {view_name}(request):
    return render(request, "{app_name}/{filename}")
'''


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

    # Une route + une vue par page ne peuvent être générées que si le
    # routeur local existe (pas de sens avec --no-urls).
    view_names = (
        [_template_stem(f) for f in options.templates]
        if options.templates and not options.no_urls
        else []
    )

    if not options.no_urls:
        _create_local_urls(app_name, root, view_names=view_names)
        _wire_urls_in_project_router(app_name, root)

    if options.templates is not None:
        _create_template_tree(app_name, root, options.templates)
        if view_names:
            _create_view_functions(app_name, root, view_names)

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


def _create_local_urls(
    app_name: str,
    project_root: Path,
    view_names: list[str] | None = None,
) -> None:
    """
    Génère `<app_name>/urls.py`.

    Sans `view_names` : routeur vide nommé (comportement historique).
    Avec `view_names` : une route `path("<nom>/", views.<nom>, name="<nom>")`
    par vue, pour que chaque page de `--templates` soit accessible
    immédiatement.
    """
    urls_path = project_root / app_name / "urls.py"

    if view_names:
        routes = "".join(
            f'    path("{name}/", views.{name}, name="{name}"),\n' for name in view_names
        )
        content = _LOCAL_URLS_WITH_ROUTES_TEMPLATE.format(app_name=app_name, routes=routes)
    else:
        content = _LOCAL_URLS_TEMPLATE.format(app_name=app_name)

    urls_path.write_text(content, encoding="utf-8")
    typer.echo(f"  • {app_name}/urls.py créé.")


def _wire_urls_in_project_router(app_name: str, project_root: Path) -> None:
    """Insère `path("<app_name>/", include(...))` dans le urls.py principal."""
    if find_main_urls(project_root) is None:
        typer.echo("  ⚠ urls.py principal introuvable — branchement ignoré.", err=True)
        return

    modified = wire_url_include(
        project_root,
        url_prefix=f"{app_name}/",
        include_target=f"{app_name}.urls",
        namespace=app_name,
    )
    if modified:
        typer.echo(f"  • {app_name}.urls branché dans le routeur principal.")


def _template_stem(filename: str) -> str:
    """Nom de fichier sans extension `.html` (ajoutée si absente)."""
    return filename[:-len(".html")] if filename.endswith(".html") else filename


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

    for raw_name in html_files:
        stem = _template_stem(raw_name)
        filename = f"{stem}.html"
        page_title = stem.replace("_", " ").title()
        (template_dir / filename).write_text(
            _HTML_TEMPLATE.format(page_title=page_title),
            encoding="utf-8",
        )
        typer.echo(f"  • templates/{app_name}/{filename} généré.")


def _create_view_functions(app_name: str, project_root: Path, view_names: list[str]) -> None:
    """
    Ajoute une fonction de vue par page de `--templates` dans
    `<app_name>/views.py` (route déjà créée par `_create_local_urls`).
    """
    views_path = project_root / app_name / "views.py"
    content = views_path.read_text(encoding="utf-8")

    if "from django.shortcuts import render" not in content:
        content = "from django.shortcuts import render\n\n" + content

    for name in view_names:
        content += _VIEW_FUNCTION_TEMPLATE.format(
            view_name=name, app_name=app_name, filename=f"{name}.html"
        )

    views_path.write_text(content, encoding="utf-8")
    typer.echo(f"  • Vues générées dans {app_name}/views.py.")


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


