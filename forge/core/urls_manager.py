"""
Localisation et modification programmatique du urls.py principal d'un projet.

Regroupe la logique partagée par `forge add` (branchement d'une app locale)
et `forge install` (branchement d'un module réutilisable comme forge_auth) :
les deux ont besoin de trouver le urls.py racine du projet et d'y insérer un
`include(...)`.
"""

from __future__ import annotations

import ast
from pathlib import Path


# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------


def _find_settings(project_root: Path) -> Path:
    """Cherche récursivement `settings.py` dans `project_root`."""
    candidates = [
        p for p in project_root.rglob("settings.py")
        if "test" not in p.parts and "migrations" not in p.parts
    ]
    if not candidates:
        raise FileNotFoundError(f"settings.py introuvable dans {project_root}")
    return candidates[0]


def _root_urlconf(settings_path: Path) -> str | None:
    """
    Extrait la valeur de `ROOT_URLCONF` (ex: `"myproject.urls"`) depuis
    `settings_path`. Retourne `None` si le réglage est absent ou n'est pas
    un littéral string simple.
    """
    tree = ast.parse(settings_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "ROOT_URLCONF" for t in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def find_main_urls(project_root: Path) -> Path | None:
    """
    Localise le urls.py principal du projet via `ROOT_URLCONF` dans
    `settings.py`. Retourne `None` si introuvable.

    Une recherche par contenu (rglob + "urlpatterns" présent) confondrait ce
    fichier avec le urls.py d'une app locale fraîchement créée ou celui d'un
    module installé comme forge_auth — l'app/le module finirait par s'inclure
    lui-même, provoquant une boucle d'inclusion infinie au chargement des URLs.
    """
    try:
        settings_path = _find_settings(project_root)
    except FileNotFoundError:
        return None

    module_path = _root_urlconf(settings_path)
    if module_path is None:
        return None

    urls_path = project_root / Path(*module_path.split(".")).with_suffix(".py")
    return urls_path if urls_path.is_file() else None


# ---------------------------------------------------------------------------
# Branchement
# ---------------------------------------------------------------------------


def wire_url_include(
    project_root: Path,
    url_prefix: str,
    include_target: str,
    namespace: str,
) -> bool:
    """
    Insère `path("<url_prefix>", include("<include_target>", namespace=...))`
    dans les `urlpatterns` du urls.py principal du projet.

    Stratégie : repère la ligne `urlpatterns = [` puis cherche le `]`
    fermant en comptant la profondeur des crochets — immunisé contre les
    crochets dans les commentaires ou les autres listes du fichier.

    Idempotent : si `include_target` figure déjà dans le fichier, ne fait
    rien.

    Retourne `True` si le fichier a été modifié, `False` si le urls.py
    principal est introuvable, malformé, ou si le branchement était déjà
    présent.
    """
    main_urls = find_main_urls(project_root)
    if main_urls is None:
        return False

    source = main_urls.read_text(encoding="utf-8")

    if f'include("{include_target}"' in source:
        return False  # déjà branché

    snippet = f'    path("{url_prefix}", include("{include_target}", namespace="{namespace}")),\n'

    if "include" not in source:
        source = source.replace(
            "from django.urls import path",
            "from django.urls import include, path",
        )

    marker_pos = source.find("urlpatterns")
    if marker_pos == -1:
        return False

    open_bracket = source.find("[", marker_pos)
    if open_bracket == -1:
        return False

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
        return False

    source = source[:close_bracket] + snippet + source[close_bracket:]
    main_urls.write_text(source, encoding="utf-8")
    return True
