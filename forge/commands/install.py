"""
Logique métier de `forge install <module_name>`.

Responsabilité
--------------
1. Construire le registre des manifestes disponibles.
2. Résoudre l'arbre de dépendances via `dependency_resolver`.
3. Pour chaque module dans l'ordre topologique :
   a. Copier les sources dans le projet hôte.
   b. Injecter dans `INSTALLED_APPS`.
   c. Brancher son urls.py dans le routeur principal, s'il en a un.
   d. Déclencher `forge configure` pour les services requis.
4. Installer les paquets Python requis (`python_packages` du manifeste).
5. Vérifier / compléter les clés d'environnement dans `.env`.

Option `--dry-run` : affiche le plan sans aucune écriture disque.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from forge.commands._options import ConfigureOptions, InstallOptions
from forge.core.config_manager import add_simple_setting, add_to_installed_apps
from forge.core.dependency_resolver import build_registry, resolve
from forge.core.urls_manager import wire_url_include

# Modules dont l'installation requiert des réglages supplémentaires que leur
# manifest.json ne peut pas exprimer de façon générique (ex : un modèle User
# personnalisé). Voir _apply_module_specific_settings.
_AUTH_USER_MODEL_MODULES = {"forge-auth": "forge_auth.User"}

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

    plan = resolve(module_name, registry)

    _print_plan(plan, module_name)

    if options.dry_run:
        typer.echo("\n[dry-run] Aucune modification effectuée.")
        return

    settings_path = root / pkg_name / "settings.py"

    for mod in plan.order:
        _install_single_module(mod, root, settings_path)

    if plan.settings_to_apply:
        _apply_settings(plan.settings_to_apply, settings_path)

    if plan.services_to_configure:
        _configure_services(plan.services_to_configure, root)

    if plan.python_packages:
        _install_python_packages(plan.python_packages)

    if plan.env_keys:
        _ensure_env_keys(plan.env_keys, root)

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
        typer.echo(f"  Paquets Python à installer : {', '.join(plan.python_packages)}")
    if plan.env_keys:
        typer.echo(f"  Clés d'environnement requises : {', '.join(plan.env_keys)}")


def _install_single_module(
    module_name: str,
    project_root: Path,
    settings_path: Path,
) -> None:
    """
    Copie les sources du module, l'injecte dans INSTALLED_APPS, branche son
    urls.py s'il en a un, et applique ses réglages spécifiques éventuels.

    Le nom du dossier source suit la convention `forge_auth` (underscores)
    pour le nom Python, `forge-auth` (tirets) pour l'identifiant manifeste.
    """
    folder_name = module_name.replace("-", "_")
    source_dir = _APPS_DIR / folder_name

    if not source_dir.exists():
        typer.echo(f"  ⚠ Sources introuvables pour '{module_name}' — ignoré.", err=True)
        return

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

    _apply_module_specific_settings(module_name, settings_path)


def _apply_module_specific_settings(module_name: str, settings_path: Path) -> None:
    """
    Applique les réglages qu'un module ne peut pas déclarer dans son
    manifest.json (celui-ci ne décrit que dépendances/services/env/paquets).

    Aujourd'hui, seul `forge-auth` en a besoin : son modèle `User` personnalisé
    doit être déclaré via `AUTH_USER_MODEL` pour que Django l'utilise à la
    place de `django.contrib.auth.models.User`.
    """
    auth_user_model = _AUTH_USER_MODEL_MODULES.get(module_name)
    if auth_user_model is None:
        return

    modified = add_simple_setting(settings_path, "AUTH_USER_MODEL", auth_user_model)
    if modified:
        typer.echo(f"  • AUTH_USER_MODEL défini sur '{auth_user_model}'.")


def _apply_settings(settings_to_apply: dict, settings_path: Path) -> None:
    """Injecte les settings déclarés par les manifestes dans `settings.py`.

    Idempotent : ``add_simple_setting`` n'écrit pas une clé déjà présente.
    Sert notamment à positionner ``AUTH_USER_MODEL`` quand un module fournit
    un modèle utilisateur personnalisé (forge-auth).
    """
    from forge.core.config_manager import add_simple_setting

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


def _install_python_packages(packages: list[str]) -> None:
    """
    Installe les paquets Python requis par les modules installés dans
    l'environnement courant (celui où tourne `forge`).

    Essaie `uv pip install` en premier : c'est le gestionnaire documenté pour
    installer django-forge-cli lui-même (`uv add django-forge-cli`), et les
    environnements virtuels créés par uv n'embarquent PAS pip par défaut — un
    simple `python -m pip install` y échoue avec « No module named pip ».
    Retombe sur `pip install` pour les environnements qui en disposent.
    """
    typer.echo(f"\n→ Installation des paquets Python : {', '.join(packages)}...")

    attempts = [
        ["uv", "pip", "install", "--python", sys.executable, *packages],
        [sys.executable, "-m", "pip", "install", *packages],
    ]

    for cmd in attempts:
        try:
            result = subprocess.run(cmd)
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            return

    typer.echo(
        "  ⚠ Échec de l'installation d'un ou plusieurs paquets Python. "
        f"Installez-les manuellement : uv pip install {' '.join(packages)}",
        err=True,
    )


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