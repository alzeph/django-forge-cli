"""
Couche d'exécution des commandes Django.

Responsabilité unique : localiser ``manage.py`` et exécuter n'importe quelle
commande Django via un sous-processus, en héritant du terminal courant
(stdout / stderr / stdin non capturés) pour conserver la coloration ANSI et
l'interactivité de Django.

Ce module est volontairement fin.  Il ne contient aucune logique métier :
c'est le point de contact entre la CLI Forge et l'écosystème Django natif.

Public API
----------
    run_django_command(args, project_root)
    find_manage_py(start_dir) -> Path
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Localisation de manage.py
# ---------------------------------------------------------------------------


def find_manage_py(start_dir: Path | None = None) -> Path:
    """
    Remonte l'arborescence depuis ``start_dir`` (ou le répertoire courant)
    jusqu'à trouver un ``manage.py``.

    Stratégie : cherche d'abord dans le dossier courant, puis dans chaque
    parent, jusqu'à la racine du système de fichiers.

    Returns
    -------
    Path
        Chemin absolu vers ``manage.py``.

    Raises
    ------
    FileNotFoundError
        Si aucun ``manage.py`` n'est trouvé dans l'arborescence.
    """
    current = (start_dir or Path.cwd()).resolve()

    for directory in (current, *current.parents):
        candidate = directory / "manage.py"
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        f"Impossible de trouver manage.py en remontant depuis : {current}\n"
        "Êtes-vous dans un projet Django ?"
    )


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------


def run_django_command(
    args: list[str],
    project_root: Path | None = None,
) -> int:
    """
    Exécute ``python manage.py <args>`` dans le contexte du projet Django.

    Le processus hérite du terminal courant (``stdout``, ``stderr``, ``stdin``
    non redirigés) : la coloration ANSI de Django, les prompts interactifs et
    les barres de progression restent fonctionnels.

    Parameters
    ----------
    args:
        Liste des arguments passés à ``manage.py``, p. ex.
        ``["makemigrations", "--check"]`` ou ``["runserver", "0.0.0.0:8000"]``.
    project_root:
        Répertoire de travail utilisé pour l'exécution. Si ``None``, le
        dossier contenant ``manage.py`` est utilisé automatiquement.

    Returns
    -------
    int
        Code de retour du processus Django (0 = succès).

    Raises
    ------
    FileNotFoundError
        Si ``manage.py`` n'est pas trouvé.
    subprocess.CalledProcessError
        Jamais levée directement : le code de retour est propagé sans lever
        d'exception, pour laisser l'appelant décider de la stratégie d'erreur.
    """
    manage_py = find_manage_py(project_root)
    cwd = manage_py.parent

    cmd = [sys.executable, str(manage_py), *args]

    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode