"""
Dataclasses des options pour chaque commande CLI.

Convention
----------
Une dataclass par commande, nommée ``<Commande>Options``.
Chaque champ correspond exactement à une option CLI déclarée dans le fichier
de commande associé.

Pour ajouter une option à une commande :
    1. Ajouter le champ ici.
    2. Ajouter le paramètre Typer correspondant dans le fichier de commande.
    3. La logique métier reçoit automatiquement le nouvel objet Options.

Aucune modification de ``main.py`` ou d'un autre fichier n'est nécessaire.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InitOptions:
    """
    Options de ``forge init <project_name>``.

    Attributes
    ----------
    install:
        Liste de modules Forge à installer dès l'initialisation.
        Correspond à ``--install=forge-auth,forge-notification``.
        Exemple : ``["forge-auth", "forge-notification"]``
    """

    install: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AddOptions:
    """
    Options de ``forge add <app_name>``.

    Attributes
    ----------
    no_urls:
        Si ``True``, supprime la création du ``urls.py`` local et le
        branchement automatique dans le routeur principal.
        Correspond à ``--no-urls``.
    templates:
        Liste de fichiers HTML à générer dans l'arborescence templates.
        Une liste vide active l'arborescence sans fichier pré-généré.
        ``None`` signifie que l'option n'a pas été passée (pas de templates).
        Correspond à ``--templates`` ou ``--templates=index.html,detail.html``.
    """

    no_urls: bool = False
    templates: list[str] | None = None


@dataclass(frozen=True)
class InstallOptions:
    """
    Options de ``forge install <module_name>``.

    Attributes
    ----------
    dry_run:
        Si ``True``, affiche le plan d'installation sans rien écrire sur
        le disque. Utile pour auditer les dépendances avant de commiter.
        Correspond à ``--dry-run``.
    """

    dry_run: bool = False


@dataclass(frozen=True)
class ConfigureOptions:
    """
    Options de ``forge configure <service>``.

    Attributes
    ----------
    postgis:
        Active le support géospatial PostGIS. Valide uniquement avec
        le service ``pgsql``. Correspond à ``--postgis``.
    dev:
        Service alternatif pour l'environnement de développement.
        Permet la segmentation DEBUG/production automatique dans settings.py.
        Exemple : ``forge configure pgsql --dev=sqlite``
        Correspond à ``--dev=<service>``.
    """

    postgis: bool = False
    dev: str | None = None