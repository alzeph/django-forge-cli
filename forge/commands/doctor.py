"""
Logique métier de `forge doctor`.

Contrôle la cohérence entre `forge.toml`, `settings.py`, `.env` et
l'environnement Python du projet. Chaque contrôle produit une ligne
`[OK]`, `[ATTENTION]` ou `[ERREUR]` ; le code de sortie vaut 1 s'il y a au
moins une erreur.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer

from forge.core import environment
from forge.core.config_manager import setting_exists
from forge.core.dependency_resolver import Manifest, build_registry
from forge.core.state import STATE_FILENAME, load_state, state_path

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_APPS_DIR = _TEMPLATES_DIR / "apps"

OK = "OK"
WARNING = "ATTENTION"
ERROR = "ERREUR"


@dataclass(frozen=True)
class Check:
    """Résultat d'un contrôle."""

    level: str
    message: str


def run(project_dir: Path | None = None, project_name: str | None = None) -> None:
    """Exécute `forge doctor`."""
    from forge.core.engine import find_manage_py

    try:
        root = project_dir or find_manage_py().parent
    except FileNotFoundError as exc:
        typer.echo(f"Erreur : {exc}", err=True)
        raise typer.Exit(code=1)

    checks = diagnose(root, project_name)
    for check in checks:
        typer.echo(f"[{check.level}] {check.message}")

    errors = sum(1 for c in checks if c.level == ERROR)
    warnings = sum(1 for c in checks if c.level == WARNING)
    typer.echo(f"\n{len(checks)} contrôles : {errors} erreur(s), {warnings} avertissement(s).")
    if errors:
        raise typer.Exit(code=1)


def diagnose(root: Path, project_name: str | None = None) -> list[Check]:
    """Retourne la liste des contrôles pour le projet situé dans `root`."""
    checks: list[Check] = []

    settings_path = _find_settings(root, project_name)
    if settings_path is None:
        return [Check(ERROR, "settings.py introuvable.")]
    checks.append(Check(OK, f"settings.py : {settings_path.relative_to(root)}"))

    python = environment.target_python(root)
    version = ".".join(map(str, environment.current_python_version(root)))
    checks.append(Check(OK, f"Interpréteur du projet : {python} (Python {version})"))

    if not state_path(root).is_file():
        checks.append(Check(WARNING, f"{STATE_FILENAME} absent : aucun module enregistré."))
        return checks

    state = load_state(root)
    registry = build_registry(_APPS_DIR)
    settings_text = settings_path.read_text(encoding="utf-8")
    env_keys = _env_keys(root / ".env")

    for name in sorted(state.modules):
        manifest = registry.get(name)
        if manifest is None:
            checks.append(Check(WARNING, f"Module '{name}' inconnu de cette version de la CLI."))
            continue
        checks += _check_module(manifest, root, settings_path, settings_text, env_keys)

    for service in state.services:
        marker = f"# forge: {service}"
        if marker in settings_text:
            checks.append(Check(OK, f"Service '{service}' présent dans settings.py"))
        else:
            checks.append(Check(ERROR, f"Service '{service}' enregistré mais absent de settings.py"))

    return checks


def _check_module(
    manifest: Manifest,
    root: Path,
    settings_path: Path,
    settings_text: str,
    env_keys: dict[str, str],
) -> list[Check]:
    name = manifest.name
    checks: list[Check] = []

    for spec in manifest.python_packages:
        dist = environment.distribution_name(spec)
        installed = environment.installed_version(dist, root)
        if installed:
            checks.append(Check(OK, f"{name} : {dist} {installed} installé"))
        else:
            checks.append(Check(ERROR, f"{name} : paquet {dist} non installé"))

    for label in manifest.installed_apps:
        if _quoted(label, settings_text):
            checks.append(Check(OK, f"{name} : '{label}' dans INSTALLED_APPS"))
        else:
            checks.append(Check(ERROR, f"{name} : '{label}' absent de INSTALLED_APPS"))

    for path in manifest.middleware:
        if _quoted(path, settings_text):
            checks.append(Check(OK, f"{name} : '{path}' dans MIDDLEWARE"))
        else:
            checks.append(Check(ERROR, f"{name} : '{path}' absent de MIDDLEWARE"))

    for key in manifest.settings:
        if setting_exists(settings_path, key):
            checks.append(Check(OK, f"{name} : {key} défini"))
        else:
            checks.append(Check(ERROR, f"{name} : {key} absent de settings.py"))

    for key in manifest.env_required:
        if env_keys.get(key):
            checks.append(Check(OK, f"{name} : {key} renseigné dans .env"))
        else:
            checks.append(Check(WARNING, f"{name} : {key} vide ou absent dans .env"))

    return checks


def _quoted(value: str, text: str) -> bool:
    return f'"{value}"' in text or f"'{value}'" in text


def _env_keys(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _find_settings(root: Path, project_name: str | None) -> Path | None:
    if project_name:
        candidate = root / project_name / "settings.py"
        return candidate if candidate.is_file() else None
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "settings.py").is_file():
            return child / "settings.py"
    return None
