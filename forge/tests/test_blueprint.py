"""
Tests du système de blueprints de projet (forge init --template).

- Chargement / catalogue : lecture réelle des blueprint.json embarqués.
- Orchestration : les commandes sous-jacentes (init/add/install/configure)
  sont mockées ; on vérifie qu'elles sont appelées dans le bon ordre avec
  les bons arguments, sans effet de bord sur le disque.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from forge.commands import blueprint as bp


# ===========================================================================
# Modèle & chargement
# ===========================================================================


class TestBlueprintModel:
    def test_from_dict_full(self) -> None:
        data = {
            "name": "demo",
            "description": "desc",
            "apps": ["core"],
            "install": ["forge-auth"],
            "configure": [
                {"service": "drf"},
                {"service": "pgsql", "dev": "sqlite"},
                {"service": "pgsql", "postgis": True},
            ],
        }
        b = bp.Blueprint.from_dict(data)
        assert b.name == "demo"
        assert b.apps == ["core"]
        assert b.install == ["forge-auth"]
        assert [s.service for s in b.configure] == ["drf", "pgsql", "pgsql"]
        assert b.configure[1].dev == "sqlite"
        assert b.configure[2].postgis is True

    def test_from_dict_minimal(self) -> None:
        b = bp.Blueprint.from_dict({"name": "bare"})
        assert b.name == "bare"
        assert b.apps == []
        assert b.install == []
        assert b.configure == []


class TestListAndLoad:
    def test_shipped_blueprints_present(self) -> None:
        names = {b.name for b in bp.list_blueprints()}
        assert {"api", "saas"} <= names

    def test_shipped_blueprints_are_valid_json(self) -> None:
        # Chaque blueprint embarqué doit être chargeable sans erreur.
        for b in bp.list_blueprints():
            assert b.name
            assert isinstance(b.configure, list)

    def test_load_known_blueprint(self) -> None:
        b = bp.load_blueprint("saas")
        assert b.name == "saas"
        assert "forge-auth" in b.install
        assert any(s.service == "drf" for s in b.configure)

    def test_load_unknown_blueprint_exits(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            bp.load_blueprint("does-not-exist")
        assert exc_info.value.exit_code == 1

    def test_list_returns_empty_when_dir_missing(self, tmp_path: Path) -> None:
        with patch.object(bp, "BLUEPRINTS_DIR", tmp_path / "nope"):
            assert bp.list_blueprints() == []


# ===========================================================================
# Orchestration
# ===========================================================================


class TestBlueprintRun:
    def _fake_blueprint(self, tmp_path: Path) -> Path:
        """Crée un blueprint 'stack' de test dans un BLUEPRINTS_DIR isolé."""
        root = tmp_path / "blueprints"
        (root / "stack").mkdir(parents=True)
        (root / "stack" / "blueprint.json").write_text(
            json.dumps(
                {
                    "name": "stack",
                    "description": "test",
                    "apps": ["core", "billing"],
                    "install": ["forge-auth"],
                    "configure": [
                        {"service": "drf"},
                        {"service": "pgsql", "dev": "sqlite"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_run_orchestrates_all_steps_in_order(self, tmp_path: Path) -> None:
        blueprints_dir = self._fake_blueprint(tmp_path)
        out = tmp_path / "work"
        out.mkdir()

        with patch.object(bp, "BLUEPRINTS_DIR", blueprints_dir), patch(
            "forge.commands.init.run"
        ) as init_run, patch("forge.commands.add.run") as add_run, patch(
            "forge.commands.install.run"
        ) as install_run, patch(
            "forge.commands.configure.run"
        ) as configure_run:
            bp.run(project_name="myproj", blueprint_name="stack", output_dir=out)

        project_root = out / "myproj"

        # init appelé une fois, avec le bon output_dir
        assert init_run.call_count == 1
        assert init_run.call_args.kwargs["project_name"] == "myproj"
        assert init_run.call_args.kwargs["output_dir"] == out

        # apps créées dans l'ordre déclaré
        assert [c.kwargs["app_name"] for c in add_run.call_args_list] == ["core", "billing"]
        for c in add_run.call_args_list:
            assert c.kwargs["project_root"] == project_root

        # module installé
        assert install_run.call_count == 1
        assert install_run.call_args.kwargs["module_name"] == "forge-auth"

        # services configurés dans l'ordre, avec les bonnes options
        services = [c.kwargs["service"] for c in configure_run.call_args_list]
        assert services == ["drf", "pgsql"]
        pgsql_opts = configure_run.call_args_list[1].kwargs["options"]
        assert pgsql_opts.dev == "sqlite"

    def test_run_unknown_blueprint_exits_before_any_action(self, tmp_path: Path) -> None:
        blueprints_dir = self._fake_blueprint(tmp_path)
        with patch.object(bp, "BLUEPRINTS_DIR", blueprints_dir), patch(
            "forge.commands.init.run"
        ) as init_run:
            with pytest.raises(typer.Exit):
                bp.run(project_name="x", blueprint_name="ghost", output_dir=tmp_path)
        init_run.assert_not_called()
