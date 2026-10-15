"""
Tests de la gestion du projet : environnement Python, forge.toml et commandes
`remove`, `upgrade`, `list` et `doctor`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from forge.commands._options import InstallOptions, ListOptions, RemoveOptions
from forge.core import environment
from forge.core.dependency_resolver import Manifest
from forge.core.state import (
    STATE_FILENAME,
    ModuleRecord,
    State,
    cli_version,
    load_state,
    save_state,
)

SETTINGS = 'INSTALLED_APPS = [\n    "django.contrib.admin",\n]\n\nMIDDLEWARE = [\n    "a.Mw",\n]\n\nROOT_URLCONF = "p.urls"\n'
URLS = "from django.urls import path\n\nurlpatterns = [\n]\n"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "manage.py").write_text("")
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (pkg / "urls.py").write_text(URLS, encoding="utf-8")
    return tmp_path


def _completed(code: int = 0, stdout: str = "") -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout)


# ===========================================================================
# environment : interpreteur du projet
# ===========================================================================


class TestProjectPython:
    def test_none_without_venv(self, tmp_path: Path) -> None:
        assert environment.project_python(tmp_path) is None

    def test_posix_venv(self, tmp_path: Path) -> None:
        python = tmp_path / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        assert environment.project_python(tmp_path) == python

    def test_windows_venv(self, tmp_path: Path) -> None:
        python = tmp_path / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_text("")
        assert environment.project_python(tmp_path) == python

    def test_path_is_not_resolved(self, tmp_path: Path) -> None:
        real = tmp_path / "base_python"
        real.write_text("")
        link = tmp_path / ".venv" / "bin" / "python"
        link.parent.mkdir(parents=True)
        link.symlink_to(real)
        assert environment.project_python(tmp_path) == link

    def test_target_python_prefers_project(self, tmp_path: Path) -> None:
        python = tmp_path / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        assert environment.target_python(tmp_path) == python

    def test_target_python_falls_back_to_current(self, tmp_path: Path) -> None:
        assert environment.target_python(tmp_path) == Path(sys.executable)
        assert environment.target_python(None) == Path(sys.executable)


class TestUvProject:
    def test_false_without_root_or_file(self, tmp_path: Path) -> None:
        assert environment.is_uv_project(None) is False
        assert environment.is_uv_project(tmp_path) is False

    def test_true_with_project_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert environment.is_uv_project(tmp_path) is True

    def test_false_without_project_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
        assert environment.is_uv_project(tmp_path) is False

    def test_false_with_invalid_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project\n")
        assert environment.is_uv_project(tmp_path) is False


class TestPythonVersion:
    def test_parses_output(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="3 13\n")):
            assert environment.python_version(Path("py")).value == (3, 13)

    def test_error_on_missing_executable(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert environment.python_version(Path("py")).is_err()

    def test_error_on_failure(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "py")):
            assert environment.python_version(Path("py")).is_err()

    def test_error_on_garbage_output(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="nope")):
            assert environment.python_version(Path("py")).is_err()

    def test_current_version_of_cli_interpreter_needs_no_subprocess(self) -> None:
        assert environment.current_python_version(None) == sys.version_info[:2]

    def test_current_version_of_project_venv(self, tmp_path: Path) -> None:
        python = tmp_path / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        with patch("subprocess.run", return_value=_completed(stdout="3 99")):
            assert environment.current_python_version(tmp_path) == (3, 99)

    def test_current_version_falls_back_when_venv_unreadable(self, tmp_path: Path) -> None:
        python = tmp_path / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert environment.current_python_version(tmp_path) == sys.version_info[:2]


class TestInstalledVersion:
    def test_returns_version(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="1.2.3\n")):
            assert environment.installed_version("pkg") == "1.2.3"

    def test_none_when_not_installed(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="")):
            assert environment.installed_version("pkg") is None

    def test_none_when_interpreter_fails(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert environment.installed_version("pkg") is None


class TestDistributionName:
    @pytest.mark.parametrize(
        ("spec", "name"),
        [
            ("django-forge-log>=1.0.0rc3", "django-forge-log"),
            ("psycopg[binary]>=3.2", "psycopg"),
            ("pkg==1.0", "pkg"),
            ("pkg~=1.0", "pkg"),
            ("pkg!=1.0", "pkg"),
            ("pkg<2", "pkg"),
            ("pkg ; python_version>'3'", "pkg"),
            ("pkg", "pkg"),
        ],
    )
    def test_extracts_name(self, spec: str, name: str) -> None:
        assert environment.distribution_name(spec) == name


class TestInstallPackages:
    def _commands(self, run: MagicMock) -> list[list[str]]:
        return [c.args[0] for c in run.call_args_list]

    def test_plain_environment_uses_uv_pip_with_target_python(self, tmp_path: Path) -> None:
        with patch("subprocess.run", return_value=_completed(0)) as run:
            assert environment.install_packages(["a>=1"], tmp_path) is True
        cmd = self._commands(run)[0]
        assert cmd[:3] == ["uv", "pip", "install"]
        assert cmd[cmd.index("--python") + 1] == sys.executable

    def test_uv_project_uses_uv_add_in_project_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        with patch("subprocess.run", return_value=_completed(0)) as run:
            environment.install_packages(["a>=1", "b>=2"], tmp_path)
        assert self._commands(run) == [["uv", "add", "a>=1", "b>=2"]]
        assert run.call_args.kwargs["cwd"] == tmp_path

    def test_uv_add_failure_falls_back_to_uv_pip(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        results = [_completed(1), _completed(0)]
        with patch("subprocess.run", side_effect=results) as run:
            assert environment.install_packages(["a>=1"], tmp_path) is True
        assert self._commands(run)[1][:3] == ["uv", "pip", "install"]

    def test_uv_missing_falls_back_to_pip_module(self, tmp_path: Path) -> None:
        results = [FileNotFoundError("uv"), _completed(0)]
        with patch("subprocess.run", side_effect=results) as run:
            assert environment.install_packages(["a>=1"], tmp_path) is True
        assert self._commands(run)[1][1:4] == ["-m", "pip", "install"]

    def test_upgrade_flags_for_uv_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        with patch("subprocess.run", return_value=_completed(0)) as run:
            environment.install_packages(["django-forge-log>=1.0.0rc3"], tmp_path, upgrade=True)
        assert self._commands(run)[0] == [
            "uv", "add", "--upgrade-package", "django-forge-log", "django-forge-log>=1.0.0rc3",
        ]

    def test_upgrade_flags_for_plain_environment(self, tmp_path: Path) -> None:
        with patch("subprocess.run", return_value=_completed(0)) as run:
            environment.install_packages(["a>=1"], tmp_path, upgrade=True)
        assert "--upgrade" in self._commands(run)[0]

    def test_returns_false_and_hints_when_everything_fails(self, tmp_path: Path, capsys) -> None:
        with patch("subprocess.run", return_value=_completed(1)):
            assert environment.install_packages(["a>=1"], tmp_path) is False
        assert 'uv pip install "a>=1"' in capsys.readouterr().err

    def test_without_root_behaves_like_plain_environment(self) -> None:
        with patch("subprocess.run", return_value=_completed(0)) as run:
            environment.install_packages(["a>=1"])
        assert self._commands(run)[0][:3] == ["uv", "pip", "install"]


class TestUninstallPackages:
    def test_uv_project_uses_uv_remove(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        with patch("subprocess.run", return_value=_completed(0)) as run:
            assert environment.uninstall_packages(["a", "b"], tmp_path) is True
        assert run.call_args.args[0] == ["uv", "remove", "a", "b"]

    def test_plain_environment(self, tmp_path: Path) -> None:
        with patch("subprocess.run", return_value=_completed(0)) as run:
            environment.uninstall_packages(["a"], tmp_path)
        assert run.call_args.args[0][:3] == ["uv", "pip", "uninstall"]

    def test_failure(self, tmp_path: Path) -> None:
        with patch("subprocess.run", return_value=_completed(1)):
            assert environment.uninstall_packages(["a"], tmp_path) is False


class TestEngineUsesProjectPython:
    def test_manage_py_runs_with_project_venv(self, project: Path) -> None:
        from forge.core.engine import run_django_command

        python = project / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        with patch("subprocess.run", return_value=_completed(0)) as run:
            run_django_command(["check"], project_root=project)
        assert run.call_args.args[0][0] == str(python)


# ===========================================================================
# state : forge.toml
# ===========================================================================


class TestState:
    def test_missing_file_gives_empty_state(self, tmp_path: Path) -> None:
        state = load_state(tmp_path)
        assert state == State()

    def test_roundtrip(self, tmp_path: Path) -> None:
        state = State(forge_version="1.0.1", blueprint="saas", services=["drf", "redis"])
        state.record_module("forge-auth", "0.2.1", ["django-forge-auth>=0.2.1"])
        state.record_module("event-bus", "1.0.0", ["django-event-bus>=1.0.0"])
        save_state(tmp_path, state)
        assert load_state(tmp_path) == state

    def test_file_is_valid_toml_with_expected_sections(self, tmp_path: Path) -> None:
        import tomllib

        state = State(forge_version="1.0.1")
        state.record_module("forge-log", "1.0.0rc3", ["django-forge-log>=1.0.0rc3"])
        save_state(tmp_path, state)
        data = tomllib.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
        assert data["forge"]["version"] == "1.0.1"
        assert data["modules"]["forge-log"]["packages"] == ["django-forge-log>=1.0.0rc3"]
        assert data["services"]["configured"] == []

    def test_blueprint_omitted_when_empty(self, tmp_path: Path) -> None:
        save_state(tmp_path, State(forge_version="1"))
        assert "blueprint" not in (tmp_path / STATE_FILENAME).read_text()

    def test_modules_are_written_sorted(self, tmp_path: Path) -> None:
        state = State()
        state.record_module("z-mod", "1", [])
        state.record_module("a-mod", "1", [])
        save_state(tmp_path, state)
        text = (tmp_path / STATE_FILENAME).read_text()
        assert text.index("a-mod") < text.index("z-mod")

    def test_special_characters_are_escaped(self, tmp_path: Path) -> None:
        state = State(blueprint='we"ird\\name')
        save_state(tmp_path, state)
        assert load_state(tmp_path).blueprint == 'we"ird\\name'

    def test_invalid_file_gives_empty_state(self, tmp_path: Path) -> None:
        (tmp_path / STATE_FILENAME).write_text("[forge\n")
        assert load_state(tmp_path) == State()

    def test_record_service_is_idempotent(self) -> None:
        state = State()
        state.record_service("drf")
        state.record_service("drf")
        assert state.services == ["drf"]

    def test_record_module_copies_packages(self) -> None:
        packages = ["a"]
        state = State()
        state.record_module("m", "1", packages)
        packages.append("b")
        assert state.modules["m"].packages == ["a"]

    def test_forget_module(self) -> None:
        state = State(modules={"m": ModuleRecord()})
        assert state.forget_module("m") is True
        assert state.forget_module("m") is False

    def test_save_creates_missing_directory(self, tmp_path: Path) -> None:
        save_state(tmp_path / "new" / "dir", State())
        assert (tmp_path / "new" / "dir" / STATE_FILENAME).is_file()

    def test_cli_version_is_a_string(self) -> None:
        assert isinstance(cli_version(), str) and cli_version()

    def test_cli_version_unknown(self) -> None:
        from importlib import metadata

        with patch("forge.core.state.metadata.version", side_effect=metadata.PackageNotFoundError):
            assert cli_version() == "0.0.0"


# ===========================================================================
# config_manager / urls_manager : retraits
# ===========================================================================


class TestRemovals:
    def test_remove_from_list_setting(self, tmp_path: Path) -> None:
        from forge.core.config_manager import add_to_installed_apps, remove_from_list_setting

        s = tmp_path / "s.py"
        s.write_text('INSTALLED_APPS = [\n    "a",\n]\n')
        add_to_installed_apps(s, "b")
        assert remove_from_list_setting(s, "INSTALLED_APPS", "b") is True
        assert remove_from_list_setting(s, "INSTALLED_APPS", "b") is False
        assert '"b"' not in s.read_text() and '"a"' in s.read_text()
        compile(s.read_text(), "s.py", "exec")

    def test_remove_middle_element(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        s = tmp_path / "s.py"
        s.write_text('L = [\n    "a",\n    "b",\n    "c",\n]\n')
        remove_from_list_setting(s, "L", "b")
        ns: dict = {}
        exec(s.read_text(), ns)
        assert ns["L"] == ["a", "c"]

    def test_remove_last_element_keeps_valid_python(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        s = tmp_path / "s.py"
        s.write_text('L = [\n    "a",\n    "b",\n]\n')
        remove_from_list_setting(s, "L", "b")
        ns: dict = {}
        exec(s.read_text(), ns)
        assert ns["L"] == ["a"]

    def test_remove_only_element_gives_empty_list(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        s = tmp_path / "s.py"
        s.write_text('L = [\n    "a",\n]\nOTHER = 1\n')
        remove_from_list_setting(s, "L", "a")
        assert s.read_text() == "L = []\nOTHER = 1\n"

    def test_remove_from_other_list_untouched(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        s = tmp_path / "s.py"
        s.write_text('A = ["x"]\nB = ["x"]\n')
        remove_from_list_setting(s, "A", "x")
        assert s.read_text() == 'A = []\nB = ["x"]\n'

    def test_remove_from_augmented_assignment(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        s = tmp_path / "s.py"
        s.write_text('A = ["x"]\nA += ["y", "z"]\n')
        assert remove_from_list_setting(s, "A", "y") is True
        assert '"y"' not in s.read_text()

    def test_remove_setting(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_setting

        s = tmp_path / "s.py"
        s.write_text("A = 1\nKEY = {'x': 1}\nB = 2\n")
        assert remove_setting(s, "KEY") is True
        assert remove_setting(s, "KEY") is False
        assert s.read_text() == "A = 1\nB = 2\n"

    def test_remove_setting_ignores_nested_assignments(self, tmp_path: Path) -> None:
        from forge.core.config_manager import remove_setting

        s = tmp_path / "s.py"
        s.write_text("if True:\n    KEY = 1\n")
        assert remove_setting(s, "KEY") is False

    def test_unwire_url_include(self, project: Path) -> None:
        from forge.core.urls_manager import unwire_url_include, wire_url_include

        wire_url_include(project, "api/", "x.urls", "x")
        wire_url_include(project, "y/", "y.urls", "y")
        assert unwire_url_include(project, "x.urls") is True
        text = (project / "p" / "urls.py").read_text()
        assert "x.urls" not in text and "y.urls" in text
        assert unwire_url_include(project, "x.urls") is False

    def test_unwire_without_main_urls(self, tmp_path: Path) -> None:
        from forge.core.urls_manager import unwire_url_include

        assert unwire_url_include(tmp_path, "x.urls") is False


# ===========================================================================
# install : enregistrement dans forge.toml
# ===========================================================================


class TestInstallRecordsState:
    def test_install_writes_forge_toml(self, project: Path) -> None:
        from forge.commands.install import run

        with patch("forge.commands.install._install_python_packages"):
            run("forge-log", InstallOptions(), project_dir=project, project_name="p")
        state = load_state(project)
        assert state.modules["forge-log"].version == "1.0.0rc3"
        assert state.modules["forge-log"].packages == ["django-forge-log>=1.0.0rc3"]

    def test_install_records_dependencies_too(self, project: Path) -> None:
        from forge.commands.install import run

        with (
            patch("forge.commands.install._install_python_packages"),
            patch("forge.commands.install._configure_services"),
        ):
            run("forge-auth", InstallOptions(), project_dir=project, project_name="p")
        assert set(load_state(project).modules) == {"forge-auth", "forge-test"}

    def test_dry_run_writes_nothing(self, project: Path) -> None:
        from forge.commands.install import run

        run("forge-log", InstallOptions(dry_run=True), project_dir=project, project_name="p")
        assert not (project / STATE_FILENAME).exists()

    def test_install_passes_project_root_to_installer(self, project: Path) -> None:
        from forge.commands.install import run

        with patch("forge.commands.install._install_python_packages") as pip:
            run("forge-log", InstallOptions(), project_dir=project, project_name="p")
        assert pip.call_args.args[1] == project

    def test_python_check_uses_project_interpreter(self, project: Path) -> None:
        from forge.commands.install import run

        with (
            patch("forge.commands.install.environment.current_python_version", return_value=(3, 10)),
            patch("forge.commands.install._install_python_packages") as pip,
            pytest.raises(typer.Exit),
        ):
            run("goroutine", InstallOptions(), project_dir=project, project_name="p")
        pip.assert_not_called()

    def test_check_python_version_with_explicit_current(self) -> None:
        from forge.commands.install import _check_python_version

        assert _check_python_version((3, 13), (3, 13)) is None
        assert "3.12" in _check_python_version((3, 13), (3, 12))


class TestConfigureRecordsState:
    def test_service_and_packages(self, project: Path) -> None:
        from forge.commands.configure import SERVICE_PACKAGES, run
        from forge.commands._options import ConfigureOptions

        with patch("forge.core.environment.install_packages") as install:
            run("drf", ConfigureOptions(), project_root=project)
        assert load_state(project).services == ["drf"]
        install.assert_called_once_with(SERVICE_PACKAGES["drf"], project)

    def test_every_service_declares_packages(self) -> None:
        from forge.commands.configure import AVAILABLE_SERVICES, SERVICE_PACKAGES

        assert set(SERVICE_PACKAGES) == set(AVAILABLE_SERVICES)
        assert all(SERVICE_PACKAGES[s] for s in AVAILABLE_SERVICES)

    def test_service_recorded_once(self, project: Path) -> None:
        from forge.commands.configure import run
        from forge.commands._options import ConfigureOptions

        with patch("forge.core.environment.install_packages"):
            run("redis", ConfigureOptions(), project_root=project)
            run("redis", ConfigureOptions(), project_root=project)
        assert load_state(project).services == ["redis"]


class TestInitCreatesProjectFiles:
    def test_pyproject_content(self, tmp_path: Path) -> None:
        import tomllib

        from forge.commands.init import _create_pyproject

        _create_pyproject(tmp_path, "my_shop")
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        assert data["project"]["name"] == "my-shop"
        assert data["project"]["requires-python"] == f">={sys.version_info[0]}.{sys.version_info[1]}"
        assert "python-dotenv>=1.0" in data["project"]["dependencies"]
        assert any(d.startswith("django>=") for d in data["project"]["dependencies"])

    def test_pyproject_not_overwritten(self, tmp_path: Path) -> None:
        from forge.commands.init import _create_pyproject

        (tmp_path / "pyproject.toml").write_text("mine")
        _create_pyproject(tmp_path, "shop")
        assert (tmp_path / "pyproject.toml").read_text() == "mine"

    def test_overlay_writes_forge_toml_and_pyproject(self, tmp_path: Path) -> None:
        from forge.commands.init import _apply_forge_settings_overlay

        (tmp_path / "shop").mkdir()
        _apply_forge_settings_overlay(tmp_path, "shop")
        assert (tmp_path / "pyproject.toml").is_file()
        assert load_state(tmp_path).forge_version == cli_version()

    def test_generated_pyproject_is_a_uv_project(self, tmp_path: Path) -> None:
        from forge.commands.init import _create_pyproject

        _create_pyproject(tmp_path, "shop")
        assert environment.is_uv_project(tmp_path) is True

    def test_blueprint_name_is_recorded(self, tmp_path: Path) -> None:
        from forge.commands import blueprint as bp

        with (
            patch("forge.commands.init.run"),
            patch("forge.commands.add.run"),
            patch("forge.commands.install.run"),
            patch("forge.commands.configure.run"),
        ):
            bp.run("proj", "saas", output_dir=tmp_path)
        assert load_state(tmp_path / "proj").blueprint == "saas"


# ===========================================================================
# forge remove
# ===========================================================================


def _install(project: Path, module: str) -> None:
    from forge.commands.install import run

    with (
        patch("forge.commands.install._install_python_packages"),
        patch("forge.commands.install._configure_services"),
    ):
        run(module, InstallOptions(), project_dir=project, project_name="p")


class TestRemove:
    def _remove(self, project: Path, module: str, **kwargs) -> None:
        from forge.commands.remove import run

        with patch("forge.core.environment.uninstall_packages", return_value=True) as uninstall:
            run(module, RemoveOptions(yes=True, **kwargs), project_dir=project, project_name="p")
        self.uninstall = uninstall

    def test_removes_apps_middleware_and_state(self, project: Path) -> None:
        _install(project, "forge-log")
        self._remove(project, "forge-log")
        text = (project / "p" / "settings.py").read_text()
        assert "forge_log" not in text and "RequestContextMiddleware" not in text
        assert "a.Mw" in text
        assert "forge-log" not in load_state(project).modules
        self.uninstall.assert_called_once_with(["django-forge-log"], project)

    def test_removes_settings_and_urls(self, project: Path) -> None:
        _install(project, "forge-auth")
        self._remove(project, "forge-auth")
        text = (project / "p" / "settings.py").read_text()
        assert "AUTH_USER_MODEL" not in text and "FORGE_AUTH" not in text
        assert "forge_auth" not in text
        assert "forge_auth.urls" not in (project / "p" / "urls.py").read_text()
        assert "forge-test" in load_state(project).modules

    def test_dependency_is_kept_but_blocked_while_needed(self, project: Path) -> None:
        from forge.commands.remove import run

        _install(project, "forge-auth")
        with pytest.raises(typer.Exit) as exc:
            run("forge-test", RemoveOptions(yes=True), project_dir=project, project_name="p")
        assert exc.value.exit_code == 1
        assert "forge-test" in load_state(project).modules

    def test_dependency_removable_after_dependent(self, project: Path) -> None:
        _install(project, "forge-auth")
        self._remove(project, "forge-auth")
        self._remove(project, "forge-test")
        assert load_state(project).modules == {}

    def test_not_installed(self, project: Path) -> None:
        from forge.commands.remove import run

        with pytest.raises(typer.Exit) as exc:
            run("forge-log", RemoveOptions(yes=True), project_dir=project, project_name="p")
        assert exc.value.exit_code == 1

    def test_unknown_module(self, project: Path, capsys) -> None:
        from forge.commands.remove import run

        with pytest.raises(typer.Exit):
            run("ghost", RemoveOptions(yes=True), project_dir=project, project_name="p")
        assert "forge-log" in capsys.readouterr().err

    def test_dry_run_changes_nothing(self, project: Path) -> None:
        _install(project, "forge-log")
        before = (project / "p" / "settings.py").read_text()
        self._remove(project, "forge-log", dry_run=True)
        assert (project / "p" / "settings.py").read_text() == before
        assert "forge-log" in load_state(project).modules
        self.uninstall.assert_not_called()

    def test_confirmation_declined(self, project: Path) -> None:
        from forge.commands.remove import run

        _install(project, "forge-log")
        with patch("typer.confirm", return_value=False), pytest.raises(typer.Exit):
            run("forge-log", RemoveOptions(), project_dir=project, project_name="p")
        assert "forge-log" in load_state(project).modules

    def test_confirmation_accepted(self, project: Path) -> None:
        from forge.commands.remove import run

        _install(project, "forge-log")
        with (
            patch("typer.confirm", return_value=True),
            patch("forge.core.environment.uninstall_packages", return_value=True),
        ):
            run("forge-log", RemoveOptions(), project_dir=project, project_name="p")
        assert "forge-log" not in load_state(project).modules

    def test_warns_about_auth_user_model(self, project: Path, capsys) -> None:
        _install(project, "forge-auth")
        self._remove(project, "forge-auth", dry_run=True)
        assert "AUTH_USER_MODEL" in capsys.readouterr().out

    def test_uninstall_failure_is_reported_not_fatal(self, project: Path, capsys) -> None:
        from forge.commands.remove import run

        _install(project, "forge-log")
        with patch("forge.core.environment.uninstall_packages", return_value=False):
            run("forge-log", RemoveOptions(yes=True), project_dir=project, project_name="p")
        assert "django-forge-log" in capsys.readouterr().err
        assert "forge-log" not in load_state(project).modules

    def test_installed_dependents_helper(self) -> None:
        from forge.commands.remove import _installed_dependents

        registry = {
            "a": Manifest(name="a"),
            "b": Manifest(name="b", dependencies=["a"]),
            "c": Manifest(name="c"),
        }
        installed = {"a": ModuleRecord(), "b": ModuleRecord(), "c": ModuleRecord()}
        assert _installed_dependents("a", installed, registry) == ["b"]
        assert _installed_dependents("c", installed, registry) == []


# ===========================================================================
# forge upgrade
# ===========================================================================


class TestUpgrade:
    def test_upgrades_all_installed_modules(self, project: Path) -> None:
        from forge.commands.upgrade import run

        _install(project, "forge-log")
        with (
            patch("forge.core.environment.install_packages") as install,
            patch("forge.core.environment.installed_version", return_value="9.9"),
        ):
            run(project_dir=project, project_name="p")
        install.assert_called_once_with(["django-forge-log>=1.0.0rc3"], project, upgrade=True)

    def test_single_module(self, project: Path) -> None:
        from forge.commands.upgrade import run

        _install(project, "forge-auth")
        with (
            patch("forge.core.environment.install_packages") as install,
            patch("forge.core.environment.installed_version", return_value="1"),
        ):
            run("forge-test", project_dir=project, project_name="p")
        assert install.call_args.args[0] == ["django-forge-test>=0.8.0"]

    def test_not_installed(self, project: Path) -> None:
        from forge.commands.upgrade import run

        with pytest.raises(typer.Exit):
            run("forge-log", project_dir=project, project_name="p")

    def test_nothing_installed(self, project: Path, capsys) -> None:
        from forge.commands.upgrade import run

        run(project_dir=project, project_name="p")
        assert "Aucun module" in capsys.readouterr().out

    def test_reapplies_missing_configuration(self, project: Path) -> None:
        from forge.commands.upgrade import run
        from forge.core.config_manager import remove_from_list_setting

        _install(project, "forge-log")
        settings = project / "p" / "settings.py"
        remove_from_list_setting(settings, "INSTALLED_APPS", "forge_log")
        with (
            patch("forge.core.environment.install_packages"),
            patch("forge.core.environment.installed_version", return_value="1"),
        ):
            run(project_dir=project, project_name="p")
        assert '"forge_log"' in settings.read_text()

    def test_refreshes_recorded_packages(self, project: Path) -> None:
        from forge.commands.upgrade import run

        state = State()
        state.record_module("forge-log", "0.0.1", ["django-forge-log>=0.0.1"])
        save_state(project, state)
        with (
            patch("forge.core.environment.install_packages"),
            patch("forge.core.environment.installed_version", return_value="1"),
        ):
            run(project_dir=project, project_name="p")
        record = load_state(project).modules["forge-log"]
        assert record.version == "1.0.0rc3"
        assert record.packages == ["django-forge-log>=1.0.0rc3"]

    def test_unknown_module_in_state_is_skipped(self, project: Path, capsys) -> None:
        from forge.commands.upgrade import run

        state = State()
        state.record_module("ghost", "1", ["ghost>=1"])
        save_state(project, state)
        with patch("forge.core.environment.install_packages") as install:
            run(project_dir=project, project_name="p")
        install.assert_not_called()
        assert "ghost" in capsys.readouterr().err

    def test_prints_installed_versions(self, project: Path, capsys) -> None:
        from forge.commands.upgrade import run

        _install(project, "forge-log")
        with (
            patch("forge.core.environment.install_packages"),
            patch("forge.core.environment.installed_version", return_value="1.0.0rc4"),
        ):
            run(project_dir=project, project_name="p")
        assert "django-forge-log 1.0.0rc4" in capsys.readouterr().out


# ===========================================================================
# forge list
# ===========================================================================


class TestList:
    def test_lists_every_module_as_available(self, tmp_path: Path, capsys) -> None:
        from forge.commands.modules import run

        run(ListOptions(), project_dir=tmp_path)
        out = capsys.readouterr().out
        for name in (
            "forge-auth", "forge-test", "forge-core", "forge-log", "event-bus", "goroutine", "pycatch"
        ):
            assert name in out
        assert "installé" not in out.replace("disponible", "")

    def test_marks_installed_modules(self, project: Path, capsys) -> None:
        from forge.commands.modules import run

        _install(project, "forge-log")
        capsys.readouterr()
        run(ListOptions(), project_dir=project)
        lines = {l.split()[0]: l for l in capsys.readouterr().out.splitlines()[1:]}
        assert "installé" in lines["forge-log"]
        assert "disponible" in lines["pycatch"]

    def test_installed_filter(self, project: Path, capsys) -> None:
        from forge.commands.modules import run

        _install(project, "forge-log")
        capsys.readouterr()
        run(ListOptions(installed=True), project_dir=project)
        out = capsys.readouterr().out
        assert "forge-log" in out and "pycatch" not in out

    def test_installed_filter_when_empty(self, tmp_path: Path, capsys) -> None:
        from forge.commands.modules import run

        run(ListOptions(installed=True), project_dir=tmp_path)
        assert "Aucun module installé" in capsys.readouterr().out

    def test_shows_pypi_package(self, tmp_path: Path, capsys) -> None:
        from forge.commands.modules import run

        run(ListOptions(), project_dir=tmp_path)
        assert "django-forge-auth>=0.2.1" in capsys.readouterr().out


# ===========================================================================
# forge doctor
# ===========================================================================


class TestDoctor:
    def _levels(self, project: Path) -> list[tuple[str, str]]:
        from forge.commands.doctor import diagnose

        with patch("forge.core.environment.installed_version", return_value="1.0"):
            return [(c.level, c.message) for c in diagnose(project, "p")]

    def test_no_settings(self, tmp_path: Path) -> None:
        from forge.commands.doctor import diagnose

        assert diagnose(tmp_path)[0].level == "ERREUR"

    def test_without_state_warns(self, project: Path) -> None:
        levels = self._levels(project)
        assert any(l == "ATTENTION" and "forge.toml" in m for l, m in levels)

    def test_healthy_project_has_no_errors(self, project: Path) -> None:
        _install(project, "forge-log")
        levels = self._levels(project)
        assert not [m for l, m in levels if l == "ERREUR"]

    def test_missing_app_is_an_error(self, project: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        _install(project, "forge-log")
        remove_from_list_setting(project / "p" / "settings.py", "INSTALLED_APPS", "forge_log")
        assert any(l == "ERREUR" and "INSTALLED_APPS" in m for l, m in self._levels(project))

    def test_missing_middleware_is_an_error(self, project: Path) -> None:
        from forge.core.config_manager import remove_from_list_setting

        _install(project, "forge-log")
        remove_from_list_setting(
            project / "p" / "settings.py", "MIDDLEWARE", "forge_log.middleware.RequestContextMiddleware"
        )
        assert any(l == "ERREUR" and "MIDDLEWARE" in m for l, m in self._levels(project))

    def test_missing_setting_is_an_error(self, project: Path) -> None:
        from forge.core.config_manager import remove_setting

        _install(project, "forge-auth")
        remove_setting(project / "p" / "settings.py", "AUTH_USER_MODEL")
        assert any(l == "ERREUR" and "AUTH_USER_MODEL" in m for l, m in self._levels(project))

    def test_package_not_installed_is_an_error(self, project: Path) -> None:
        from forge.commands.doctor import diagnose

        _install(project, "forge-log")
        with patch("forge.core.environment.installed_version", return_value=None):
            checks = diagnose(project, "p")
        assert any(c.level == "ERREUR" and "django-forge-log" in c.message for c in checks)

    def test_empty_env_key_is_a_warning(self, project: Path) -> None:
        _install(project, "forge-auth")
        levels = self._levels(project)
        assert any(l == "ATTENTION" and "JWT_SIGNING_KEY" in m for l, m in levels)

    def test_filled_env_key_is_ok(self, project: Path) -> None:
        _install(project, "forge-auth")
        (project / ".env").write_text("SECRET_KEY=abc\nJWT_SIGNING_KEY=def\n")
        levels = self._levels(project)
        assert any(l == "OK" and "JWT_SIGNING_KEY" in m for l, m in levels)

    def test_commented_env_key_is_ignored(self, project: Path) -> None:
        _install(project, "forge-auth")
        (project / ".env").write_text("# JWT_SIGNING_KEY=def\n")
        assert any(l == "ATTENTION" and "JWT_SIGNING_KEY" in m for l, m in self._levels(project))

    def test_service_marker_checked(self, project: Path) -> None:
        state = State(services=["redis"])
        save_state(project, state)
        assert any(l == "ERREUR" and "redis" in m for l, m in self._levels(project))
        (project / "p" / "settings.py").write_text(SETTINGS + "\n# forge: redis\nREDIS_URL = 'x'\n")
        assert any(l == "OK" and "redis" in m for l, m in self._levels(project))

    def test_unknown_module_warns(self, project: Path) -> None:
        state = State()
        state.record_module("ghost", "1", [])
        save_state(project, state)
        assert any(l == "ATTENTION" and "ghost" in m for l, m in self._levels(project))

    def test_run_exits_1_on_error(self, project: Path) -> None:
        from forge.commands.doctor import run

        save_state(project, State(services=["redis"]))
        with patch("forge.core.environment.installed_version", return_value="1"):
            with pytest.raises(typer.Exit) as exc:
                run(project_dir=project, project_name="p")
        assert exc.value.exit_code == 1

    def test_run_exits_0_when_healthy(self, project: Path, capsys) -> None:
        from forge.commands.doctor import run

        _install(project, "forge-log")
        with patch("forge.core.environment.installed_version", return_value="1"):
            run(project_dir=project, project_name="p")
        assert "0 erreur(s)" in capsys.readouterr().out

    def test_run_outside_project(self, tmp_path: Path, monkeypatch) -> None:
        from forge.commands.doctor import run

        monkeypatch.chdir(tmp_path)
        with patch("forge.core.engine.find_manage_py", side_effect=FileNotFoundError("pas de projet")):
            with pytest.raises(typer.Exit) as exc:
                run()
        assert exc.value.exit_code == 1

    def test_find_settings_without_name(self, project: Path) -> None:
        from forge.commands.doctor import _find_settings

        assert _find_settings(project, None) == project / "p" / "settings.py"
        assert _find_settings(project, "other") is None


# ===========================================================================
# main.py : nouvelles commandes
# ===========================================================================


runner = CliRunner()


class TestMainNewCommands:
    def test_remove_options(self) -> None:
        from forge.main import app

        with patch("forge.commands.remove.run") as run:
            runner.invoke(app, ["remove", "forge-log", "--dry-run", "-y"])
        assert run.call_args.kwargs["module_name"] == "forge-log"
        assert run.call_args.kwargs["options"] == RemoveOptions(dry_run=True, yes=True)

    def test_remove_defaults(self) -> None:
        from forge.main import app

        with patch("forge.commands.remove.run") as run:
            runner.invoke(app, ["remove", "forge-log"])
        assert run.call_args.kwargs["options"] == RemoveOptions()

    def test_upgrade_with_and_without_module(self) -> None:
        from forge.main import app

        with patch("forge.commands.upgrade.run") as run:
            runner.invoke(app, ["upgrade"])
            assert run.call_args.kwargs["module_name"] is None
            runner.invoke(app, ["upgrade", "forge-log"])
            assert run.call_args.kwargs["module_name"] == "forge-log"

    def test_list_options(self) -> None:
        from forge.main import app

        with patch("forge.commands.modules.run") as run:
            runner.invoke(app, ["list", "--installed"])
        assert run.call_args.kwargs["options"] == ListOptions(installed=True)

    def test_doctor(self) -> None:
        from forge.main import app

        with patch("forge.commands.doctor.run") as run:
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        run.assert_called_once_with()
