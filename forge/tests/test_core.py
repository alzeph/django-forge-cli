"""
Tests unitaires du package ``forge.core``.

Stratégie
---------
- **config_manager** : on écrit des fichiers ``settings.py`` temporaires dans
  des répertoires tmp (``tmp_path`` de pytest) et on vérifie les mutations.
- **engine** : on mocke ``subprocess.run`` — on ne lance pas de vrai Django.
- **dependency_resolver** : on travaille entièrement en mémoire avec des
  ``Manifest`` construits à la main.

Dépendances de test : pytest, libcst (déjà requis par le projet).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports des modules sous test
# ---------------------------------------------------------------------------
# Ajuste le PYTHONPATH si les tests sont lancés depuis la racine du repo.

import sys, os
sys.path.insert(0, str(Path(__file__).parent.parent))

from forge.core.config_manager import (
    add_simple_setting,
    add_to_installed_apps,
    add_to_list_setting,
    append_raw_block,
    setting_exists,
)
from forge.core.dependency_resolver import (
    InstallPlan,
    Manifest,
    build_registry,
    load_manifest,
    resolve,
)
from forge.core.engine import find_manage_py, run_django_command
from forge.core.urls_manager import find_main_urls, wire_url_include


# ===========================================================================
# Fixtures
# ===========================================================================


MINIMAL_SETTINGS = """\
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
]

DEBUG = True
"""


@pytest.fixture()
def settings_file(tmp_path: Path) -> Path:
    """Crée un settings.py minimal dans un répertoire temporaire."""
    p = tmp_path / "settings.py"
    p.write_text(MINIMAL_SETTINGS, encoding="utf-8")
    return p


# ===========================================================================
# config_manager — add_to_installed_apps
# ===========================================================================


class TestAddToInstalledApps:
    def test_adds_new_app(self, settings_file: Path) -> None:
        modified = add_to_installed_apps(settings_file, "myapp")
        assert modified is True
        content = settings_file.read_text()
        assert '"myapp"' in content

    def test_idempotent_existing_app(self, settings_file: Path) -> None:
        modified = add_to_installed_apps(settings_file, "django.contrib.admin")
        assert modified is False
        # Le fichier ne doit pas avoir été modifié
        assert settings_file.read_text().count('"django.contrib.admin"') == 1

    def test_added_app_is_valid_python(self, settings_file: Path) -> None:
        add_to_installed_apps(settings_file, "forge_auth")
        # Vérifie que le fichier résultant est parseable
        import ast
        ast.parse(settings_file.read_text())

    def test_multiple_additions(self, settings_file: Path) -> None:
        add_to_installed_apps(settings_file, "app_one")
        add_to_installed_apps(settings_file, "app_two")
        content = settings_file.read_text()
        assert '"app_one"' in content
        assert '"app_two"' in content

    def test_multiple_additions_each_on_own_line(self, settings_file: Path) -> None:
        """
        Régression : chaque appel relit le fichier depuis le disque (comme le
        fait `forge install` pour chaque module de la chaîne de dépendances).
        Un bug de whitespace LibCST faisait que la 2e app ajoutée (et les
        suivantes) atterrissait sur la même ligne physique que la précédente
        au lieu d'une nouvelle ligne indentée.
        """
        add_to_installed_apps(settings_file, "forge_test")
        add_to_installed_apps(settings_file, "forge_auth")
        add_to_installed_apps(settings_file, "rest_framework")

        lines = settings_file.read_text().splitlines()
        assert '    "forge_test",' in lines
        assert '    "forge_auth",' in lines
        # Dernier élément ajouté : pas encore de virgule trailing (ajoutée au
        # prochain appel), mais doit être sur sa propre ligne, pas collé au
        # précédent.
        assert '    "rest_framework"' in lines

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            add_to_installed_apps(tmp_path / "nope.py", "myapp")


# ===========================================================================
# config_manager — add_to_list_setting
# ===========================================================================


class TestAddToListSetting:
    def test_adds_to_middleware(self, settings_file: Path) -> None:
        modified = add_to_list_setting(
            settings_file,
            "MIDDLEWARE",
            "django.middleware.csrf.CsrfViewMiddleware",
        )
        assert modified is True
        assert "CsrfViewMiddleware" in settings_file.read_text()

    def test_no_op_when_already_present(self, settings_file: Path) -> None:
        modified = add_to_list_setting(
            settings_file,
            "MIDDLEWARE",
            "django.middleware.security.SecurityMiddleware",
        )
        assert modified is False

    def test_unknown_setting_does_not_crash(self, settings_file: Path) -> None:
        # Si la variable n'existe pas, aucune modification ne doit avoir lieu
        modified = add_to_list_setting(settings_file, "NONEXISTENT_LIST", "value")
        assert modified is False


# ===========================================================================
# config_manager — add_simple_setting
# ===========================================================================


class TestAddSimpleSetting:
    def test_adds_new_key(self, settings_file: Path) -> None:
        modified = add_simple_setting(settings_file, "REDIS_URL", "redis://localhost:6379/0")
        assert modified is True
        assert "REDIS_URL" in settings_file.read_text()

    def test_does_not_overwrite_existing_key(self, settings_file: Path) -> None:
        # DEBUG est déjà dans MINIMAL_SETTINGS
        modified = add_simple_setting(settings_file, "DEBUG", False)
        assert modified is False
        # La valeur originale doit être préservée
        assert "DEBUG = True" in settings_file.read_text()

    def test_bool_value(self, settings_file: Path) -> None:
        add_simple_setting(settings_file, "USE_TZ", True)
        assert "USE_TZ = True" in settings_file.read_text()

    def test_int_value(self, settings_file: Path) -> None:
        add_simple_setting(settings_file, "SESSION_COOKIE_AGE", 3600)
        assert "SESSION_COOKIE_AGE = 3600" in settings_file.read_text()


# ===========================================================================
# config_manager — append_raw_block
# ===========================================================================


class TestAppendRawBlock:
    CELERY_BLOCK = """
        # forge: celery
        CELERY_BROKER_URL = "redis://localhost:6379/0"
        CELERY_RESULT_BACKEND = CELERY_BROKER_URL
    """

    def test_appends_block(self, settings_file: Path) -> None:
        modified = append_raw_block(settings_file, self.CELERY_BLOCK)
        assert modified is True
        assert "CELERY_BROKER_URL" in settings_file.read_text()

    def test_guard_comment_prevents_double_insert(self, settings_file: Path) -> None:
        append_raw_block(settings_file, self.CELERY_BLOCK, guard_comment="# forge: celery")
        modified = append_raw_block(
            settings_file, self.CELERY_BLOCK, guard_comment="# forge: celery"
        )
        assert modified is False
        # Le bloc contient CELERY_BROKER_URL 2x (clé + valeur de CELERY_RESULT_BACKEND).
        # Un seul insert → 2 occurrences. Le guard empêche un second insert → toujours 2.
        assert settings_file.read_text().count("CELERY_BROKER_URL") == 2

    def test_no_guard_allows_repeat(self, settings_file: Path) -> None:
        append_raw_block(settings_file, self.CELERY_BLOCK)
        modified = append_raw_block(settings_file, self.CELERY_BLOCK)
        assert modified is True


# ===========================================================================
# config_manager — setting_exists
# ===========================================================================


class TestSettingExists:
    def test_existing_key(self, settings_file: Path) -> None:
        assert setting_exists(settings_file, "DEBUG") is True

    def test_missing_key(self, settings_file: Path) -> None:
        assert setting_exists(settings_file, "CELERY_BROKER_URL") is False

    def test_installed_apps_exists(self, settings_file: Path) -> None:
        assert setting_exists(settings_file, "INSTALLED_APPS") is True


# ===========================================================================
# engine — find_manage_py
# ===========================================================================


class TestFindManagePy:
    def test_finds_in_current_dir(self, tmp_path: Path) -> None:
        manage = tmp_path / "manage.py"
        manage.write_text("# manage.py")
        result = find_manage_py(tmp_path)
        assert result == manage

    def test_finds_in_parent(self, tmp_path: Path) -> None:
        manage = tmp_path / "manage.py"
        manage.write_text("# manage.py")
        subdir = tmp_path / "myapp"
        subdir.mkdir()
        result = find_manage_py(subdir)
        assert result == manage

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="manage.py"):
            find_manage_py(tmp_path)


# ===========================================================================
# engine — run_django_command
# ===========================================================================


class TestRunDjangoCommand:
    def test_calls_subprocess_with_correct_args(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("")

        with patch("forge.core.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            code = run_django_command(["migrate", "--run-syncdb"], project_root=tmp_path)

        assert code == 0
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == sys.executable
        assert "manage.py" in called_cmd[1]
        assert "migrate" in called_cmd
        assert "--run-syncdb" in called_cmd

    def test_propagates_nonzero_returncode(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("")

        with patch("forge.core.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            code = run_django_command(["check"], project_root=tmp_path)

        assert code == 1


# ===========================================================================
# dependency_resolver — Manifest
# ===========================================================================


class TestManifest:
    def test_from_dict_minimal(self) -> None:
        m = Manifest.from_dict({"name": "forge-test"})
        assert m.name == "forge-test"
        assert m.dependencies == []
        assert m.configure == []
        assert m.env_required == []
        assert m.python_packages == []

    def test_from_dict_full(self) -> None:
        data = {
            "name": "forge-notification",
            "version": "2.0.0",
            "dependencies": ["forge-auth"],
            "configure": ["redis"],
            "env_required": ["NOTIFICATION_API_KEY"],
            "python_packages": ["twilio"],
        }
        m = Manifest.from_dict(data)
        assert m.version == "2.0.0"
        assert "forge-auth" in m.dependencies
        assert "redis" in m.configure
        assert "NOTIFICATION_API_KEY" in m.env_required
        assert "twilio" in m.python_packages

    def test_from_json(self, tmp_path: Path) -> None:
        data = {"name": "forge-test", "version": "1.0.0"}
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps(data))
        m = Manifest.from_json(p)
        assert m.name == "forge-test"

    def test_settings_default_empty(self) -> None:
        m = Manifest.from_dict({"name": "forge-test"})
        assert m.settings == {}

    def test_from_dict_parses_settings(self) -> None:
        m = Manifest.from_dict(
            {"name": "forge-auth", "settings": {"AUTH_USER_MODEL": "forge_auth.User"}}
        )
        assert m.settings == {"AUTH_USER_MODEL": "forge_auth.User"}


# ===========================================================================
# dependency_resolver — resolve
# ===========================================================================


def _make_registry() -> dict[str, Manifest]:
    """Registre fictif pour les tests de résolution."""
    return {
        "forge-test": Manifest(name="forge-test"),
        "forge-auth": Manifest(
            name="forge-auth",
            dependencies=["forge-test"],
            configure=[],
            python_packages=["pyotp"],
        ),
        "forge-notification": Manifest(
            name="forge-notification",
            dependencies=["forge-auth"],
            configure=["redis"],
            env_required=["NOTIFICATION_API_KEY"],
            python_packages=["twilio"],
        ),
    }


class TestResolve:
    def test_simple_chain(self) -> None:
        plan = resolve("forge-notification", _make_registry())
        # forge-test doit précéder forge-auth, qui précède forge-notification
        assert plan.order.index("forge-test") < plan.order.index("forge-auth")
        assert plan.order.index("forge-auth") < plan.order.index("forge-notification")

    def test_no_duplicate_in_order(self) -> None:
        plan = resolve("forge-notification", _make_registry())
        assert len(plan.order) == len(set(plan.order))

    def test_services_collected(self) -> None:
        plan = resolve("forge-notification", _make_registry())
        assert "redis" in plan.services_to_configure

    def test_env_keys_collected(self) -> None:
        plan = resolve("forge-notification", _make_registry())
        assert "NOTIFICATION_API_KEY" in plan.env_keys

    def test_python_packages_collected_and_deduplicated(self) -> None:
        plan = resolve("forge-notification", _make_registry())
        # pyotp vient de forge-auth (dépendance), twilio de forge-notification
        assert "pyotp" in plan.python_packages
        assert "twilio" in plan.python_packages
        assert plan.python_packages.count("pyotp") == 1

    def test_leaf_module(self) -> None:
        plan = resolve("forge-test", _make_registry())
        assert plan.order == ["forge-test"]
        assert plan.services_to_configure == []

    def test_unknown_module_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown-module"):
            resolve("unknown-module", _make_registry())

    def test_cycle_detection(self) -> None:
        cyclic_registry = {
            "a": Manifest(name="a", dependencies=["b"]),
            "b": Manifest(name="b", dependencies=["a"]),
        }
        with pytest.raises(ValueError, match="Cycle"):
            resolve("a", cyclic_registry)

    def test_diamond_deduplication(self) -> None:
        """
        Graphe en losange :  C dépend de A et B, A et B dépendent tous deux de base.
        base doit apparaître une seule fois.
        """
        registry = {
            "base": Manifest(name="base"),
            "A": Manifest(name="A", dependencies=["base"]),
            "B": Manifest(name="B", dependencies=["base"]),
            "C": Manifest(name="C", dependencies=["A", "B"]),
        }
        plan = resolve("C", registry)
        assert plan.order.count("base") == 1


# ===========================================================================
# dependency_resolver — load_manifest / build_registry
# ===========================================================================


class TestLoadManifest:
    def test_loads_existing_manifest(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "forge_auth"
        app_dir.mkdir()
        (app_dir / "manifest.json").write_text(
            json.dumps({"name": "forge-auth", "dependencies": ["forge-test"]})
        )
        m = load_manifest("forge-auth", tmp_path)
        assert m.name == "forge-auth"
        assert "forge-test" in m.dependencies

    def test_raises_for_missing_module(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest("forge-nonexistent", tmp_path)


class TestBuildRegistry:
    def test_builds_from_directory(self, tmp_path: Path) -> None:
        for name in ("forge_test", "forge_auth"):
            d = tmp_path / name
            d.mkdir()
            (d / "manifest.json").write_text(
                json.dumps({"name": name.replace("_", "-")})
            )

        registry = build_registry(tmp_path)
        assert "forge-test" in registry
        assert "forge-auth" in registry

    def test_ignores_dirs_without_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "not_a_module").mkdir()
        registry = build_registry(tmp_path)
        assert "not-a-module" not in registry


# ===========================================================================
# urls_manager — find_main_urls / wire_url_include
# ===========================================================================


def _make_project(tmp_path: Path) -> Path:
    """Projet Django factice minimal : manage.py + myproject/{settings,urls}.py."""
    (tmp_path / "manage.py").write_text("# manage.py")
    pkg = tmp_path / "myproject"
    pkg.mkdir()
    (pkg / "settings.py").write_text(
        'ROOT_URLCONF = "myproject.urls"\nDEBUG = True\n', encoding="utf-8"
    )
    (pkg / "urls.py").write_text(
        "from django.urls import path\n\nurlpatterns = [\n]\n", encoding="utf-8"
    )
    return tmp_path


class TestFindMainUrls:
    def test_finds_urls_via_root_urlconf(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        result = find_main_urls(project)
        assert result == project / "myproject" / "urls.py"

    def test_returns_none_when_settings_absent(self, tmp_path: Path) -> None:
        assert find_main_urls(tmp_path) is None

    def test_returns_none_without_root_urlconf(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("# manage.py")
        pkg = tmp_path / "myproject"
        pkg.mkdir()
        (pkg / "settings.py").write_text("DEBUG = True\n", encoding="utf-8")
        (pkg / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")

        assert find_main_urls(tmp_path) is None

    def test_ignores_decoy_app_urls_with_urlpatterns(self, tmp_path: Path) -> None:
        """
        Régression : un urls.py d'app locale (ou de module installé, ex.
        forge_auth) contient déjà "urlpatterns" — une recherche par contenu
        le confondrait avec le urls.py principal, provoquant une
        auto-inclusion et une boucle infinie.
        """
        project = _make_project(tmp_path)
        decoy = project / "blog"
        decoy.mkdir()
        (decoy / "urls.py").write_text(
            'app_name = "blog"\nurlpatterns: list = []\n', encoding="utf-8"
        )

        result = find_main_urls(project)

        assert result == project / "myproject" / "urls.py"


class TestWireUrlInclude:
    def test_injects_include(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        modified = wire_url_include(
            project, url_prefix="blog/", include_target="blog.urls", namespace="blog"
        )
        assert modified is True
        content = (project / "myproject" / "urls.py").read_text()
        assert 'include("blog.urls", namespace="blog")' in content
        assert "from django.urls import include, path" in content

    def test_idempotent(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        wire_url_include(project, url_prefix="blog/", include_target="blog.urls", namespace="blog")
        second = wire_url_include(
            project, url_prefix="blog/", include_target="blog.urls", namespace="blog"
        )
        assert second is False
        content = (project / "myproject" / "urls.py").read_text()
        assert content.count("blog.urls") == 1

    def test_empty_prefix_mounts_at_root(self, tmp_path: Path) -> None:
        """forge_auth branche ses propres URLs sans préfixe supplémentaire,
        son urls.py définissant déjà le segment "forge_auth/"."""
        project = _make_project(tmp_path)
        modified = wire_url_include(
            project, url_prefix="", include_target="forge_auth.urls", namespace="forge_auth"
        )
        assert modified is True
        content = (project / "myproject" / "urls.py").read_text()
        assert 'path("", include("forge_auth.urls", namespace="forge_auth"))' in content

    def test_returns_false_when_main_urls_missing(self, tmp_path: Path) -> None:
        assert wire_url_include(tmp_path, "x/", "x.urls", "x") is False