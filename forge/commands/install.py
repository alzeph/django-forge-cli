"""
Logique métier de `forge install <module_name>`.

Responsabilité
--------------
1. Construire le registre des manifestes disponibles.
2. Résoudre l'arbre de dépendances via `dependency_resolver`.
3. Pour chaque module dans l'ordre topologique :
   a. Copier les sources dans le projet hôte.
   b. Injecter dans `INSTALLED_APPS`.
   c. Déclencher `forge configure` pour les services requis.
4. Vérifier / compléter les clés d'environnement dans `.env`.

Option `--dry-run` : affiche le plan sans aucune écriture disque.
"""

from __future__ import annotations

from pathlib import Path

import typer

from forge.commands._options import ConfigureOptions, InstallOptions
from forge.core.config_manager import add_to_installed_apps
from forge.core.dependency_resolver import build_registry, resolve

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

    if plan.services_to_configure:
        _configure_services(plan.services_to_configure)

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
    if plan.env_keys:
        typer.echo(f"  Clés d'environnement requises : {', '.join(plan.env_keys)}")


def _install_single_module(
    module_name: str,
    project_root: Path,
    settings_path: Path,
) -> None:
    """
    Copie les sources du module et l'injecte dans INSTALLED_APPS.

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


def _configure_services(services: list[str]) -> None:
    """Délègue la configuration de chaque service à `configure.run`."""
    from forge.commands.configure import run as configure_run

    for service in services:
        typer.echo(f"\n→ Configuration de '{service}'...")
        configure_run(service=service, options=ConfigureOptions())


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