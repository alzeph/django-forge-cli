"""
Logique métier de `forge install <module_name>`.

Responsabilité
--------------
1. Construire le registre des manifestes disponibles.
2. Résoudre l'arbre de dépendances via `dependency_resolver`.
3. Vérifier que la version de Python satisfait la chaîne de modules.
4. Pour chaque module dans l'ordre topologique, s'il embarque des sources
   (cas historique), les copier, l'injecter dans `INSTALLED_APPS` et brancher
   son urls.py.
5. Appliquer la configuration minimale déclarée par les manifestes
   (`installed_apps`, `middleware`, `settings`, `urls`).
6. Déclencher `forge configure` pour les services requis.
7. Installer depuis PyPI les paquets requis (`python_packages`).
8. Vérifier / compléter les clés d'environnement dans `.env`.

Option `--dry-run` : affiche le plan sans aucune écriture disque.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from pycatch import Err, Ok, catch

from forge.commands._options import ConfigureOptions, InstallOptions
from forge.core.config_manager import (
    add_simple_setting,
    add_to_installed_apps,
    add_to_list_setting,
)
from forge.core.dependency_resolver import build_registry, resolve_safe
from forge.core.urls_manager import wire_url_include

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_APPS_DIR = _TEMPLATES_DIR / "apps"


# ---------------------------------------------------------------------------
# Point d'entrée de la commande
# ---------------------------------------------------------------------------


def run(
    module_name: str,
    options: InstallOptions,
    project_dir: Path | None = None,
    project_name: str | None = None,
) -> None:
    """
    Exécute `forge install`.

    Parameters
    ----------
    module_name:
        Identifiant du module à installer (ex : `"forge-notification"`).
    options:
        Options de la commande (voir :class:`~forge.commands._options.InstallOptions`).
    project_dir:
        Racine du projet hôte. Déduit depuis `manage.py` si `None`.
    project_name:
        Nom du package Django principal (sous-dossier contenant `settings.py`).
        Déduit automatiquement si `None`.
    """
    from forge.core.engine import find_manage_py

    root = project_dir or find_manage_py().parent
    pkg_name = project_name or _detect_project_package(root)

    registry = build_registry(_APPS_DIR)

    if module_name not in registry:
        typer.echo(
            f"✗ Module '{module_name}' introuvable dans le registre Forge.\n"
            f"  Modules disponibles : {', '.join(sorted(registry))}",
            err=True,
        )
        raise typer.Exit(code=1)

    match resolve_safe(module_name, registry):
        case Ok(plan):
            pass
        case Err(error):
            message = error.args[0] if isinstance(error, KeyError) else error
            typer.echo(f"✗ {message}", err=True)
            raise typer.Exit(code=1)

    _print_plan(plan, module_name)

    python_error = _check_python_version(plan.min_python)

    if options.dry_run:
        if python_error:
            typer.echo(f"\n⚠ {python_error}", err=True)
        typer.echo("\n[dry-run] Aucune modification effectuée.")
        return

    if python_error:
        typer.echo(f"✗ {python_error}", err=True)
        raise typer.Exit(code=1)

    settings_path = root / pkg_name / "settings.py"

    for mod in plan.order:
        _install_single_module(mod, root, settings_path)

    _apply_manifest_config(plan, root, settings_path, project_name=pkg_name)

    if plan.services_to_configure:
        _configure_services(plan.services_to_configure, root)

    if plan.python_packages:
        _install_python_packages(plan.python_packages)

    if plan.env_keys:
        _ensure_env_keys(plan.env_keys, root)

    if plan.installed_apps:
        typer.echo("  → Appliquez les migrations : forge migrate")

    typer.echo(f"\n✓ '{module_name}' et ses dépendances installés avec succès.")


# ---------------------------------------------------------------------------
# Étapes internes
# ---------------------------------------------------------------------------


def _print_plan(plan, module_name: str) -> None:
    typer.echo(f"\nPlan d'installation pour '{module_name}' :")
    for i, mod in enumerate(plan.order, 1):
        typer.echo(f"  {i}. {mod}")
    if plan.services_to_configure:
        typer.echo(f"  Services à configurer : {', '.join(plan.services_to_configure)}")
    if plan.python_packages:
        typer.echo(f"  Paquets PyPI à installer : {', '.join(plan.python_packages)}")
    if plan.env_keys:
        typer.echo(f"  Clés d'environnement requises : {', '.join(plan.env_keys)}")


def _install_single_module(
    module_name: str,
    project_root: Path,
    settings_path: Path,
) -> None:
    """
    Copie les sources du module, l'injecte dans INSTALLED_APPS et branche son
    urls.py s'il en a un.

    Ne concerne que les modules qui embarquent leurs sources. Un module
    distribué sur PyPI (`forge-auth`, `forge-log`, …) n'a qu'un `manifest.json` :
    il est installé par `_install_python_packages` et configuré par
    `_apply_manifest_config`, il n'y a donc rien à copier ici.

    Le nom du dossier source suit la convention `forge_auth` (underscores)
    pour le nom Python, `forge-auth` (tirets) pour l'identifiant manifeste.
    """
    folder_name = module_name.replace("-", "_")
    source_dir = _APPS_DIR / folder_name

    if not source_dir.exists():
        typer.echo(f"  ⚠ Module '{module_name}' introuvable dans le registre — ignoré.", err=True)
        return

    if all(entry.name == "manifest.json" for entry in source_dir.iterdir()):
        return  # module PyPI : aucune source à copier

    dest_dir = project_root / folder_name
    if dest_dir.exists():
        typer.echo(f"  • '{folder_name}' déjà présent — copie ignorée.")
    else:
        import shutil
        shutil.copytree(source_dir, dest_dir, ignore=shutil.ignore_patterns("manifest.json"))
        typer.echo(f"  • '{folder_name}' copié dans le projet.")

    modified = add_to_installed_apps(settings_path, folder_name)
    if modified:
        typer.echo(f"  • '{folder_name}' ajouté à INSTALLED_APPS.")

    if (dest_dir / "urls.py").is_file():
        wired = wire_url_include(
            project_root,
            url_prefix="",
            include_target=f"{folder_name}.urls",
            namespace=folder_name,
        )
        if wired:
            typer.echo(f"  • {folder_name}.urls branché dans le routeur principal.")


def _check_python_version(min_python: tuple[int, int] | None) -> str | None:
    """Message d'erreur si l'interpréteur courant est trop ancien, sinon `None`.

    Le contrôle se fait avant toute écriture : sans lui, `pip` échouerait en fin
    d'installation, après que `settings.py` ait déjà été modifié.
    """
    if min_python is None or sys.version_info[:2] >= min_python:
        return None
    required = ".".join(map(str, min_python))
    current = ".".join(map(str, sys.version_info[:2]))
    return (
        f"Python {required}+ requis par cette chaîne de modules "
        f"(interpréteur courant : {current})."
    )


def _apply_manifest_config(
    plan,
    project_root: Path,
    settings_path: Path,
    project_name: str,
) -> None:
    """Applique la configuration minimale déclarée par les manifestes.

    Idempotent : chaque écriture est ignorée si l'élément est déjà présent.
    """
    for app_label in plan.installed_apps:
        if add_to_installed_apps(settings_path, app_label):
            typer.echo(f"  • '{app_label}' ajouté à INSTALLED_APPS.")

    for middleware in plan.middleware:
        if add_to_list_setting(settings_path, "MIDDLEWARE", middleware):
            typer.echo(f"  • '{middleware}' ajouté à MIDDLEWARE.")

    if plan.settings_to_apply:
        rendered = _render_placeholders(plan.settings_to_apply, project_name)
        _apply_settings(rendered, settings_path)

    for url in plan.urls_to_wire:
        include_target = url["include"]
        wired = wire_url_include(
            project_root,
            url_prefix=url.get("prefix", ""),
            include_target=include_target,
            namespace=url.get("namespace", include_target.split(".")[0]),
        )
        if wired:
            typer.echo(f"  • {include_target} branché dans le routeur principal.")


def _render_placeholders(value, project_name: str):
    """Remplace `{project_name}` dans toutes les chaînes d'une structure."""
    if isinstance(value, str):
        return value.replace("{project_name}", project_name)
    if isinstance(value, dict):
        return {k: _render_placeholders(v, project_name) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_placeholders(v, project_name) for v in value]
    return value


def _apply_settings(settings_to_apply: dict, settings_path: Path) -> None:
    """Injecte les settings déclarés par les manifestes dans `settings.py`.

    Idempotent : ``add_simple_setting`` n'écrit pas une clé déjà présente.
    Sert notamment à positionner ``AUTH_USER_MODEL`` quand un module fournit
    un modèle utilisateur personnalisé (forge-auth).
    """
    for key, value in settings_to_apply.items():
        if add_simple_setting(settings_path, key, value):
            typer.echo(f"  • {key} = {value!r} ajouté à settings.py.")


def _configure_services(services: list[str], project_root: Path) -> None:
    """Délègue la configuration de chaque service à `configure.run`.

    Transmet explicitement ``project_root`` : sans lui, ``configure.run``
    retomberait sur ``find_manage_py()`` depuis le répertoire courant, ce qui
    échoue dès que ``forge install`` est invoqué hors du dossier du projet
    (ex. via un blueprint exécuté depuis le dossier parent).
    """
    from forge.commands.configure import run as configure_run

    for service in services:
        typer.echo(f"\n→ Configuration de '{service}'...")
        configure_run(service=service, options=ConfigureOptions(), project_root=project_root)


@catch(FileNotFoundError)
def _run_installer(cmd: list[str]) -> int:
    """Lance un installeur et retourne son code de retour.

    `Err(FileNotFoundError)` si l'exécutable n'existe pas (ex : `uv` absent).
    """
    return subprocess.run(cmd).returncode


def _install_python_packages(packages: list[str]) -> bool:
    """
    Installe depuis PyPI les paquets requis par les modules installés, dans
    l'environnement courant (celui où tourne `forge`).

    Les spécifications viennent des manifestes (ex : `django-forge-log>=1.0.0rc3`) :
    une borne de pré-version suffit à `uv` comme à `pip` pour autoriser cette
    pré-version, sans ouvrir les pré-versions des dépendances transitives.

    Essaie `uv pip install` en premier : c'est le gestionnaire documenté pour
    installer django-forge-cli lui-même (`uv add django-forge-cli`), et les
    environnements virtuels créés par uv n'embarquent PAS pip par défaut — un
    simple `python -m pip install` y échoue avec « No module named pip ».
    Retombe sur `pip install` pour les environnements qui en disposent.

    Retourne `True` si l'installation a réussi.
    """
    typer.echo(f"\n→ Installation depuis PyPI : {', '.join(packages)}...")

    attempts = [
        ["uv", "pip", "install", "--python", sys.executable, *packages],
        [sys.executable, "-m", "pip", "install", *packages],
    ]

    for cmd in attempts:
        match _run_installer(cmd):
            case Ok(0):
                return True

    quoted = " ".join(f'"{p}"' for p in packages)
    typer.echo(
        "  ⚠ Échec de l'installation d'un ou plusieurs paquets Python. "
        f"Installez-les manuellement : uv pip install {quoted}",
        err=True,
    )
    return False


def _ensure_env_keys(keys: list[str], project_root: Path) -> None:
    """
    Vérifie que chaque clé existe dans `.env`. Ajoute les manquantes
    avec une valeur vide pour alerter le développeur.
    """
    env_file = project_root / ".env"

    existing = set()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                existing.add(line.split("=", 1)[0].strip())

    missing = [k for k in keys if k not in existing]
    if not missing:
        return

    with env_file.open("a", encoding="utf-8") as f:
        f.write("\n# Ajouté automatiquement par forge install — à compléter\n")
        for key in missing:
            f.write(f"{key}=\n")
        typer.echo(f"  • Clés ajoutées dans .env : {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _detect_project_package(project_root: Path) -> str:
    """
    Détecte le nom du package Django principal en cherchant `settings.py`
    dans les sous-dossiers immédiats de `project_root`.
    """
    for child in project_root.iterdir():
        if child.is_dir() and (child / "settings.py").exists():
            return child.name
    raise FileNotFoundError(
        f"Impossible de détecter le package Django dans {project_root}. "
        "Assurez-vous que settings.py est accessible."
    )