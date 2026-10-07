"""
Tests des commandes CLI de Django Forge.

Stratégie par commande
----------------------
- **configure** : logique pure (append_raw_block), testable sans Django.
- **add**       : helpers filesystem testables sans Django ; les étapes qui
                  appellent ``django-admin`` sont mockées.
- **install**   : ``_detect_project_package`` et ``_ensure_env_keys`` sont
                  testables en pur filesystem ; la résolution de dépendances
                  est déjà couverte dans test_core.py.
- **init**      : les appels subprocess sont mockés ; on vérifie l'orchestration.

Aucun test ne lance un vrai ``django-admin`` — subprocess.run est mocké partout
où il interviendrait, ce qui rend la suite rapide et sans dépendance
d'environnement.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import typer

from forge.commands._options import AddOptions, ConfigureOptions, InitOptions, InstallOptions


# ===========================================================================
# Fixtures communes
# ===========================================================================


MINIMAL_SETTINGS = """\
INSTALLED_APPS = [
    "django.contrib.admin",
]

ROOT_URLCONF = "myproject.urls"

DEBUG = True
"""

MINIMAL_URLS = """\
from django.urls import path

urlpatterns = [
]
"""


@pytest.fixture()
def project_tree(tmp_path: Path) -> Path:
    """
    Crée un projet Django factice minimal dans tmp_path :

        tmp_path/
            manage.py
            myproject/
                settings.py
                urls.py
    """
    (tmp_path / "manage.py").write_text("# manage.py")
    pkg = tmp_path / "myproject"
    pkg.mkdir()
    (pkg / "settings.py").write_text(MINIMAL_SETTINGS, encoding="utf-8")
    (pkg / "urls.py").write_text(MINIMAL_URLS, encoding="utf-8")
    return tmp_path


# ===========================================================================
# configure — _find_settings
# ===========================================================================


class TestConfigureFindSettings:
    def test_finds_settings_in_subpackage(self, project_tree: Path) -> None:
        from forge.commands.configure import _find_settings

        result = _find_settings(project_tree)
        assert result.name == "settings.py"
        assert result.exists()

    def test_raises_when_no_settings(self, tmp_path: Path) -> None:
        from forge.commands.configure import _find_settings

        with pytest.raises(FileNotFoundError):
            _find_settings(tmp_path)


# ===========================================================================
# configure — handlers de service (logique d'injection)
# ===========================================================================


class TestConfigureHandlers:
    """
    Teste que chaque handler injecte bien son bloc dans settings.py
    et que la protection guard_comment est active (idempotence).
    """

    def _settings(self, tmp_path: Path) -> Path:
        p = tmp_path / "settings.py"
        p.write_text(MINIMAL_SETTINGS, encoding="utf-8")
        return p

    def test_redis_injects_block(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_redis

        s = self._settings(tmp_path)
        _configure_redis(s, ConfigureOptions())
        assert "REDIS_URL" in s.read_text()

    def test_redis_is_idempotent(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_redis

        s = self._settings(tmp_path)
        _configure_redis(s, ConfigureOptions())
        _configure_redis(s, ConfigureOptions())
        assert s.read_text().count("# forge: redis") == 1

    def test_celery_injects_block(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_celery

        s = self._settings(tmp_path)
        _configure_celery(s, ConfigureOptions())
        content = s.read_text()
        assert "CELERY_BROKER_URL" in content
        assert "CELERY_RESULT_BACKEND" in content

    def test_drf_injects_block(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_drf

        s = self._settings(tmp_path)
        _configure_drf(s, ConfigureOptions())
        assert "REST_FRAMEWORK" in s.read_text()

    def test_drf_registers_apps_in_installed_apps(self, tmp_path: Path) -> None:
        """
        Sans ça, DRF configuré via `forge configure drf` (ou `forge install
        forge-auth`, qui en dépend) ne fonctionne pas : Django lève
        ImproperlyConfigured tant que 'rest_framework' n'est pas listé.
        """
        from forge.commands.configure import _configure_drf

        s = self._settings(tmp_path)
        _configure_drf(s, ConfigureOptions())
        content = s.read_text()
        assert '"rest_framework"' in content
        assert '"drf_spectacular"' in content

    def test_pgsql_without_dev(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_pgsql

        s = self._settings(tmp_path)
        _configure_pgsql(s, ConfigureOptions())
        content = s.read_text()
        assert "postgresql" in content
        assert "if DEBUG" not in content

    def test_pgsql_with_dev_sqlite(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_pgsql

        s = self._settings(tmp_path)
        _configure_pgsql(s, ConfigureOptions(dev="sqlite"))
        content = s.read_text()
        assert "if DEBUG" in content
        # Le backend SQLite de Django s'appelle "sqlite3", pas "sqlite" :
        # une config avec "django.db.backends.sqlite" lève ImproperlyConfigured.
        assert '"django.db.backends.sqlite3"' in content
        assert '"django.db.backends.sqlite"' not in content
        assert "postgresql" in content

    def test_pgsql_with_postgis(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_pgsql

        s = self._settings(tmp_path)
        _configure_pgsql(s, ConfigureOptions(postgis=True))
        assert "postgis" in s.read_text()

    def test_mysql_without_dev(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_mysql

        s = self._settings(tmp_path)
        _configure_mysql(s, ConfigureOptions())
        assert "mysql" in s.read_text()

    def test_mysql_with_dev_sqlite(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_mysql

        s = self._settings(tmp_path)
        _configure_mysql(s, ConfigureOptions(dev="sqlite"))
        content = s.read_text()
        assert "if DEBUG" in content
        # Idem : le moteur DEBUG doit être "sqlite3", pas "sqlite".
        assert '"django.db.backends.sqlite3"' in content
        assert '"django.db.backends.sqlite"' not in content

    def test_channels_injects_block(self, tmp_path: Path) -> None:
        from forge.commands.configure import _configure_channels

        s = self._settings(tmp_path)
        _configure_channels(s, ConfigureOptions())
        assert "CHANNEL_LAYERS" in s.read_text()

    def test_unknown_service_exits(self, project_tree: Path) -> None:
        from forge.commands.configure import run

        with pytest.raises(typer.Exit) as exc_info:
            run(service="unknown_service", options=ConfigureOptions(), project_root=project_tree)
        assert exc_info.value.exit_code == 1


# ===========================================================================
# add — helpers filesystem
# ===========================================================================


class TestAddHelpers:
    def test_find_settings(self, project_tree: Path) -> None:
        from forge.commands.add import _find_settings

        result = _find_settings(project_tree)
        assert result.name == "settings.py"

    def test_find_settings_raises_when_absent(self, tmp_path: Path) -> None:
        from forge.commands.add import _find_settings

        with pytest.raises(FileNotFoundError):
            _find_settings(tmp_path)


# ===========================================================================
# add — _wire_urls_in_project_router
# ===========================================================================


class TestWireUrls:
    def test_injects_include_in_urlpatterns(self, project_tree: Path) -> None:
        from forge.commands.add import _wire_urls_in_project_router

        _wire_urls_in_project_router("blog", project_tree)
        content = (project_tree / "myproject" / "urls.py").read_text()
        assert "blog" in content
        assert "include" in content

    def test_adds_include_import_if_missing(self, project_tree: Path) -> None:
        from forge.commands.add import _wire_urls_in_project_router

        _wire_urls_in_project_router("shop", project_tree)
        content = (project_tree / "myproject" / "urls.py").read_text()
        assert "from django.urls import include, path" in content

    def test_does_not_duplicate_on_second_call(self, project_tree: Path) -> None:
        from forge.commands.add import _wire_urls_in_project_router

        _wire_urls_in_project_router("api", project_tree)
        _wire_urls_in_project_router("api", project_tree)
        content = (project_tree / "myproject" / "urls.py").read_text()
        assert content.count('"api/"') == 1

    def test_result_is_valid_python(self, project_tree: Path) -> None:
        import ast

        from forge.commands.add import _wire_urls_in_project_router

        _wire_urls_in_project_router("orders", project_tree)
        content = (project_tree / "myproject" / "urls.py").read_text()
        ast.parse(content)  # lève SyntaxError si invalide


# ===========================================================================
# add — _create_local_urls
# ===========================================================================


class TestCreateLocalUrls:
    def test_creates_urls_file(self, project_tree: Path) -> None:
        from forge.commands.add import _create_local_urls

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        _create_local_urls("blog", project_tree)

        urls_file = app_dir / "urls.py"
        assert urls_file.exists()
        content = urls_file.read_text()
        assert 'app_name = "blog"' in content
        assert "urlpatterns" in content

    def test_urls_file_is_valid_python(self, project_tree: Path) -> None:
        import ast

        from forge.commands.add import _create_local_urls

        (project_tree / "myapp").mkdir()
        _create_local_urls("myapp", project_tree)
        content = (project_tree / "myapp" / "urls.py").read_text()
        ast.parse(content)

    def test_with_view_names_generates_routes(self, project_tree: Path) -> None:
        from forge.commands.add import _create_local_urls

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        _create_local_urls("blog", project_tree, view_names=["index", "detail"])

        content = (app_dir / "urls.py").read_text()
        assert 'path("index/", views.index, name="index")' in content
        assert 'path("detail/", views.detail, name="detail")' in content

    def test_with_view_names_is_valid_python(self, project_tree: Path) -> None:
        import ast

        from forge.commands.add import _create_local_urls

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        _create_local_urls("blog", project_tree, view_names=["index", "detail"])

        ast.parse((app_dir / "urls.py").read_text())

    def test_empty_view_names_matches_historical_behavior(self, project_tree: Path) -> None:
        from forge.commands.add import _create_local_urls

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        _create_local_urls("blog", project_tree, view_names=[])

        assert "urlpatterns: list = []" in (app_dir / "urls.py").read_text()


# ===========================================================================
# add — _create_view_functions
# ===========================================================================


class TestCreateViewFunctions:
    def test_appends_view_functions(self, project_tree: Path) -> None:
        from forge.commands.add import _create_view_functions

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        (app_dir / "views.py").write_text(
            "from django.shortcuts import render\n\n# Create your views here.\n",
            encoding="utf-8",
        )

        _create_view_functions("blog", project_tree, ["index", "detail"])

        content = (app_dir / "views.py").read_text()
        assert "def index(request):" in content
        assert 'render(request, "blog/index.html")' in content
        assert "def detail(request):" in content
        assert 'render(request, "blog/detail.html")' in content

    def test_adds_missing_render_import(self, project_tree: Path) -> None:
        from forge.commands.add import _create_view_functions

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        (app_dir / "views.py").write_text("# Create your views here.\n", encoding="utf-8")

        _create_view_functions("blog", project_tree, ["index"])

        content = (app_dir / "views.py").read_text()
        assert "from django.shortcuts import render" in content

    def test_result_is_valid_python(self, project_tree: Path) -> None:
        import ast

        from forge.commands.add import _create_view_functions

        app_dir = project_tree / "blog"
        app_dir.mkdir()
        (app_dir / "views.py").write_text(
            "from django.shortcuts import render\n\n# Create your views here.\n",
            encoding="utf-8",
        )

        _create_view_functions("blog", project_tree, ["index", "detail"])

        ast.parse((app_dir / "views.py").read_text())


# ===========================================================================
# add — _create_template_tree
# ===========================================================================


class TestCreateTemplateTree:
    def test_creates_directory_structure(self, project_tree: Path) -> None:
        from forge.commands.add import _create_template_tree

        (project_tree / "blog").mkdir()
        _create_template_tree("blog", project_tree, [])

        assert (project_tree / "blog" / "templates" / "blog").is_dir()

    def test_generates_specified_html_files(self, project_tree: Path) -> None:
        from forge.commands.add import _create_template_tree

        (project_tree / "blog").mkdir()
        _create_template_tree("blog", project_tree, ["index.html", "detail.html"])

        template_dir = project_tree / "blog" / "templates" / "blog"
        assert (template_dir / "index.html").exists()
        assert (template_dir / "detail.html").exists()

    def test_adds_html_extension_if_missing(self, project_tree: Path) -> None:
        from forge.commands.add import _create_template_tree

        (project_tree / "shop").mkdir()
        _create_template_tree("shop", project_tree, ["list"])  # sans .html

        assert (project_tree / "shop" / "templates" / "shop" / "list.html").exists()

    def test_empty_list_creates_only_directory(self, project_tree: Path) -> None:
        from forge.commands.add import _create_template_tree

        (project_tree / "store").mkdir()
        _create_template_tree("store", project_tree, [])

        template_dir = project_tree / "store" / "templates" / "store"
        assert template_dir.is_dir()
        assert list(template_dir.iterdir()) == []


# ===========================================================================
# add — run() orchestration (subprocess mocké)
# ===========================================================================


class TestAddRun:
    def test_run_calls_startapp_and_registers(self, project_tree: Path) -> None:
        from forge.commands.add import run

        def fake_startapp(app_name, root):
            (root / app_name).mkdir()

        with (
            patch("forge.commands.add.run_django_command", return_value=0) as mock_cmd,
            patch("forge.commands.add._run_startapp", side_effect=fake_startapp),
        ):
            run("newapp", AddOptions(), project_root=project_tree)

        mock_cmd.assert_not_called()  # _run_startapp est mocké directement
        settings = (project_tree / "myproject" / "settings.py").read_text()
        assert '"newapp"' in settings

    def test_run_does_not_wire_app_into_its_own_urls(self, project_tree: Path) -> None:
        """
        Régression du branchement URL : `forge add blog` créait parfois
        `blog/urls.py` avec un `include("blog.urls")` pointant sur lui-même
        (boucle infinie) au lieu de brancher `myproject/urls.py`.
        """
        from forge.commands.add import run

        def fake_startapp(app_name, root):
            (root / app_name).mkdir()

        with patch("forge.commands.add._run_startapp", side_effect=fake_startapp):
            run("blog", AddOptions(), project_root=project_tree)

        blog_urls = (project_tree / "blog" / "urls.py").read_text()
        main_urls = (project_tree / "myproject" / "urls.py").read_text()

        assert "blog.urls" not in blog_urls
        assert 'include("blog.urls"' in main_urls

    def test_run_with_no_urls_skips_url_wiring(self, project_tree: Path) -> None:
        from forge.commands.add import run

        def fake_startapp(app_name, root):
            (root / app_name).mkdir()

        with patch("forge.commands.add._run_startapp", side_effect=fake_startapp):
            run("nourl", AddOptions(no_urls=True), project_root=project_tree)

        assert not (project_tree / "nourl" / "urls.py").exists()

    def test_run_with_templates_creates_tree(self, project_tree: Path) -> None:
        from forge.commands.add import run

        with patch(
            "forge.commands.add._run_startapp", side_effect=self._fake_startapp_with_views
        ):
            run("blog", AddOptions(templates=["index.html"]), project_root=project_tree)

        assert (project_tree / "blog" / "templates" / "blog" / "index.html").exists()

    def _fake_startapp_with_views(self, app_name, root):
        """Simule `startapp` en créant aussi views.py, comme le vrai django-admin."""
        app_dir = root / app_name
        app_dir.mkdir()
        (app_dir / "views.py").write_text(
            "from django.shortcuts import render\n\n# Create your views here.\n",
            encoding="utf-8",
        )

    def test_run_with_templates_generates_view_and_route(self, project_tree: Path) -> None:
        """
        Point 25 : une page de --templates doit être accessible immédiatement,
        sans code manuel — vue + route générées automatiquement.
        """
        from forge.commands.add import run

        with patch(
            "forge.commands.add._run_startapp", side_effect=self._fake_startapp_with_views
        ):
            run("blog", AddOptions(templates=["index.html"]), project_root=project_tree)

        views_content = (project_tree / "blog" / "views.py").read_text()
        urls_content = (project_tree / "blog" / "urls.py").read_text()

        assert "def index(request):" in views_content
        assert 'render(request, "blog/index.html")' in views_content
        assert 'path("index/", views.index, name="index")' in urls_content

    def test_run_with_templates_and_no_urls_skips_views_and_routes(
        self, project_tree: Path
    ) -> None:
        """--no-urls désactive tout routage : pas de vue générée non plus."""
        from forge.commands.add import run

        with patch(
            "forge.commands.add._run_startapp", side_effect=self._fake_startapp_with_views
        ):
            run(
                "blog",
                AddOptions(templates=["index.html"], no_urls=True),
                project_root=project_tree,
            )

        assert not (project_tree / "blog" / "urls.py").exists()
        views_content = (project_tree / "blog" / "views.py").read_text()
        assert "def index(request):" not in views_content
        # Le fichier HTML, lui, est toujours généré.
        assert (project_tree / "blog" / "templates" / "blog" / "index.html").exists()

    def test_run_invalid_name_exits(self, project_tree: Path) -> None:
        from forge.commands.add import run

        with pytest.raises(typer.Exit) as exc_info:
            run("invalid-name!", AddOptions(), project_root=project_tree)
        assert exc_info.value.exit_code == 1

    def test_run_existing_app_exits(self, project_tree: Path) -> None:
        from forge.commands.add import run

        # myproject/ existe déjà dans project_tree (fixture)
        with pytest.raises(typer.Exit) as exc_info:
            run("myproject", AddOptions(), project_root=project_tree)
        assert exc_info.value.exit_code == 1

    def test_startapp_failure_exits(self, project_tree: Path) -> None:
        from forge.commands.add import run

        def fake_startapp_fail(app_name, root):
            (root / app_name).mkdir()
            raise typer.Exit(code=1)

        with patch("forge.commands.add._run_startapp", side_effect=fake_startapp_fail):
            with pytest.raises(typer.Exit) as exc_info:
                run("broken", AddOptions(), project_root=project_tree)
            assert exc_info.value.exit_code == 1


# ===========================================================================
# install — _detect_project_package
# ===========================================================================


class TestDetectProjectPackage:
    def test_detects_package_with_settings(self, project_tree: Path) -> None:
        from forge.commands.install import _detect_project_package

        result = _detect_project_package(project_tree)
        assert result == "myproject"

    def test_raises_when_no_settings_found(self, tmp_path: Path) -> None:
        from forge.commands.install import _detect_project_package

        with pytest.raises(FileNotFoundError):
            _detect_project_package(tmp_path)


# ===========================================================================
# install — _ensure_env_keys
# ===========================================================================


class TestEnsureEnvKeys:
    def test_creates_env_file_with_missing_keys(self, project_tree: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        _ensure_env_keys(["SECRET_KEY", "DB_PASSWORD"], project_tree)

        env_file = project_tree / ".env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "SECRET_KEY=" in content
        assert "DB_PASSWORD=" in content

    def test_does_not_duplicate_existing_keys(self, project_tree: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        env_file = project_tree / ".env"
        env_file.write_text("SECRET_KEY=already_set\n", encoding="utf-8")

        _ensure_env_keys(["SECRET_KEY", "NEW_KEY"], project_tree)

        content = env_file.read_text()
        assert content.count("SECRET_KEY") == 1
        assert "NEW_KEY=" in content

    def test_no_op_when_all_keys_present(self, project_tree: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        env_file = project_tree / ".env"
        env_file.write_text("MY_KEY=value\n", encoding="utf-8")
        original_mtime = env_file.stat().st_mtime

        _ensure_env_keys(["MY_KEY"], project_tree)

        # Fichier non modifié
        assert env_file.stat().st_mtime == original_mtime

    def test_appends_to_existing_env_file(self, project_tree: Path) -> None:
        from forge.commands.install import _ensure_env_keys

        env_file = project_tree / ".env"
        env_file.write_text("EXISTING=value\n", encoding="utf-8")

        _ensure_env_keys(["NEW_KEY"], project_tree)

        content = env_file.read_text()
        assert "EXISTING=value" in content
        assert "NEW_KEY=" in content


# ===========================================================================
# install — run() avec dry_run
# ===========================================================================


class TestInstallDryRun:
    def test_dry_run_does_not_modify_settings(self, project_tree: Path, tmp_path: Path) -> None:
        from forge.commands.install import run

        # Registre fictif via patch
        from forge.core.dependency_resolver import Manifest

        fake_registry = {
            "forge-test": Manifest(name="forge-test"),
        }

        with (
            patch("forge.commands.install.build_registry", return_value=fake_registry),
            patch("forge.commands.install._detect_project_package", return_value="myproject"),
        ):
            settings = project_tree / "myproject" / "settings.py"
            original_content = settings.read_text()

            run(
                module_name="forge-test",
                options=InstallOptions(dry_run=True),
                project_dir=project_tree,
                project_name="myproject",
            )

            assert settings.read_text() == original_content


# ===========================================================================
# install — _install_python_packages
# ===========================================================================


class TestInstallPythonPackages:
    def test_tries_uv_pip_install_first(self) -> None:
        """
        uv est le gestionnaire documenté pour installer django-forge-cli
        (`uv add django-forge-cli`) et ses venvs n'embarquent pas pip par
        défaut — `uv pip install` doit donc être tenté avant `pip install`.
        """
        from forge.commands.install import _install_python_packages

        with patch("forge.commands.install.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _install_python_packages(["pyotp", "twilio"])

        mock_run.assert_called_once()
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "uv"
        assert "pip" in called_cmd and "install" in called_cmd
        assert "pyotp" in called_cmd
        assert "twilio" in called_cmd

    def test_falls_back_to_pip_when_uv_is_not_found(self) -> None:
        from forge.commands.install import _install_python_packages

        with patch("forge.commands.install.subprocess.run") as mock_run:
            mock_run.side_effect = [
                FileNotFoundError("uv introuvable"),
                MagicMock(returncode=0),
            ]
            _install_python_packages(["pyotp"])

        assert mock_run.call_count == 2
        fallback_cmd = mock_run.call_args_list[1][0][0]
        assert fallback_cmd[1:3] == ["-m", "pip"]
        assert "pyotp" in fallback_cmd

    def test_does_not_raise_when_both_attempts_fail(self) -> None:
        from forge.commands.install import _install_python_packages

        with patch("forge.commands.install.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            _install_python_packages(["broken-package"])  # ne doit pas lever

    def test_run_installs_python_packages_from_plan(self, project_tree: Path) -> None:
        from forge.commands.install import run
        from forge.core.dependency_resolver import Manifest

        fake_registry = {
            "forge-test": Manifest(name="forge-test", python_packages=["pyotp"]),
        }

        with (
            patch("forge.commands.install.build_registry", return_value=fake_registry),
            patch("forge.commands.install._detect_project_package", return_value="myproject"),
            patch("forge.commands.install._install_single_module"),
            patch("forge.commands.install.subprocess.run") as mock_pip,
        ):
            mock_pip.return_value = MagicMock(returncode=0)
            run(
                module_name="forge-test",
                options=InstallOptions(),
                project_dir=project_tree,
                project_name="myproject",
            )

        mock_pip.assert_called_once()
        assert "pyotp" in mock_pip.call_args[0][0]


# ===========================================================================
# install — _install_single_module (branchement URL + réglages spécifiques)
# ===========================================================================


class TestInstallSingleModule:
    def _fake_module(self, project_tree: Path, name: str, has_urls: bool) -> None:
        """Dépose un faux module source sous _APPS_DIR (patché sur ce test)."""
        source_dir = project_tree / "_fake_apps" / name
        source_dir.mkdir(parents=True)
        (source_dir / "manifest.json").write_text("{}")
        if has_urls:
            (source_dir / "urls.py").write_text(
                "from django.urls import path\n\n"
                f"app_name = '{name}'\n"
                "urlpatterns = []\n",
                encoding="utf-8",
            )

    def test_wires_module_urls_when_present(self, project_tree: Path) -> None:
        from forge.commands.install import _install_single_module

        self._fake_module(project_tree, "demo_mod", has_urls=True)

        with patch("forge.commands.install._APPS_DIR", project_tree / "_fake_apps"):
            settings_path = project_tree / "myproject" / "settings.py"
            _install_single_module("demo-mod", project_tree, settings_path)

        main_urls = (project_tree / "myproject" / "urls.py").read_text()
        assert 'include("demo_mod.urls", namespace="demo_mod")' in main_urls

    def test_does_not_wire_urls_when_module_has_none(self, project_tree: Path) -> None:
        from forge.commands.install import _install_single_module

        self._fake_module(project_tree, "forge_test", has_urls=False)

        with patch("forge.commands.install._APPS_DIR", project_tree / "_fake_apps"):
            settings_path = project_tree / "myproject" / "settings.py"
            _install_single_module("forge-test", project_tree, settings_path)

        main_urls = (project_tree / "myproject" / "urls.py").read_text()
        assert "include(" not in main_urls

    def test_sets_auth_user_model_for_forge_auth(self, project_tree: Path) -> None:
        from forge.commands.install import _install_single_module

        self._fake_module(project_tree, "forge_auth", has_urls=False)

        with patch("forge.commands.install._APPS_DIR", project_tree / "_fake_apps"):
            settings_path = project_tree / "myproject" / "settings.py"
            _install_single_module("forge-auth", project_tree, settings_path)

        content = settings_path.read_text()
        assert "AUTH_USER_MODEL" in content
        assert "forge_auth.User" in content

    def test_does_not_set_auth_user_model_for_other_modules(self, project_tree: Path) -> None:
        from forge.commands.install import _install_single_module

        self._fake_module(project_tree, "forge_test", has_urls=False)

        with patch("forge.commands.install._APPS_DIR", project_tree / "_fake_apps"):
            settings_path = project_tree / "myproject" / "settings.py"
            _install_single_module("forge-test", project_tree, settings_path)

        assert "AUTH_USER_MODEL" not in settings_path.read_text()


# ===========================================================================
# init — run() orchestration (subprocess mocké)
# ===========================================================================


class TestInitRun:
    def test_run_calls_startproject(self, tmp_path: Path) -> None:
        from forge.commands.init import run

        def fake_startproject(name, cwd):
            # simule ce que django-admin ferait
            (cwd / name / name).mkdir(parents=True)
            (cwd / name / name / "settings.py").write_text(MINIMAL_SETTINGS)

        with (
            patch("forge.commands.init._run_django_startproject", side_effect=fake_startproject),
            patch("forge.commands.init._apply_forge_settings_overlay"),
            patch("forge.commands.init._register_forge_test"),
        ):
            run("mysite", InitOptions(), output_dir=tmp_path)

    def test_invalid_project_name_exits(self, tmp_path: Path) -> None:
        from forge.commands.init import run

        with pytest.raises(typer.Exit) as exc_info:
            run("invalid-name!", InitOptions(), output_dir=tmp_path)
        assert exc_info.value.exit_code == 1

    def test_existing_directory_exits(self, tmp_path: Path) -> None:
        from forge.commands.init import run

        (tmp_path / "existing").mkdir()

        with pytest.raises(typer.Exit) as exc_info:
            run("existing", InitOptions(), output_dir=tmp_path)
        assert exc_info.value.exit_code == 1

    def test_startproject_failure_exits(self, tmp_path: Path) -> None:
        from forge.commands.init import run

        def fake_fail(name, cwd):
            raise typer.Exit(code=1)

        with patch("forge.commands.init._run_django_startproject", side_effect=fake_fail):
            with pytest.raises(typer.Exit) as exc_info:
                run("mysite", InitOptions(), output_dir=tmp_path)
            assert exc_info.value.exit_code == 1


# ===========================================================================
# install — application des settings & transmission de la racine
# ===========================================================================


class TestInstallSettingsAndRoot:
    def test_apply_settings_writes_new_key(self, tmp_path: Path) -> None:
        from forge.commands.install import _apply_settings

        s = tmp_path / "settings.py"
        s.write_text("DEBUG = True\n", encoding="utf-8")

        _apply_settings({"AUTH_USER_MODEL": "forge_auth.User"}, s)

        content = s.read_text()
        assert "AUTH_USER_MODEL" in content
        assert "forge_auth.User" in content

    def test_apply_settings_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        from forge.commands.install import _apply_settings

        s = tmp_path / "settings.py"
        s.write_text('AUTH_USER_MODEL = "existing.User"\n', encoding="utf-8")

        _apply_settings({"AUTH_USER_MODEL": "forge_auth.User"}, s)

        content = s.read_text()
        assert content.count("AUTH_USER_MODEL") == 1
        assert "existing.User" in content

    def test_configure_services_forwards_project_root(self, tmp_path: Path) -> None:
        from forge.commands.install import _configure_services

        with patch("forge.commands.configure.run") as configure_run:
            _configure_services(["redis"], tmp_path)

        assert configure_run.call_count == 1
        assert configure_run.call_args.kwargs["project_root"] == tmp_path
