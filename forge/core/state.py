"""
État du projet : le fichier `forge.toml`.

`forge.toml` est écrit à la racine du projet hôte et garde la trace de ce que
Forge y a installé (modules, services configurés, blueprint). Il permet à
`forge list`, `forge remove`, `forge upgrade` et `forge doctor` de savoir ce
qui est en place sans deviner à partir de `settings.py`.

Format :

    [forge]
    version = "1.0.1"
    blueprint = "saas"

    [services]
    configured = ["drf", "redis"]

    [modules."forge-auth"]
    version = "0.2.1"
    packages = ["django-forge-auth>=0.2.1"]

Public API
----------
    load_state(root) -> State
    save_state(root, state) -> None
    State, ModuleRecord
"""

from __future__ import annotations

import json
import tomllib
from importlib import metadata
from dataclasses import dataclass, field
from pathlib import Path

STATE_FILENAME = "forge.toml"


@dataclass
class ModuleRecord:
    """Module installé : version du manifeste et spécifications PyPI posées."""

    version: str = "0.0.0"
    packages: list[str] = field(default_factory=list)


@dataclass
class State:
    """Contenu de `forge.toml`."""

    forge_version: str = ""
    blueprint: str = ""
    services: list[str] = field(default_factory=list)
    modules: dict[str, ModuleRecord] = field(default_factory=dict)

    def record_module(self, name: str, version: str, packages: list[str]) -> None:
        self.modules[name] = ModuleRecord(version=version, packages=list(packages))

    def record_service(self, service: str) -> None:
        if service not in self.services:
            self.services.append(service)

    def forget_module(self, name: str) -> bool:
        return self.modules.pop(name, None) is not None


def cli_version() -> str:
    """Version installée de `django-forge-cli`, `0.0.0` si inconnue."""
    try:
        return metadata.version("django-forge-cli")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def state_path(root: Path) -> Path:
    return root / STATE_FILENAME


def load_state(root: Path) -> State:
    """Lit `forge.toml`. Un fichier absent ou illisible donne un état vide."""
    path = state_path(root)
    if not path.is_file():
        return State()

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return State()

    section = data.get("forge", {})
    modules = {
        name: ModuleRecord(
            version=str(values.get("version", "0.0.0")),
            packages=[str(p) for p in values.get("packages", [])],
        )
        for name, values in data.get("modules", {}).items()
    }
    return State(
        forge_version=str(section.get("version", "")),
        blueprint=str(section.get("blueprint", "")),
        services=[str(s) for s in data.get("services", {}).get("configured", [])],
        modules=modules,
    )


def save_state(root: Path, state: State) -> None:
    """Écrit `forge.toml` (sérialisation TOML minimale : chaînes et listes)."""
    lines = ["[forge]", f"version = {_string(state.forge_version)}"]
    if state.blueprint:
        lines.append(f"blueprint = {_string(state.blueprint)}")

    lines += ["", "[services]", f"configured = {_array(state.services)}"]

    for name in sorted(state.modules):
        record = state.modules[name]
        lines += [
            "",
            f"[modules.{_string(name)}]",
            f"version = {_string(record.version)}",
            f"packages = {_array(record.packages)}",
        ]

    root.mkdir(parents=True, exist_ok=True)
    state_path(root).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _string(value: str) -> str:
    """Chaîne TOML entre guillemets (l'échappement JSON est valide en TOML)."""
    return json.dumps(value, ensure_ascii=False)


def _array(values: list[str]) -> str:
    return "[" + ", ".join(_string(v) for v in values) + "]"
