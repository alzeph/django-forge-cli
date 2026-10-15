"""
Tests de détail : chaque petit paramètre, valeur par défaut et branche limite.

Complète test_core / test_command / test_blueprint sans les dupliquer :
- manifestes embarqués (chaque champ de chacun des 7 modules) ;
- dataclasses d'options ;
- config_manager (idempotence, remplacement de bloc) ;
- handlers `configure` (postgis, --dev, mysql, channels, erreurs) ;
- helpers d'`install` et d'`init` ;
- routage de `main.py` (CliRunner) ;
- blueprints (entrées invalides, catalogue vide).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from forge.commands._options import (
    AddOptions,
    ConfigureOptions,
    InitOptions,
    InstallOptions,
)
from forge.commands.install import _APPS_DIR
from forge.core.dependency_resolver import InstallPlan, Manifest, build_registry, resolve

SETTINGS = 'INSTALLED_APPS = [\n    "django.contrib.admin",\n]\n\nROOT_URLCONF = "p.urls"\n'


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def settings_file(tmp_path: Path) -> Path:
    return _write(tmp_path / "settings.py", SETTINGS)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "manage.py").write_text("")
    pkg = tmp_path / "p"
    pkg.mkdir()
    _write(pkg / "settings.py", SETTINGS + "DEBUG = True\n")
    _write(pkg / "urls.py", "from django.urls import path\n\nurlpatterns = [\n]\n")
    return tmp_path


# ===========================================================================
# Manifestes embarqués : chaque champ
# ===========================================================================


def _manifest(folder: str) -> dict:
    return json.loads((_APPS_DIR / folder / "manifest.json").read_text(encoding="utf-8"))


class TestShippedManifests:
    EXPECTED = {
        "forge-auth": "forge_auth",
        "forge-test": "forge_test",
        "forge-core": "forge_core",
        "forge-log": "forge_log",
        "event-bus": "event_bus",
        "goroutine": "goroutine",
        "pycatch": "pycatch",
    }

    def test_registry_contains_exactly_the_shipped_modules(self) -> None:
        assert set(build_registry(_APPS_DIR)) == set(self.EXPECTED)

    @pytest.mark.parametrize(("name", "folder"), EXPECTED.items())
    def test_folder_name_matches_module_name(self, name: str, folder: str) -> None:
        assert _manifest(folder)["name"] == name
        assert name.replace("-", "_") == folder

    @pytest.mark.parametrize("folder", EXPECTED.values())
    def test_every_manifest_has_one_pypi_package_with_lower_bound(self, folder: str) -> None:
        packages = _manifest(folder)["python_packages"]
        assert len(packages) == 1
        assert ">=" in packages[0]

    @pytest.mark.parametrize("folder", EXPECTED.values())
    def test_every_manifest_only_uses_known_keys(self, folder: str) -> None:
        known = {
            "name", "version", "dependencies", "configure", "env_required",
            "python_packages", "installed_apps", "middleware", "settings",
            "urls", "requires_python",
        }
        assert set(_manifest(folder)) <= known

    @pytest.mark.parametrize("folder", EXPECTED.values())
    def test_every_manifest_resolves_without_error(self, folder: str) -> None:
        registry = build_registry(_APPS_DIR)
        plan = resolve(_manifest(folder)["name"], registry)
        assert plan.order[-1] == _manifest(folder)["name"]

    @pytest.mark.parametrize("folder", EXPECTED.values())
    def test_no_stale_cache_or_source_in_module_dir(self, folder: str) -> None:
        names = {p.name for p in (_APPS_DIR / folder).iterdir() if p.name != "__pycache__"}
        assert names == {"manifest.json"}

    def test_versions(self) -> None:
        versions = {f: _manifest(f)["version"] for f in self.EXPECTED.values()}
        assert versions == {
            "forge_auth": "0.2.1",
            "forge_test": "0.8.0",
            "forge_core": "0.1.0",
            "forge_log": "1.0.0rc3",
            "event_bus": "1.0.0",
            "goroutine": "1.0.0rc2",
            "pycatch": "1.0.0rc1",
        }

    def test_forge_auth_fields(self) -> None:
        m = _manifest("forge_auth")
        assert m["dependencies"] == ["forge-test"]
        assert m["configure"] == ["drf"]
        assert m["env_required"] == ["SECRET_KEY", "JWT_SIGNING_KEY"]
        assert m["installed_apps"] == ["rest_framework_simplejwt.token_blacklist", "forge_auth"]
        assert m["settings"] == {"AUTH_USER_MODEL": "forge_auth.User", "FORGE_AUTH": {}}
        assert m["urls"] == {"prefix": "api/", "include": "forge_auth.urls"}
        assert "requires_python" not in m

    def test_forge_test_has_no_django_config(self) -> None:
        m = _manifest("forge_test")
        assert m["dependencies"] == [] and m["configure"] == []
        for key in ("installed_apps", "middleware", "settings", "urls"):
            assert key not in m

    def test_forge_core_fields(self) -> None:
        m = _manifest("forge_core")
        assert m["python_packages"] == ["django-forge-core>=0.1.0"]
        assert m["dependencies"] == [] and m["configure"] == []
        for key in ("installed_apps", "middleware", "settings", "urls", "requires_python"):
            assert key not in m

    def test_forge_log_fields(self) -> None:
        m = _manifest("forge_log")
        assert m["installed_apps"] == ["forge_log"]
        assert m["middleware"] == ["forge_log.middleware.RequestContextMiddleware"]
        assert "settings" not in m and "urls" not in m

    def test_event_bus_fields(self) -> None:
        m = _manifest("event_bus")
        assert m["requires_python"] == "3.13"
        assert m["installed_apps"] == ["django_event_bus"]
        assert m["settings"] == {"EVENT_BUS": {"SERVICE_NAME": "{project_name}"}}

    def test_goroutine_fields(self) -> None:
        m = _manifest("goroutine")
        assert m["requires_python"] == "3.13"
        assert m["dependencies"] == ["pycatch"]
        assert m["installed_apps"] == ["django_goroutine"]

    def test_pycatch_fields(self) -> None:
        m = _manifest("pycatch")
        assert m["requires_python"] == "3.12"
        assert m["python_packages"] == ["pycatch-safe>=1.0.0rc1"]
        assert "installed_apps" not in m

    def test_cli_declares_pycatch_dependency(self) -> None:
        pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        assert "pycatch-safe>=1.0.0rc1" in pyproject


# ===========================================================================
# Manifest / InstallPlan : valeurs par défaut et agrégation
# ===========================================================================


class TestManifestDefaults:
    def test_every_field_default(self) -> None:
        m = Manifest(name="x")
        assert m.version == "0.0.0"
        assert m.dependencies == [] and m.configure == [] and m.env_required == []
        assert m.python_packages == [] and m.installed_apps == [] and m.middleware == []
        assert m.settings == {} and m.urls is None and m.requires_python == ""

    def test_default_lists_are_not_shared_between_instances(self) -> None:
        a, b = Manifest(name="a"), Manifest(name="b")
        a.installed_apps.append("x")
        a.settings["k"] = 1
        assert b.installed_apps == [] and b.settings == {}

    def test_from_dict_reads_every_field(self) -> None:
        m = Manifest.from_dict(
            {
                "name": "n", "version": "2.0", "dependencies": ["d"], "configure": ["c"],
                "env_required": ["E"], "python_packages": ["p>=1"], "installed_apps": ["a"],
                "middleware": ["m"], "settings": {"K": "V"},
                "urls": {"prefix": "x/", "include": "n.urls"}, "requires_python": "3.13",
            }
        )
        assert (m.version, m.dependencies, m.configure) == ("2.0", ["d"], ["c"])
        assert (m.env_required, m.python_packages) == (["E"], ["p>=1"])
        assert (m.installed_apps, m.middleware, m.settings) == (["a"], ["m"], {"K": "V"})
        assert m.urls == {"prefix": "x/", "include": "n.urls"}
        assert m.requires_python == "3.13"

    def test_name_is_required(self) -> None:
        with pytest.raises(KeyError):
            Manifest.from_dict({})


class TestInstallPlanDefaults:
    def test_every_field_default(self) -> None:
        p = InstallPlan()
        assert p.order == [] and p.services_to_configure == [] and p.env_keys == []
        assert p.python_packages == [] and p.installed_apps == [] and p.middleware == []
        assert p.settings_to_apply == {} and p.urls_to_wire == [] and p.min_python is None


class TestResolverAggregation:
    def test_first_module_to_declare_a_setting_wins(self) -> None:
        registry = {
            "dep": Manifest(name="dep", settings={"K": "dep"}),
            "root": Manifest(name="root", dependencies=["dep"], settings={"K": "root", "J": 1}),
        }
        assert resolve("root", registry).settings_to_apply == {"K": "dep", "J": 1}

    def test_duplicate_url_declarations_are_deduplicated(self) -> None:
        url = {"prefix": "a/", "include": "x.urls"}
        registry = {
            "dep": Manifest(name="dep", urls=url),
            "root": Manifest(name="root", dependencies=["dep"], urls=dict(url)),
        }
        assert resolve("root", registry).urls_to_wire == [url]

    def test_distinct_urls_keep_install_order(self) -> None:
        u1, u2 = {"prefix": "a/", "include": "a.urls"}, {"prefix": "b/", "include": "b.urls"}
        registry = {
            "a": Manifest(name="a", urls=u1),
            "b": Manifest(name="b", dependencies=["a"], urls=u2),
        }
        assert resolve("b", registry).urls_to_wire == [u1, u2]

    def test_middleware_and_apps_are_deduplicated(self) -> None:
        registry = {
            "a": Manifest(name="a", middleware=["m"], installed_apps=["x"]),
            "b": Manifest(name="b", dependencies=["a"], middleware=["m", "n"], installed_apps=["x"]),
        }
        plan = resolve("b", registry)
        assert plan.middleware == ["m", "n"] and plan.installed_apps == ["x"]

    def test_min_python_without_constraint_is_none(self) -> None:
        assert resolve("a", {"a": Manifest(name="a")}).min_python is None

    def test_min_python_keeps_the_highest_regardless_of_order(self) -> None:
        registry = {
            "hi": Manifest(name="hi", requires_python="3.13"),
            "lo": Manifest(name="lo", dependencies=["hi"], requires_python="3.12"),
        }
        assert resolve("lo", registry).min_python == (3, 13)

    def test_min_python_without_minor_defaults_to_zero(self) -> None:
        assert resolve("a", {"a": Manifest(name="a", requires_python="4")}).min_python == (4, 0)

    def test_parse_version_empty(self) -> None:
        from forge.core.dependency_resolver import _parse_version

        assert _parse_version("") is None
        assert _parse_version("3.13") == (3, 13)

    def test_self_cycle_is_detected(self) -> None:
        from forge.core.dependency_resolver import resolve_safe

        result = resolve_safe("a", {"a": Manifest(name="a", dependencies=["a"])})
        assert result.is_err() and "a -> a" in str(result.error)

    def test_resolve_safe_ok_value_is_the_plan(self) -> None:
        from forge.core.dependency_resolver import resolve_safe

        result = resolve_safe("a", {"a": Manifest(name="a")})
        assert result.is_ok() and result.value.order == ["a"]

    def test_resolve_safe_does_not_swallow_other_exceptions(self) -> None:
        from forge.core.dependency_resolver import resolve_safe

        with pytest.raises(AttributeError):
            resolve_safe("a", {"a": None})  # type: ignore[dict-item]

    def test_build_registry_skips_plain_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.txt").write_text("x")
        assert build_registry(tmp_path) == {}


# ===========================================================================
# Options
# ===========================================================================


class TestOptionsDefaults:
    def test_init(self) -> None:
        assert InitOptions().install == []

    def test_add(self) -> None:
        o = AddOptions()
        assert o.no_urls is False and o.templates is None

    def test_install(self) -> None:
        assert InstallOptions().dry_run is False

    def test_configure(self) -> None:
        o = ConfigureOptions()
        assert o.postgis is False and o.dev is None

    def test_options_are_frozen(self) -> None:
        with pytest.raises(Exception):
            InstallOptions().dry_run = True  # type: ignore[misc]

    def test_init_lists_are_independent(self) -> None:
        assert InitOptions().install is not InitOptions().install


# ===========================================================================
# config_manager
# ===========================================================================


class TestConfigManager:
    def test_installed_apps_appends_once(self, settings_file: Path) -> None:
        from forge.core.config_manager import add_to_installed_apps

        assert add_to_installed_apps(settings_file, "forge_log") is True
        assert add_to_installed_apps(settings_file, "forge_log") is False
        assert settings_file.read_text().count('"forge_log"') == 1

    def test_installed_apps_keeps_existing_entries_in_order(self, settings_file: Path) -> None:
        from forge.core.config_manager import add_to_installed_apps

        add_to_installed_apps(settings_file, "a")
        add_to_installed_apps(settings_file, "b")
        text = settings_file.read_text()
        assert text.index("django.contrib.admin") < text.index('"a"') < text.index('"b"')

    def test_dotted_app_label(self, settings_file: Path) -> None:
        from forge.core.config_manager import add_to_installed_apps

        add_to_installed_apps(settings_file, "rest_framework_simplejwt.token_blacklist")
        assert '"rest_framework_simplejwt.token_blacklist"' in settings_file.read_text()

    def test_result_is_valid_python(self, settings_file: Path) -> None:
        from forge.core.config_manager import add_to_installed_apps

        add_to_installed_apps(settings_file, "x")
        compile(settings_file.read_text(), "settings.py", "exec")

    def test_add_to_list_setting_creates_nothing_for_missing_list(self, settings_file: Path) -> None:
        from forge.core.config_manager import add_to_list_setting

        before = settings_file.read_text()
        assert add_to_list_setting(settings_file, "MIDDLEWARE", "m") is False
        assert settings_file.read_text() == before

    def test_add_to_list_setting_on_other_list(self, tmp_path: Path) -> None:
        from forge.core.config_manager import add_to_list_setting

        s = _write(tmp_path / "s.py", 'MIDDLEWARE = [\n    "a",\n]\n')
        assert add_to_list_setting(s, "MIDDLEWARE", "b") is True
        assert add_to_list_setting(s, "MIDDLEWARE", "b") is False
        assert '"b"' in s.read_text()

    def test_missing_file(self, tmp_path: Path) -> None:
        from forge.core.config_manager import add_to_installed_apps

        with pytest.raises(FileNotFoundError):
            add_to_installed_apps(tmp_path / "nope.py", "x")

    @pytest.mark.parametrize(
        "value", ["text", 3, True, None, {}, {"A": {"B": "c"}}, ["x", "y"]]
    )
    def test_simple_setting_roundtrip(self, tmp_path: Path, value: object) -> None:
        from forge.core.config_manager import add_simple_setting

        s = _write(tmp_path / "s.py", "")
        assert add_simple_setting(s, "K", value) is True
        namespace: dict = {}
        exec(s.read_text(), namespace)
        assert namespace["K"] == value

    def test_simple_setting_not_overwritten(self, tmp_path: Path) -> None:
        from forge.core.config_manager import add_simple_setting

        s = _write(tmp_path / "s.py", "K = 1\n")
        assert add_simple_setting(s, "K", 2) is False
        assert s.read_text() == "K = 1\n"

    def test_setting_exists(self, tmp_path: Path) -> None:
        from forge.core.config_manager import setting_exists

        s = _write(tmp_path / "s.py", "A = 1\n")
        assert setting_exists(s, "A") is True
        assert setting_exists(s, "B") is False

    def test_append_raw_block_dedents_and_separates(self, tmp_path: Path) -> None:
        from forge.core.config_manager import append_raw_block

        s = _write(tmp_path / "s.py", "A = 1\n")
        assert append_raw_block(s, "\n    B = 2\n") is True
        assert s.read_text() == "A = 1\n\nB = 2\n"

    def test_append_raw_block_guard_makes_it_idempotent(self, tmp_path: Path) -> None:
        from forge.core.config_manager import append_raw_block

        s = _write(tmp_path / "s.py", "A = 1\n")
        assert append_raw_block(s, "# g\nB = 2", guard_comment="# g") is True
        assert append_raw_block(s, "# g\nB = 2", guard_comment="# g") is False
        assert s.read_text().count("# g") == 1

    def test_replace_block_appends_when_guard_absent(self, tmp_path: Path) -> None:
        from forge.core.config_manager import replace_or_append_raw_block

        s = _write(tmp_path / "s.py", "A = 1\n")
        replace_or_append_raw_block(s, "# g\nB = 2", guard_comment="# g")
        assert s.read_text() == "A = 1\n\n# g\nB = 2\n"

    def test_replace_block_replaces_in_place(self, tmp_path: Path) -> None:
        from forge.core.config_manager import replace_or_append_raw_block

        s = _write(tmp_path / "s.py", "A = 1\n\n# g\nB = 2\n\nC = 3\n")
        replace_or_append_raw_block(s, "# g\nB = 9", guard_comment="# g")
        text = s.read_text()
        assert "B = 9" in text and "B = 2" not in text
        assert text.count("# g") == 1 and "C = 3" in text and text.startswith("A = 1")

    def test_replace_block_at_end_of_file(self, tmp_path: Path) -> None:
        from forge.core.config_manager import replace_or_append_raw_block

        s = _write(tmp_path / "s.py", "A = 1\n\n# g\nB = 2")
        replace_or_append_raw_block(s, "# g\nB = 9", guard_comment="# g")
        assert s.read_text().endswith("B = 9\n") and "B = 2" not in s.read_text()


# ===========================================================================
# configure : chaque handler et chaque option
# ===========================================================================


class TestConfigureDetails:
    def _run(self, handler: str, path: Path, **opts) -> str:
        from forge.commands import configure

        getattr(configure, handler)(path, ConfigureOptions(**opts))
        return path.read_text()

    def test_services_registry(self) -> None:
        from forge.commands.configure import AVAILABLE_SERVICES

        assert AVAILABLE_SERVICES == ["celery", "channels", "drf", "mysql", "pgsql", "redis"]

    @pytest.mark.parametrize(
        ("alias", "engine"),
        [
            ("sqlite", "django.db.backends.sqlite3"),
            ("sqlite3", "django.db.backends.sqlite3"),
            ("mysql", "django.db.backends.mysql"),
            ("postgresql", "django.db.backends.postgresql"),
            ("postgres", "django.db.backends.postgresql"),
            ("oracle", "django.db.backends.oracle"),
        ],
    )
    def test_dev_engine(self, alias: str, engine: str) -> None:
        from forge.commands.configure import _dev_engine

        assert _dev_engine(alias) == engine

    def test_pgsql_default(self, settings_file: Path) -> None:
        text = self._run("_configure_pgsql", settings_file)
        assert "django.db.backends.postgresql" in text
        assert 'os.getenv("DB_PORT", "5432")' in text
        assert "if DEBUG" not in text

    def test_pgsql_postgis(self, settings_file: Path) -> None:
        text = self._run("_configure_pgsql", settings_file, postgis=True)
        assert "django.contrib.gis.db.backends.postgis" in text

    def test_pgsql_dev_sqlite(self, settings_file: Path) -> None:
        text = self._run("_configure_pgsql", settings_file, dev="sqlite")
        assert "if DEBUG:" in text and "django.db.backends.sqlite3" in text
        assert 'BASE_DIR / "db.sqlite3"' in text
        assert "django.db.backends.postgresql" in text

    def test_pgsql_is_replaced_not_duplicated(self, settings_file: Path) -> None:
        self._run("_configure_pgsql", settings_file)
        text = self._run("_configure_pgsql", settings_file, postgis=True)
        assert text.count("# forge: pgsql") == 1
        assert "postgis" in text

    def test_mysql_default(self, settings_file: Path) -> None:
        text = self._run("_configure_mysql", settings_file)
        assert "django.db.backends.mysql" in text and '"3306"' in text

    def test_mysql_dev(self, settings_file: Path) -> None:
        text = self._run("_configure_mysql", settings_file, dev="sqlite")
        assert "if DEBUG:" in text and "db.sqlite3" in text

    def test_remove_sqlite_block(self, tmp_path: Path) -> None:
        from forge.commands.configure import _remove_sqlite_databases

        s = _write(
            tmp_path / "s.py",
            "A = 1\n\n# Database\nDATABASES = {\n    'default': {\n"
            "        'ENGINE': 'django.db.backends.sqlite3',\n        'NAME': 'x',\n    }\n}\nB = 2\n",
        )
        _remove_sqlite_databases(s)
        assert "sqlite3" not in s.read_text() and "B = 2" in s.read_text()

    def test_remove_sqlite_noop_without_sqlite(self, settings_file: Path) -> None:
        from forge.commands.configure import _remove_sqlite_databases

        before = settings_file.read_text()
        _remove_sqlite_databases(settings_file)
        assert settings_file.read_text() == before

    def test_remove_sqlite_noop_when_already_forge(self, tmp_path: Path) -> None:
        from forge.commands.configure import _remove_sqlite_databases

        s = _write(tmp_path / "s.py", "# forge: pgsql\nx = 'sqlite3'\n")
        _remove_sqlite_databases(s)
        assert "sqlite3" in s.read_text()

    def test_redis_default_url(self, settings_file: Path) -> None:
        assert 'redis://localhost:6379/0' in self._run("_configure_redis", settings_file)

    def test_celery_uses_redis_and_writes_app_file(self, tmp_path: Path) -> None:
        pkg = tmp_path / "proj"
        pkg.mkdir()
        s = _write(pkg / "settings.py", "TIME_ZONE = 'UTC'\n")
        text = self._run("_configure_celery", s)
        assert "CELERY_BROKER_URL" in text and 'CELERY_ACCEPT_CONTENT = ["json"]' in text
        celery = (pkg / "celery.py").read_text()
        assert 'Celery("proj")' in celery and "proj.settings" in celery

    def test_celery_does_not_overwrite_existing_app_file(self, tmp_path: Path) -> None:
        pkg = tmp_path / "proj"
        pkg.mkdir()
        s = _write(pkg / "settings.py", "TIME_ZONE = 'UTC'\n")
        (pkg / "celery.py").write_text("custom")
        self._run("_configure_celery", s)
        assert (pkg / "celery.py").read_text() == "custom"

    def test_channels_uses_project_asgi(self, tmp_path: Path) -> None:
        s = _write(tmp_path / "settings.py", "A = 1\n")
        text = self._run("_configure_channels", s)
        assert "channels_redis.core.RedisChannelLayer" in text

    def test_drf_without_forge_auth_uses_session_auth(self, settings_file: Path) -> None:
        text = self._run("_configure_drf", settings_file)
        assert "SessionAuthentication" in text and "JWTAuthenticationFlexible" not in text
        assert '"rest_framework"' in text and '"drf_spectacular"' in text
        assert '"PAGE_SIZE": 20' in text

    def test_drf_with_forge_auth_uses_jwt(self, tmp_path: Path) -> None:
        s = _write(tmp_path / "settings.py", 'INSTALLED_APPS = [\n    "forge_auth",\n]\n')
        text = self._run("_configure_drf", s)
        assert "forge_auth.authentification.JWTAuthenticationFlexible" in text
        assert "SessionAuthentication" not in text

    def test_drf_is_idempotent(self, settings_file: Path) -> None:
        self._run("_configure_drf", settings_file)
        text = self._run("_configure_drf", settings_file)
        assert text.count("# forge: drf") == 1 and text.count('"drf_spectacular"') == 1

    def test_run_unknown_service(self, project: Path) -> None:
        from forge.commands.configure import run

        with pytest.raises(typer.Exit) as exc:
            run("nope", ConfigureOptions(), project_root=project)
        assert exc.value.exit_code == 1

    def test_run_known_service(self, project: Path) -> None:
        from forge.commands.configure import run

        with patch("forge.core.environment.install_packages") as install:
            run("redis", ConfigureOptions(), project_root=project)
        assert "# forge: redis" in (project / "p" / "settings.py").read_text()
        install.assert_called_once_with(["redis>=5.0"], project)

    def test_find_settings_ignores_test_and_migrations_dirs(self, tmp_path: Path) -> None:
        from forge.commands.configure import _find_settings

        for d in ("test", "migrations"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "settings.py").write_text("")
        with pytest.raises(FileNotFoundError):
            _find_settings(tmp_path)


# ===========================================================================
# install : petits helpers
# ===========================================================================


class TestInstallHelpers:
    def test_render_placeholders_scalars_untouched(self) -> None:
        from forge.commands.install import _render_placeholders

        assert _render_placeholders(3, "x") == 3
        assert _render_placeholders(None, "x") is None
        assert _render_placeholders(True, "x") is True

    def test_render_placeholders_nested(self) -> None:
        from forge.commands.install import _render_placeholders

        value = {"a": ["{project_name}", {"b": "p-{project_name}-{project_name}"}], "c": 1}
        assert _render_placeholders(value, "shop") == {
            "a": ["shop", {"b": "p-shop-shop"}],
            "c": 1,
        }

    def test_render_placeholders_does_not_mutate_input(self) -> None:
        from forge.commands.install import _render_placeholders

        value = {"a": "{project_name}"}
        _render_placeholders(value, "x")
        assert value == {"a": "{project_name}"}

    def test_check_python_version_boundaries(self) -> None:
        from forge.commands.install import _check_python_version

        major, minor = sys.version_info[:2]
        assert _check_python_version((major, minor)) is None
        assert _check_python_version((major, minor + 1)) is not None
        assert _check_python_version((major, 0)) is None

    def test_check_python_version_message_contents(self) -> None:
        from forge.commands.install import _check_python_version

        message = _check_python_version((99, 5))
        assert "99.5+" in message and f"{sys.version_info[0]}.{sys.version_info[1]}" in message

    def test_run_installer_returns_exit_code(self) -> None:
        from forge.commands.install import _run_installer

        with patch("forge.core.environment.subprocess.run", return_value=MagicMock(returncode=3)):
            assert _run_installer(["x"]).value == 3

    def test_run_installer_captures_missing_executable(self) -> None:
        from forge.commands.install import _run_installer

        with patch("forge.core.environment.subprocess.run", side_effect=FileNotFoundError("uv")):
            assert _run_installer(["uv"]).is_err()

    def test_run_installer_lets_other_errors_propagate(self) -> None:
        from forge.commands.install import _run_installer

        with patch("forge.core.environment.subprocess.run", side_effect=PermissionError):
            with pytest.raises(PermissionError):
                _run_installer(["uv"])

    def test_install_packages_returns_true_on_success(self) -> None:
        from forge.commands.install import _install_python_packages

        with patch("forge.core.environment.subprocess.run", return_value=MagicMock(returncode=0)):
            assert _install_python_packages(["a>=1"]) is True

    def test_install_packages_returns_false_when_all_fail(self) -> None:
        from forge.commands.install import _install_python_packages

        with patch("forge.core.environment.subprocess.run", return_value=MagicMock(returncode=1)):
            assert _install_python_packages(["a>=1"]) is False

    def test_install_packages_falls_back_when_uv_fails(self) -> None:
        from forge.commands.install import _install_python_packages

        results = [MagicMock(returncode=2), MagicMock(returncode=0)]
        with patch("forge.core.environment.subprocess.run", side_effect=results) as run:
            assert _install_python_packages(["a>=1"]) is True
        assert run.call_count == 2

    def test_install_packages_targets_current_interpreter(self) -> None:
        from forge.commands.install import _install_python_packages

        with patch("forge.core.environment.subprocess.run", return_value=MagicMock(returncode=0)) as run:
            _install_python_packages(["a>=1", "b>=2"])
        cmd = run.call_args[0][0]
        assert cmd[:3] == ["uv", "pip", "install"]
        assert cmd[cmd.index("--python") + 1] == sys.executable
        assert cmd[-2:] == ["a>=1", "b>=2"]

    def test_install_packages_manual_hint_quotes_specs(self, capsys) -> None:
        from forge.commands.install import _install_python_packages

        with patch("forge.core.environment.subprocess.run", return_value=MagicMock(returncode=1)):
            _install_python_packages(["a>=1"])
        assert 'uv pip install "a>=1"' in capsys.readouterr().err

    def test_detect_project_package(self, project: Path) -> None:
        from forge.commands.install import _detect_project_package

        assert _detect_project_package(project) == "p"

    def test_detect_project_package_missing(self, tmp_path: Path) -> None:
        from forge.commands.install import _detect_project_package

        with pytest.raises(FileNotFoundError):
            _detect_project_package(tmp_path)

    def test_ensure_env_keys_order_and_header(self, tmp_path: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        _ensure_env_keys(["B", "A"], tmp_path)
        text = (tmp_path / ".env").read_text()
        assert text.index("B=") < text.index("A=") and "forge install" in text

    def test_ensure_env_keys_ignores_commented_keys(self, tmp_path: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        (tmp_path / ".env").write_text("# KEY=old\n", encoding="utf-8")
        _ensure_env_keys(["KEY"], tmp_path)
        assert "\nKEY=\n" in (tmp_path / ".env").read_text()

    def test_ensure_env_keys_keeps_values_with_equals(self, tmp_path: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        (tmp_path / ".env").write_text("URL=a=b\n", encoding="utf-8")
        _ensure_env_keys(["URL"], tmp_path)
        assert (tmp_path / ".env").read_text() == "URL=a=b\n"

    def test_print_plan_lists_everything(self, capsys) -> None:
        from forge.commands.install import _print_plan

        plan = InstallPlan(
            order=["a", "b"], services_to_configure=["drf"], python_packages=["p>=1"], env_keys=["K"]
        )
        _print_plan(plan, "b")
        out = capsys.readouterr().out
        assert "1. a" in out and "2. b" in out and "drf" in out
        assert "PyPI" in out and "p>=1" in out and "K" in out

    def test_print_plan_omits_empty_sections(self, capsys) -> None:
        from forge.commands.install import _print_plan

        _print_plan(InstallPlan(order=["a"]), "a")
        out = capsys.readouterr().out
        assert "Services" not in out and "PyPI" not in out and "environnement" not in out


class TestApplyManifestConfigDetails:
    def test_custom_namespace_is_used(self, project: Path) -> None:
        from forge.commands.install import _apply_manifest_config

        plan = InstallPlan(urls_to_wire=[{"include": "x.urls", "namespace": "custom"}])
        _apply_manifest_config(plan, project, project / "p" / "settings.py", "p")
        assert 'namespace="custom"' in (project / "p" / "urls.py").read_text()

    def test_missing_prefix_defaults_to_empty(self, project: Path) -> None:
        from forge.commands.install import _apply_manifest_config

        plan = InstallPlan(urls_to_wire=[{"include": "x.urls"}])
        _apply_manifest_config(plan, project, project / "p" / "settings.py", "p")
        assert 'path("", include("x.urls", namespace="x"))' in (project / "p" / "urls.py").read_text()

    def test_empty_plan_changes_nothing(self, project: Path) -> None:
        from forge.commands.install import _apply_manifest_config

        s = project / "p" / "settings.py"
        before = s.read_text()
        _apply_manifest_config(InstallPlan(), project, s, "p")
        assert s.read_text() == before

    def test_event_bus_service_name_is_project_name(self, project: Path) -> None:
        from forge.commands.install import _apply_manifest_config

        plan = resolve("event-bus", build_registry(_APPS_DIR))
        _apply_manifest_config(plan, project, project / "p" / "settings.py", "shop")
        assert "'SERVICE_NAME': 'shop'" in (project / "p" / "settings.py").read_text()

    def test_forge_log_full_chain_on_real_settings(self, tmp_path: Path) -> None:
        from forge.commands.install import _apply_manifest_config

        s = _write(
            tmp_path / "settings.py",
            'INSTALLED_APPS = [\n    "django.contrib.admin",\n]\nMIDDLEWARE = [\n    "a",\n]\n',
        )
        plan = resolve("forge-log", build_registry(_APPS_DIR))
        _apply_manifest_config(plan, tmp_path, s, "p")
        _apply_manifest_config(plan, tmp_path, s, "p")
        text = s.read_text()
        assert text.count('"forge_log"') == 1
        assert text.count("RequestContextMiddleware") == 1

    def test_dry_run_prints_python_warning_without_exiting(self, project: Path, capsys) -> None:
        from forge.commands.install import run

        registry = {"m": Manifest(name="m", requires_python="99.0")}
        with patch("forge.commands.install.build_registry", return_value=registry):
            run("m", InstallOptions(dry_run=True), project_dir=project, project_name="p")
        captured = capsys.readouterr()
        assert "99.0" in captured.err and "dry-run" in captured.out

    def test_unknown_module_message_lists_available(self, project: Path, capsys) -> None:
        from forge.commands.install import run

        with pytest.raises(typer.Exit):
            run("ghost", InstallOptions(), project_dir=project, project_name="p")
        err = capsys.readouterr().err
        assert "ghost" in err and "forge-auth" in err and "goroutine" in err

    def test_full_run_with_pypi_manifest_end_to_end(self, project: Path) -> None:
        from forge.commands.install import run

        with patch("forge.commands.install._install_python_packages") as pip:
            run("forge-log", InstallOptions(), project_dir=project, project_name="p")
        text = (project / "p" / "settings.py").read_text()
        pip.assert_called_once_with(["django-forge-log>=1.0.0rc3"], project)
        assert '"forge_log"' in text
        assert not (project / "forge_log").exists()

    def test_full_run_forge_auth_wires_everything(self, project: Path) -> None:
        from forge.commands.install import run

        with (
            patch("forge.commands.install._install_python_packages") as pip,
            patch("forge.commands.install._configure_services") as services,
        ):
            run("forge-auth", InstallOptions(), project_dir=project, project_name="p")
        text = (project / "p" / "settings.py").read_text()
        assert "AUTH_USER_MODEL = 'forge_auth.User'" in text and "FORGE_AUTH = {}" in text
        assert 'include("forge_auth.urls", namespace="forge_auth")' in (project / "p" / "urls.py").read_text()
        assert pip.call_args[0][0] == ["django-forge-test>=0.8.0", "django-forge-auth>=0.2.1"]
        services.assert_called_once_with(["drf"], project)
        env = (project / ".env").read_text()
        assert "SECRET_KEY=" in env and "JWT_SIGNING_KEY=" in env


# ===========================================================================
# init : helpers
# ===========================================================================


class TestInitHelpers:
    @pytest.mark.parametrize("name", ["shop", "my_site", "_x", "a1"])
    def test_valid_project_names(self, name: str) -> None:
        from forge.commands.init import _validate_project_name

        _validate_project_name(name)

    @pytest.mark.parametrize("name", ["1shop", "my-site", "a b", "", "x.y"])
    def test_invalid_project_names(self, name: str) -> None:
        from forge.commands.init import _validate_project_name

        with pytest.raises(typer.Exit):
            _validate_project_name(name)

    def test_target_directory_must_not_exist(self, tmp_path: Path) -> None:
        from forge.commands.init import _validate_target_directory

        _validate_target_directory(tmp_path / "new")
        with pytest.raises(typer.Exit):
            _validate_target_directory(tmp_path)

    def test_startproject_failure_propagates_returncode(self, tmp_path: Path) -> None:
        from forge.commands.init import _run_django_startproject

        with patch("forge.commands.init.subprocess.run", return_value=MagicMock(returncode=7)):
            with pytest.raises(typer.Exit) as exc:
                _run_django_startproject("x", tmp_path)
        assert exc.value.exit_code == 7

    def test_startproject_command_line(self, tmp_path: Path) -> None:
        from forge.commands.init import _run_django_startproject

        with patch("forge.commands.init.subprocess.run", return_value=MagicMock(returncode=0)) as run:
            _run_django_startproject("shop", tmp_path)
        cmd = run.call_args[0][0]
        assert cmd == [sys.executable, "-m", "django", "startproject", "shop", str(tmp_path / "shop")]
        assert run.call_args.kwargs["cwd"] == tmp_path

    def test_initial_dotenv_content(self, tmp_path: Path) -> None:
        from forge.commands.init import _create_initial_dotenv

        _create_initial_dotenv(tmp_path)
        lines = (tmp_path / ".env").read_text().splitlines()
        assert lines[0].startswith("SECRET_KEY=") and len(lines[0]) > 50
        assert lines[1:] == ["DEBUG=True", "ALLOWED_HOSTS=localhost,127.0.0.1"]

    def test_initial_dotenv_secret_is_random(self, tmp_path: Path) -> None:
        from forge.commands.init import _create_initial_dotenv

        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(), b.mkdir()
        _create_initial_dotenv(a)
        _create_initial_dotenv(b)
        assert (a / ".env").read_text() != (b / ".env").read_text()

    def test_initial_dotenv_never_overwrites(self, tmp_path: Path) -> None:
        from forge.commands.init import _create_initial_dotenv

        (tmp_path / ".env").write_text("MINE=1\n")
        _create_initial_dotenv(tmp_path)
        assert (tmp_path / ".env").read_text() == "MINE=1\n"

    def test_overlay_substitutes_project_name(self, tmp_path: Path) -> None:
        from forge.commands.init import _apply_forge_settings_overlay

        (tmp_path / "shop").mkdir()
        _apply_forge_settings_overlay(tmp_path, "shop")
        assert "{{project_name}}" not in (tmp_path / "shop" / "settings.py").read_text()
        assert (tmp_path / "shop" / "urls.py").is_file()
        assert (tmp_path / "templates" / "base.html").is_file()
        assert (tmp_path / ".env").is_file()

    def test_base_template_not_overwritten(self, tmp_path: Path) -> None:
        from forge.commands.init import _create_base_template

        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "base.html").write_text("mine")
        _create_base_template(tmp_path)
        assert (tmp_path / "templates" / "base.html").read_text() == "mine"

    def test_register_forge_test(self, project: Path) -> None:
        from forge.commands.init import _register_forge_test

        _register_forge_test(project, "p")
        assert '"forge_test"' in (project / "p" / "settings.py").read_text()

    def test_install_modules_runs_each_in_order(self, tmp_path: Path) -> None:
        from forge.commands.init import _install_modules

        with patch("forge.commands.install.run") as run:
            _install_modules(["forge-log", "goroutine"], tmp_path, "p")
        assert [c.kwargs["module_name"] for c in run.call_args_list] == ["forge-log", "goroutine"]
        assert all(c.kwargs["project_name"] == "p" for c in run.call_args_list)

    def test_run_with_install_option_installs_modules(self, tmp_path: Path) -> None:
        from forge.commands.init import run

        with (
            patch("forge.commands.init._run_django_startproject"),
            patch("forge.commands.init._apply_forge_settings_overlay"),
            patch("forge.commands.init._install_modules") as modules,
        ):
            run("shop", InitOptions(install=["forge-log"]), output_dir=tmp_path)
        modules.assert_called_once_with(["forge-log"], tmp_path / "shop", "shop")

    def test_run_without_install_option_installs_nothing(self, tmp_path: Path) -> None:
        from forge.commands.init import run

        with (
            patch("forge.commands.init._run_django_startproject"),
            patch("forge.commands.init._apply_forge_settings_overlay"),
            patch("forge.commands.init._install_modules") as modules,
        ):
            run("shop", InitOptions(), output_dir=tmp_path)
        modules.assert_not_called()


# ===========================================================================
# main.py : routage et options Typer
# ===========================================================================


runner = CliRunner()


class TestMain:
    def test_forge_command_names(self) -> None:
        from forge.main import _forge_command_names

        assert _forge_command_names() == {
            "init", "templates", "add", "install", "configure",
            "remove", "upgrade", "list", "doctor",
        }

    def test_help_lists_commands(self) -> None:
        from forge.main import app

        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for command in ("init", "templates", "add", "install", "configure", "remove", "upgrade", "list", "doctor"):
            assert command in result.output

    def test_install_passes_dry_run(self) -> None:
        from forge.main import app

        with patch("forge.commands.install.run") as run:
            result = runner.invoke(app, ["install", "forge-log", "--dry-run"])
        assert result.exit_code == 0
        assert run.call_args.kwargs["module_name"] == "forge-log"
        assert run.call_args.kwargs["options"] == InstallOptions(dry_run=True)

    def test_install_default_is_not_dry_run(self) -> None:
        from forge.main import app

        with patch("forge.commands.install.run") as run:
            runner.invoke(app, ["install", "forge-log"])
        assert run.call_args.kwargs["options"].dry_run is False

    def test_install_requires_module_name(self) -> None:
        from forge.main import app

        assert runner.invoke(app, ["install"]).exit_code != 0

    def test_configure_passes_options(self) -> None:
        from forge.main import app

        with patch("forge.commands.configure.run") as run:
            runner.invoke(app, ["configure", "pgsql", "--postgis", "--dev=sqlite"])
        assert run.call_args.kwargs["service"] == "pgsql"
        assert run.call_args.kwargs["options"] == ConfigureOptions(postgis=True, dev="sqlite")

    def test_configure_defaults(self) -> None:
        from forge.main import app

        with patch("forge.commands.configure.run") as run:
            runner.invoke(app, ["configure", "redis"])
        assert run.call_args.kwargs["options"] == ConfigureOptions()

    def test_add_without_templates_option(self) -> None:
        from forge.main import app

        with patch("forge.commands.add.run") as run:
            runner.invoke(app, ["add", "blog"])
        assert run.call_args.kwargs["options"] == AddOptions(no_urls=False, templates=None)

    def test_add_no_urls(self) -> None:
        from forge.main import app

        with patch("forge.commands.add.run") as run:
            runner.invoke(app, ["add", "blog", "--no-urls"])
        assert run.call_args.kwargs["options"].no_urls is True

    def test_add_templates_with_files(self) -> None:
        from forge.main import app

        with patch("forge.commands.add.run") as run:
            runner.invoke(app, ["add", "blog", "--templates=index.html, detail.html,"])
        assert run.call_args.kwargs["options"].templates == ["index.html", "detail.html"]

    def test_add_templates_empty_value_gives_empty_list(self) -> None:
        from forge.main import app

        with patch("forge.commands.add.run") as run:
            runner.invoke(app, ["add", "blog", "--templates="])
        assert run.call_args.kwargs["options"].templates == []

    def test_init_with_install_splits_and_strips(self) -> None:
        from forge.main import app

        with patch("forge.commands.init.run") as run:
            runner.invoke(app, ["init", "shop", "--install", "forge-auth, forge-log"])
        assert run.call_args.kwargs["options"].install == ["forge-auth", "forge-log"]
        assert run.call_args.kwargs["project_name"] == "shop"

    def test_init_without_install(self) -> None:
        from forge.main import app

        with patch("forge.commands.init.run") as run:
            runner.invoke(app, ["init", "shop"])
        assert run.call_args.kwargs["options"].install == []

    def test_init_with_template_delegates_to_blueprint(self) -> None:
        from forge.main import app

        with (
            patch("forge.commands.blueprint.run") as blueprint_run,
            patch("forge.commands.init.run") as init_run,
        ):
            runner.invoke(app, ["init", "shop", "--template", "saas"])
        blueprint_run.assert_called_once_with(project_name="shop", blueprint_name="saas")
        init_run.assert_not_called()

    def test_templates_command_prints_catalog(self) -> None:
        from forge.main import app

        result = runner.invoke(app, ["templates"])
        assert "saas" in result.output and "api" in result.output

    def test_main_routes_forge_command_to_typer(self) -> None:
        from forge import main as main_module

        with (
            patch.object(sys, "argv", ["forge", "install", "x"]),
            patch.object(main_module, "_forge_command_names", return_value={"install"}),
            patch.object(main_module, "app") as app,
        ):
            main_module.main()
        app.assert_called_once_with()

    def test_main_without_args_goes_to_typer(self) -> None:
        from forge import main as main_module

        with patch.object(sys, "argv", ["forge"]), patch.object(main_module, "app") as app:
            main_module.main()
        app.assert_called_once()

    def test_main_global_option_goes_to_typer(self) -> None:
        from forge import main as main_module

        with patch.object(sys, "argv", ["forge", "--help"]), patch.object(main_module, "app") as app:
            main_module.main()
        app.assert_called_once()

    def test_main_passthrough_exit_code(self) -> None:
        from forge import main as main_module

        with (
            patch.object(sys, "argv", ["forge", "migrate", "--plan"]),
            patch("forge.core.engine.run_django_command", return_value=3) as run,
        ):
            with pytest.raises(SystemExit) as exc:
                main_module.main()
        assert exc.value.code == 3
        run.assert_called_once_with(["migrate", "--plan"])

    def test_main_passthrough_without_manage_py(self, capsys) -> None:
        from forge import main as main_module

        with (
            patch.object(sys, "argv", ["forge", "migrate"]),
            patch("forge.core.engine.run_django_command", side_effect=FileNotFoundError("pas de manage.py")),
        ):
            with pytest.raises(SystemExit) as exc:
                main_module.main()
        assert exc.value.code == 1
        assert "pas de manage.py" in capsys.readouterr().err


# ===========================================================================
# blueprints
# ===========================================================================


class TestBlueprintDetails:
    def test_shipped_blueprint_contents(self) -> None:
        from forge.commands import blueprint as bp

        api, saas = bp.load_blueprint("api"), bp.load_blueprint("saas")
        assert api.install == ["forge-core"] and api.apps == ["core"]
        assert saas.install == ["forge-core", "forge-auth"]
        assert [s.service for s in saas.configure] == ["drf", "pgsql", "celery", "redis"]
        assert saas.configure[1].dev == "sqlite"

    def test_every_blueprint_references_known_modules_and_services(self) -> None:
        from forge.commands import blueprint as bp
        from forge.commands.configure import AVAILABLE_SERVICES

        modules = set(build_registry(_APPS_DIR))
        for blueprint in bp.list_blueprints():
            assert set(blueprint.install) <= modules, blueprint.name
            assert {s.service for s in blueprint.configure} <= set(AVAILABLE_SERVICES), blueprint.name

    def test_blueprints_are_sorted(self) -> None:
        from forge.commands import blueprint as bp

        names = [b.name for b in bp.list_blueprints()]
        assert names == sorted(names)

    def test_configure_step_defaults(self) -> None:
        from forge.commands.blueprint import ConfigureStep

        step = ConfigureStep(service="drf")
        assert step.postgis is False and step.dev is None

    def test_unknown_blueprint_lists_available(self, capsys) -> None:
        from forge.commands import blueprint as bp

        with pytest.raises(typer.Exit) as exc:
            bp.load_blueprint("ghost")
        assert exc.value.exit_code == 1
        err = capsys.readouterr().err
        assert "ghost" in err and "api" in err and "saas" in err

    def test_empty_catalog(self, tmp_path: Path, capsys) -> None:
        from forge.commands import blueprint as bp

        with patch.object(bp, "BLUEPRINTS_DIR", tmp_path / "missing"):
            assert bp.list_blueprints() == []
            bp.print_catalog()
        assert "Aucun blueprint" in capsys.readouterr().out

    def test_catalog_shows_usage_hint(self, capsys) -> None:
        from forge.commands import blueprint as bp

        bp.print_catalog()
        assert "forge init <nom_projet> --template <blueprint>" in capsys.readouterr().out

    def test_directory_without_blueprint_json_is_ignored(self, tmp_path: Path) -> None:
        from forge.commands import blueprint as bp

        (tmp_path / "empty").mkdir()
        with patch.object(bp, "BLUEPRINTS_DIR", tmp_path):
            assert bp.list_blueprints() == []


# ===========================================================================
# add : helpers
# ===========================================================================


class TestAddDetails:
    @pytest.mark.parametrize(("given", "stem"), [("index.html", "index"), ("index", "index"), ("a.b.html", "a.b")])
    def test_template_stem(self, given: str, stem: str) -> None:
        from forge.commands.add import _template_stem

        assert _template_stem(given) == stem

    def test_invalid_app_name(self) -> None:
        from forge.commands.add import _validate_app_name

        _validate_app_name("blog")
        for bad in ("my-app", "1app", ""):
            with pytest.raises(typer.Exit):
                _validate_app_name(bad)

    def test_existing_app_dir_rejected(self, tmp_path: Path) -> None:
        from forge.commands.add import _validate_app_does_not_exist

        _validate_app_does_not_exist("blog", tmp_path)
        (tmp_path / "blog").mkdir()
        with pytest.raises(typer.Exit):
            _validate_app_does_not_exist("blog", tmp_path)

    def test_startapp_failure_propagates_code(self, tmp_path: Path) -> None:
        from forge.commands.add import _run_startapp

        with patch("forge.commands.add.run_django_command", return_value=5):
            with pytest.raises(typer.Exit) as exc:
                _run_startapp("blog", tmp_path)
        assert exc.value.exit_code == 5

    def test_local_urls_with_and_without_routes(self, tmp_path: Path) -> None:
        from forge.commands.add import _create_local_urls

        (tmp_path / "blog").mkdir()
        _create_local_urls("blog", tmp_path)
        assert "urlpatterns" in (tmp_path / "blog" / "urls.py").read_text()
        _create_local_urls("blog", tmp_path, ["index", "detail"])
        text = (tmp_path / "blog" / "urls.py").read_text()
        assert 'path("index/", views.index, name="index")' in text
        assert 'path("detail/", views.detail, name="detail")' in text

    def test_wire_warns_when_main_urls_missing(self, tmp_path: Path, capsys) -> None:
        from forge.commands.add import _wire_urls_in_project_router

        _wire_urls_in_project_router("blog", tmp_path)
        assert "introuvable" in capsys.readouterr().err

    def test_wire_uses_app_prefix(self, project: Path) -> None:
        from forge.commands.add import _wire_urls_in_project_router

        _wire_urls_in_project_router("blog", project)
        assert 'path("blog/", include("blog.urls", namespace="blog"))' in (project / "p" / "urls.py").read_text()


# ===========================================================================
# urls_manager / engine / console
# ===========================================================================


class TestUrlsAndEngine:
    def test_wire_is_idempotent(self, project: Path) -> None:
        from forge.core.urls_manager import wire_url_include

        assert wire_url_include(project, "a/", "a.urls", "a") is True
        assert wire_url_include(project, "a/", "a.urls", "a") is False
        assert (project / "p" / "urls.py").read_text().count("a.urls") == 1

    def test_wire_adds_include_import_once(self, project: Path) -> None:
        from forge.core.urls_manager import wire_url_include

        wire_url_include(project, "a/", "a.urls", "a")
        wire_url_include(project, "b/", "b.urls", "b")
        assert (project / "p" / "urls.py").read_text().count("from django.urls import include, path") == 1

    def test_wire_returns_false_without_main_urls(self, tmp_path: Path) -> None:
        from forge.core.urls_manager import wire_url_include

        assert wire_url_include(tmp_path, "a/", "a.urls", "a") is False

    def test_wire_returns_false_without_urlpatterns(self, project: Path) -> None:
        from forge.core.urls_manager import wire_url_include

        (project / "p" / "urls.py").write_text("x = 1\n")
        assert wire_url_include(project, "a/", "a.urls", "a") is False

    def test_find_main_urls_none_without_settings(self, tmp_path: Path) -> None:
        from forge.core.urls_manager import find_main_urls

        assert find_main_urls(tmp_path) is None

    def test_find_main_urls_none_without_root_urlconf(self, tmp_path: Path) -> None:
        from forge.core.urls_manager import find_main_urls

        (tmp_path / "p").mkdir()
        (tmp_path / "p" / "settings.py").write_text("A = 1\n")
        assert find_main_urls(tmp_path) is None

    def test_find_main_urls_none_when_file_missing(self, tmp_path: Path) -> None:
        from forge.core.urls_manager import find_main_urls

        (tmp_path / "p").mkdir()
        (tmp_path / "p" / "settings.py").write_text('ROOT_URLCONF = "p.urls"\n')
        assert find_main_urls(tmp_path) is None

    def test_find_manage_py_walks_up(self, project: Path) -> None:
        from forge.core.engine import find_manage_py

        deep = project / "p" / "x" / "y"
        deep.mkdir(parents=True)
        assert find_manage_py(deep) == (project / "manage.py").resolve()

    def test_find_manage_py_missing_message(self, tmp_path: Path) -> None:
        from forge.core.engine import find_manage_py

        with patch("pathlib.Path.is_file", return_value=False):
            with pytest.raises(FileNotFoundError, match="manage.py"):
                find_manage_py(tmp_path)

    def test_run_django_command_returns_exit_code_and_uses_manage_py_dir(self, project: Path) -> None:
        from forge.core.engine import run_django_command

        with patch("forge.core.engine.subprocess.run", return_value=MagicMock(returncode=4)) as run:
            assert run_django_command(["check"], project_root=project) == 4
        assert run.call_args[0][0] == [sys.executable, str((project / "manage.py").resolve()), "check"]
        assert run.call_args.kwargs["cwd"] == project.resolve()


# ===========================================================================
# install : modules à sources embarquées (cas historique conservé)
# ===========================================================================


class TestEmbeddedSourceModules:
    def _source(self, root: Path, files: dict[str, str]) -> Path:
        apps = root / "_apps"
        module = apps / "demo_mod"
        module.mkdir(parents=True)
        (module / "manifest.json").write_text("{}")
        for name, content in files.items():
            (module / name).write_text(content)
        return apps

    def test_sources_are_copied_without_manifest_and_app_registered(self, project: Path) -> None:
        from forge.commands.install import _install_single_module

        apps = self._source(project, {"__init__.py": "", "models.py": "X = 1"})
        with patch("forge.commands.install._APPS_DIR", apps):
            _install_single_module("demo-mod", project, project / "p" / "settings.py")

        assert (project / "demo_mod" / "models.py").read_text() == "X = 1"
        assert not (project / "demo_mod" / "manifest.json").exists()
        assert '"demo_mod"' in (project / "p" / "settings.py").read_text()

    def test_existing_copy_is_not_overwritten(self, project: Path, capsys) -> None:
        from forge.commands.install import _install_single_module

        apps = self._source(project, {"__init__.py": ""})
        (project / "demo_mod").mkdir()
        (project / "demo_mod" / "mine.py").write_text("keep")
        with patch("forge.commands.install._APPS_DIR", apps):
            _install_single_module("demo-mod", project, project / "p" / "settings.py")

        assert (project / "demo_mod" / "mine.py").read_text() == "keep"
        assert not (project / "demo_mod" / "__init__.py").exists()
        assert "déjà présent" in capsys.readouterr().out

    def test_unknown_module_dir_warns(self, project: Path, capsys) -> None:
        from forge.commands.install import _install_single_module

        with patch("forge.commands.install._APPS_DIR", project / "_none"):
            _install_single_module("ghost", project, project / "p" / "settings.py")
        assert "introuvable" in capsys.readouterr().err
