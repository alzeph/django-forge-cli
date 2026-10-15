"""
Environnement Python du projet hôte.

La CLI et le projet Django ne partagent pas forcément le même interpréteur :
`forge` peut être installé comme outil global (`uv tool install`) alors que le
projet possède son propre `.venv`. Ce module localise l'interpréteur du projet
et y installe les paquets, de façon à ce que les dépendances soient aussi
enregistrées dans le `pyproject.toml` du projet quand il en a un.

Stratégie d'installation, dans l'ordre :

1. `uv add <paquets>` si le projet possède un `pyproject.toml` avec une
   section `[project]` (met à jour le fichier, le verrou et le `.venv`).
2. `uv pip install --python <interpréteur du projet> <paquets>`.
3. `<interpréteur du projet> -m pip install <paquets>`.

Public API
----------
    project_python(root) -> Path | None
    target_python(root) -> Path
    python_version(python) -> Result[tuple[int, int], Exception]
    current_python_version(root=None) -> tuple[int, int]
    distribution_name(spec) -> str
    is_uv_project(root) -> bool
    install_packages(packages, root=None, upgrade=False) -> bool
    uninstall_packages(names, root=None) -> bool
    installed_version(name, root=None) -> str | None
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import typer
from pycatch import Err, Ok, catch


# ---------------------------------------------------------------------------
# Localisation de l'interpréteur
# ---------------------------------------------------------------------------


def project_python(root: Path) -> Path | None:
    """Interpréteur du `.venv` du projet, ou `None` s'il n'y en a pas.

    Le chemin n'est volontairement pas résolu : suivre le lien symbolique
    mènerait à l'interpréteur de base et ferait perdre le contexte du venv.
    """
    for relative in (".venv/bin/python", ".venv/Scripts/python.exe"):
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def target_python(root: Path | None) -> Path:
    """Interpréteur cible : celui du projet, sinon celui qui exécute la CLI."""
    if root is not None:
        found = project_python(root)
        if found is not None:
            return found
    return Path(sys.executable)


def is_uv_project(root: Path | None) -> bool:
    """`True` si `root/pyproject.toml` déclare une section `[project]`."""
    if root is None:
        return False
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    match _read_toml(pyproject):
        case Ok(data):
            return "project" in data
        case Err(_):
            return False


@catch(OSError, tomllib.TOMLDecodeError)
def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


# ---------------------------------------------------------------------------
# Interrogation de l'interpréteur
# ---------------------------------------------------------------------------


@catch(OSError, ValueError, subprocess.SubprocessError)
def python_version(python: Path) -> tuple[int, int]:
    """Version `(majeur, mineur)` de `python`, obtenue en l'exécutant."""
    output = subprocess.run(
        [str(python), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    major, minor = output.split()
    return int(major), int(minor)


def current_python_version(root: Path | None = None) -> tuple[int, int]:
    """Version de l'interpréteur cible, ou de la CLI si elle est introuvable."""
    python = target_python(root)
    if python == Path(sys.executable):
        return sys.version_info[0], sys.version_info[1]
    match python_version(python):
        case Ok(version):
            return version
        case Err(_):
            return sys.version_info[0], sys.version_info[1]


@catch(OSError, subprocess.SubprocessError)
def _query_distribution(python: Path, name: str) -> str | None:
    code = (
        "import importlib.metadata as m, sys\n"
        "try:\n"
        "    print(m.version(sys.argv[1]))\n"
        "except m.PackageNotFoundError:\n"
        "    pass\n"
    )
    output = subprocess.run(
        [str(python), "-c", code, name], capture_output=True, text=True, check=True
    ).stdout.strip()
    return output or None


def installed_version(name: str, root: Path | None = None) -> str | None:
    """Version installée de la distribution `name` dans l'environnement cible."""
    match _query_distribution(target_python(root), name):
        case Ok(version):
            return version
        case Err(_):
            return None


# ---------------------------------------------------------------------------
# Installation et désinstallation
# ---------------------------------------------------------------------------


@catch(FileNotFoundError)
def run_installer(cmd: list[str], cwd: Path | None = None) -> int:
    """Lance un installeur et retourne son code de retour.

    `Err(FileNotFoundError)` si l'exécutable n'existe pas (ex : `uv` absent).
    """
    return subprocess.run(cmd, cwd=cwd).returncode


def _first_success(attempts: list[tuple[list[str], Path | None]]) -> bool:
    for cmd, cwd in attempts:
        match run_installer(cmd, cwd):
            case Ok(0):
                return True
    return False


def install_packages(
    packages: list[str],
    root: Path | None = None,
    upgrade: bool = False,
) -> bool:
    """Installe des spécifications PyPI dans l'environnement du projet.

    Une borne de pré-version (`rc`) dans une spécification suffit à `uv` comme
    à `pip` pour autoriser cette pré-version, sans ouvrir les pré-versions des
    dépendances transitives.

    Retourne `True` si l'une des tentatives a réussi.
    """
    typer.echo(f"\n> Installation depuis PyPI : {', '.join(packages)}...")

    python = str(target_python(root))
    attempts: list[tuple[list[str], Path | None]] = []

    if is_uv_project(root):
        add = ["uv", "add"]
        if upgrade:
            for spec in packages:
                add += ["--upgrade-package", distribution_name(spec)]
        attempts.append(([*add, *packages], root))

    pip_flags = ["--upgrade"] if upgrade else []
    attempts.append((["uv", "pip", "install", "--python", python, *pip_flags, *packages], None))
    attempts.append(([python, "-m", "pip", "install", *pip_flags, *packages], None))

    if _first_success(attempts):
        return True

    quoted = " ".join(f'"{p}"' for p in packages)
    typer.echo(
        "  ! Échec de l'installation d'un ou plusieurs paquets Python. "
        f"Installez-les manuellement : uv pip install {quoted}",
        err=True,
    )
    return False


def uninstall_packages(names: list[str], root: Path | None = None) -> bool:
    """Désinstalle des distributions de l'environnement du projet."""
    python = str(target_python(root))
    attempts: list[tuple[list[str], Path | None]] = []

    if is_uv_project(root):
        attempts.append((["uv", "remove", *names], root))
    attempts.append((["uv", "pip", "uninstall", "--python", python, *names], None))
    attempts.append(([python, "-m", "pip", "uninstall", "-y", *names], None))

    return _first_success(attempts)


def distribution_name(spec: str) -> str:
    """Nom de distribution d'une spécification (`django-forge-log>=1.0` -> `django-forge-log`)."""
    name = spec
    for separator in ("[", "<", ">", "=", "!", "~", ";", " "):
        name = name.split(separator, 1)[0]
    return name.strip()


__all__ = [
    "current_python_version",
    "distribution_name",
    "install_packages",
    "installed_version",
    "is_uv_project",
    "project_python",
    "python_version",
    "run_installer",
    "target_python",
    "uninstall_packages",
]
