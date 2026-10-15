"""
Résolution récursive des dépendances de modules Forge.

Chaque module déclaré dans `forge/templates/apps/` possède un
`manifest.json` qui décrit comment l'intégrer à un projet. Les modules sont
installés depuis PyPI (`python_packages`) ; le manifeste dit ensuite ce qu'il
faut régler dans le projet pour qu'ils fonctionnent (config minimale) :

- `dependencies`    : liste d'autres modules Forge requis.
- `configure`       : liste de services à configurer (redis, celery, …).
- `env_required`    : clés d'environnement à garantir dans `.env`.
- `python_packages` : spécifications PyPI à installer (ex : `django-forge-auth>=0.2.1`).
- `installed_apps`  : apps à ajouter à `INSTALLED_APPS`.
- `middleware`      : classes à ajouter à `MIDDLEWARE`.
- `settings`        : réglages simples à poser dans `settings.py`
  (`{project_name}` est substitué par le nom du projet).
- `urls`            : `{"prefix": ..., "include": ...}` à brancher dans `urls.py`.
- `requires_python` : version minimale de Python (ex : `"3.13"`).

Cet algorithme implémente un tri topologique **DFS avec détection de cycles**,
ce qui garantit que :

1. Les dépendances sont toujours installées avant le module qui les requiert.
2. Les cycles de dépendances sont détectés proprement (levée d'exception) au
   lieu de provoquer une récursion infinie.
3. Chaque module n'apparaît qu'une seule fois dans le plan final
   (déduplication).

Public API
----------
    resolve(module_name, registry) -> InstallPlan
    resolve_safe(module_name, registry) -> Result[InstallPlan, KeyError | ValueError]
    load_manifest(module_name, apps_dir) -> Manifest
    Manifest          (dataclass)
    InstallPlan       (dataclass)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pycatch import catch


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------


@dataclass
class Manifest:
    """
    Représentation Python d'un `manifest.json` de module Forge.

    Attributes
    ----------
    name:
        Identifiant unique du module (ex : `"forge-auth"`).
    version:
        Version sémantique déclarée (ex : `"1.0.0"`).
    dependencies:
        Modules Forge requis avant celui-ci.
    configure:
        Services à configurer via `forge configure` (ex : `["redis"]`).
    env_required:
        Clés d'environnement à vérifier / insérer dans `.env`.
    python_packages:
        Spécifications PyPI à installer lors de `forge install` (ex :
        `["django-forge-auth>=0.2.1"]`). Une borne de pré-version (`rc`) dans
        la spécification suffit à autoriser cette pré-version.
    installed_apps:
        Apps à ajouter à `INSTALLED_APPS` du projet hôte.
    middleware:
        Classes à ajouter à `MIDDLEWARE` du projet hôte.
    settings:
        Réglages à poser dans `settings.py` s'ils sont absents. Les chaînes
        peuvent contenir `{project_name}`.
    urls:
        Branchement dans le `urls.py` principal :
        `{"prefix": "api/", "include": "forge_auth.urls"}`. `None` si absent.
    requires_python:
        Version minimale de Python, au format `"3.13"`. Vide = pas de contrainte.
    """

    name: str
    version: str = "0.0.0"
    dependencies: list[str] = field(default_factory=list)
    configure: list[str] = field(default_factory=list)
    env_required: list[str] = field(default_factory=list)
    python_packages: list[str] = field(default_factory=list)
    installed_apps: list[str] = field(default_factory=list)
    middleware: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    urls: dict | None = None
    requires_python: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        return cls(
            name=data["name"],
            version=data.get("version", "0.0.0"),
            dependencies=data.get("dependencies", []),
            configure=data.get("configure", []),
            env_required=data.get("env_required", []),
            python_packages=data.get("python_packages", []),
            installed_apps=data.get("installed_apps", []),
            middleware=data.get("middleware", []),
            settings=data.get("settings", {}),
            urls=data.get("urls"),
            requires_python=data.get("requires_python", ""),
        )

    @classmethod
    def from_json(cls, path: Path) -> "Manifest":
        with path.open(encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


@dataclass
class InstallPlan:
    """
    Plan d'installation produit par :func:`resolve`.

    Attributes
    ----------
    order:
        Modules dans l'ordre d'installation (dépendances en premier).
    services_to_configure:
        Union dédupliquée de tous les services requis, dans l'ordre
        d'apparition.
    env_keys:
        Union dédupliquée de toutes les clés d'environnement requises.
    python_packages:
        Union dédupliquée de tous les paquets PyPI requis par la chaîne de
        modules, dans l'ordre d'apparition.
    installed_apps / middleware:
        Unions dédupliquées, dans l'ordre d'installation des modules.
    settings_to_apply:
        Fusion des `settings` de tous les manifestes (le premier module à
        déclarer une clé l'emporte).
    urls_to_wire:
        Branchements `urls.py` de la chaîne de modules, dans l'ordre.
    min_python:
        Version minimale de Python exigée par la chaîne, ex : `(3, 13)`.
        `None` si aucun module n'impose de contrainte.
    """

    order: list[str] = field(default_factory=list)
    services_to_configure: list[str] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    python_packages: list[str] = field(default_factory=list)
    installed_apps: list[str] = field(default_factory=list)
    middleware: list[str] = field(default_factory=list)
    settings_to_apply: dict = field(default_factory=dict)
    urls_to_wire: list[dict] = field(default_factory=list)
    min_python: tuple[int, int] | None = None


def _parse_version(version: str) -> tuple[int, int] | None:
    """`"3.13"` → `(3, 13)` ; chaîne vide → `None`."""
    if not version:
        return None
    major, _, minor = version.partition(".")
    return int(major), int(minor or 0)


# ---------------------------------------------------------------------------
# Chargement de manifeste
# ---------------------------------------------------------------------------


def load_manifest(module_name: str, apps_dir: Path) -> Manifest:
    """
    Charge le manifeste du module `module_name` depuis `apps_dir`.

    Le nom du dossier est le nom du module avec les tirets remplacés par des
    underscores (convention Python) : `"forge-auth"` → `apps_dir/forge_auth/`.

    Parameters
    ----------
    module_name:
        Identifiant du module (p. ex. `"forge-auth"`).
    apps_dir:
        Répertoire contenant les dossiers de modules (`forge/templates/apps/`).

    Raises
    ------
    FileNotFoundError
        Si le dossier ou le fichier `manifest.json` n'existe pas.
    json.JSONDecodeError
        Si le fichier n'est pas un JSON valide.
    """
    folder_name = module_name.replace("-", "_")
    manifest_path = apps_dir / folder_name / "manifest.json"

    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Manifest introuvable pour le module '{module_name}' : {manifest_path}"
        )

    return Manifest.from_json(manifest_path)


# ---------------------------------------------------------------------------
# Algorithme de résolution (DFS + détection de cycles)
# ---------------------------------------------------------------------------


class _Resolver:
    """
    Implémente le tri topologique par DFS (Depth-First Search).

    États d'un nœud :
        `"visiting"`  → le nœud est sur la pile d'appels courante.
        `"visited"`   → le nœud et toutes ses dépendances ont été traités.
        absent          → non encore rencontré.
    """

    def __init__(self, registry: dict[str, Manifest]) -> None:
        self._registry = registry
        self._state: dict[str, str] = {}
        self._order: list[str] = []
        self._services: list[str] = []
        self._env_keys: list[str] = []
        self._python_packages: list[str] = []
        self._installed_apps: list[str] = []
        self._middleware: list[str] = []
        self._settings: dict = {}
        self._urls: list[dict] = []
        self._min_python: tuple[int, int] | None = None

    def _visit(self, name: str, stack: list[str]) -> None:
        state = self._state.get(name)

        if state == "visited":
            return  # déjà traité, rien à faire

        if state == "visiting":
            cycle = " → ".join(stack + [name])
            raise ValueError(f"Cycle de dépendances détecté : {cycle}")

        if name not in self._registry:
            raise KeyError(
                f"Module '{name}' introuvable dans le registre. "
                "Vérifiez qu'un manifest.json existe pour ce module."
            )

        self._state[name] = "visiting"
        manifest = self._registry[name]

        for dep in manifest.dependencies:
            self._visit(dep, stack + [name])

        # Insertion du module après ses dépendances (ordre topologique)
        self._order.append(name)

        # Collecte des services et clés (déduplication par ordre d'apparition)
        for svc in manifest.configure:
            if svc not in self._services:
                self._services.append(svc)

        for key in manifest.env_required:
            if key not in self._env_keys:
                self._env_keys.append(key)

        for pkg in manifest.python_packages:
            if pkg not in self._python_packages:
                self._python_packages.append(pkg)

        for app in manifest.installed_apps:
            if app not in self._installed_apps:
                self._installed_apps.append(app)

        for mw in manifest.middleware:
            if mw not in self._middleware:
                self._middleware.append(mw)

        for key, value in manifest.settings.items():
            self._settings.setdefault(key, value)

        if manifest.urls and manifest.urls not in self._urls:
            self._urls.append(manifest.urls)

        required = _parse_version(manifest.requires_python)
        if required and (self._min_python is None or required > self._min_python):
            self._min_python = required

        self._state[name] = "visited"

    def run(self, root: str) -> InstallPlan:
        self._visit(root, [])
        return InstallPlan(
            order=self._order,
            services_to_configure=self._services,
            env_keys=self._env_keys,
            python_packages=self._python_packages,
            installed_apps=self._installed_apps,
            middleware=self._middleware,
            settings_to_apply=self._settings,
            urls_to_wire=self._urls,
            min_python=self._min_python,
        )


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def resolve(module_name: str, registry: dict[str, Manifest]) -> InstallPlan:
    """
    Calcule le plan d'installation complet pour `module_name`.

    Le `registry` est un dictionnaire `{nom_module: Manifest}` qui doit
    contenir tous les modules susceptibles d'apparaître dans la chaîne de
    dépendances.

    Parameters
    ----------
    module_name:
        Module racine à installer (p. ex. `"forge-notification"`).
    registry:
        Dictionnaire de tous les manifestes disponibles.

    Returns
    -------
    InstallPlan
        Plan ordonné prêt à être exécuté par la commande `forge install`.

    Raises
    ------
    KeyError
        Si `module_name` ou l'une de ses dépendances est absent du registre.
    ValueError
        Si un cycle de dépendances est détecté.

    Example
    -------
    Avec les manifestes suivants :
        forge-test        : aucune dépendance
        forge-auth        : dépend de forge-test
        forge-notification: dépend de forge-auth

    `resolve("forge-notification", registry)` produit :
        InstallPlan(order=["forge-test", "forge-auth", "forge-notification"], ...)
    """
    return _Resolver(registry).run(module_name)


#: Variante de :func:`resolve` qui retourne un `Result` au lieu de lever :
#: `Ok(InstallPlan)`, ou `Err(KeyError | ValueError)` (module inconnu / cycle).
resolve_safe = catch(KeyError, ValueError)(resolve)


def build_registry(apps_dir: Path) -> dict[str, Manifest]:
    """
    Construit le registre complet en parcourant `apps_dir`.

    Pratique pour charger tous les manifestes en une passe avant d'appeler
    :func:`resolve`.

    Parameters
    ----------
    apps_dir:
        Dossier contenant les sous-dossiers de modules Forge
        (`forge/templates/apps/`).

    Returns
    -------
    dict[str, Manifest]
        Clés : nom canonique du module (avec tirets), valeurs : Manifest.
    """
    registry: dict[str, Manifest] = {}

    for entry in apps_dir.iterdir():
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = Manifest.from_json(manifest_path)
        registry[manifest.name] = manifest

    return registry