"""
Résolution récursive des dépendances de modules Forge.

Chaque module embarqué dans `forge/templates/apps/` possède un
`manifest.json` qui déclare :

- `dependencies` : liste d'autres modules Forge requis.
- `configure`    : liste de services à configurer (redis, celery, …).
- `env_required` : clés d'environnement à garantir dans `.env`.

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
    load_manifest(module_name, apps_dir) -> Manifest
    Manifest          (dataclass)
    InstallPlan       (dataclass)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


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
    settings:
        Paires clé/valeur à injecter dans `settings.py` lors de l'installation
        (ex : ``{"AUTH_USER_MODEL": "forge_auth.User"}``).
    """

    name: str
    version: str = "0.0.0"
    dependencies: list[str] = field(default_factory=list)
    configure: list[str] = field(default_factory=list)
    env_required: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        return cls(
            name=data["name"],
            version=data.get("version", "0.0.0"),
            dependencies=data.get("dependencies", []),
            configure=data.get("configure", []),
            env_required=data.get("env_required", []),
            settings=data.get("settings", {}),
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
    settings_to_apply:
        Fusion des paires clé/valeur à injecter dans `settings.py`
        (premier module déclarant une clé l'emporte).
    """

    order: list[str] = field(default_factory=list)
    services_to_configure: list[str] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    settings_to_apply: dict = field(default_factory=dict)


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
        self._settings: dict = {}

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

        # Collecte des settings (premier déclarant l'emporte)
        for setting_key, setting_value in manifest.settings.items():
            self._settings.setdefault(setting_key, setting_value)

        self._state[name] = "visited"

    def run(self, root: str) -> InstallPlan:
        self._visit(root, [])
        return InstallPlan(
            order=self._order,
            services_to_configure=self._services,
            env_keys=self._env_keys,
            settings_to_apply=self._settings,
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