"""
forge/commands/configure.py
============================
Logique métier de `forge configure <service>`.

Responsabilité
--------------
Injecter dans `settings.py` la configuration d'un service tiers.
Chaque service est implémenté comme une fonction `_configure_<service>`
enregistrée dans le registre `_SERVICE_REGISTRY`.

Pour ajouter un nouveau service :
    1. Écrire `_configure_<service>(settings_path, options)` ci-dessous.
    2. L'enregistrer dans `_SERVICE_REGISTRY`.
    C'est tout — aucune autre modification n'est requise.

Option `--dev` : génère une configuration conditionnelle `if DEBUG`
pour segmenter automatiquement les environnements dev/prod.
Option `--postgis` : active PostGIS pour le service `pgsql`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import typer

from forge.commands._options import ConfigureOptions
from forge.core.config_manager import append_raw_block, replace_or_append_raw_block, setting_exists

# ---------------------------------------------------------------------------
# Type des handlers de service
# ---------------------------------------------------------------------------

ServiceHandler = Callable[[Path, ConfigureOptions], None]

# ---------------------------------------------------------------------------
# Handlers par service
# ---------------------------------------------------------------------------


def _configure_redis(settings_path: Path, options: ConfigureOptions) -> None:
    block = """
# forge: redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
"""
    append_raw_block(settings_path, block, guard_comment="# forge: redis")
    typer.echo("  • Redis configuré.")


def _configure_celery(settings_path: Path, options: ConfigureOptions) -> None:
    block = """
# forge: celery
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
"""
    append_raw_block(settings_path, block, guard_comment="# forge: celery")
    _generate_celery_app_file(settings_path)
    typer.echo("  • Celery configuré (settings.py + celery.py).")


def _generate_celery_app_file(settings_path: Path) -> None:
    """
    Génère `celery.py` au même niveau que `settings.py`.

    Ce fichier instancie l'app Celery, la lie aux settings Django via le
    namespace `CELERY_` et active l'autodiscovery des tâches.

    Le nom du projet est déduit du `DJANGO_SETTINGS_MODULE` que Django
    inscrit automatiquement, ou directement depuis le nom du dossier parent
    de `settings.py` (source de vérité fiable sans importer Django).
    """
    project_package = settings_path.parent.name  # ex: "myproject"
    celery_path = settings_path.parent / "celery.py"

    if celery_path.exists():
        typer.echo("  • celery.py déjà présent — ignoré.")
        return

    content = f'''\
"""
Celery application entry point.

Ce fichier est généré par `forge configure celery`.
Il doit être importé dans `{project_package}/__init__.py` pour que les
signaux Django et les tâches soient découverts au démarrage.

Dans `{project_package}/__init__.py`, ajoutez :

    from .celery import app as celery_app
    __all__ = ("celery_app",)
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{project_package}.settings")

app = Celery("{project_package}")

# Charge la configuration depuis django.conf.settings en filtrant
# les clés préfixées CELERY_ (namespace).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Découvre automatiquement les modules tasks.py dans chaque app INSTALLED_APPS.
app.autodiscover_tasks()
'''
    celery_path.write_text(content, encoding="utf-8")
    typer.echo(f"  • {project_package}/celery.py généré.")


def _configure_drf(settings_path: Path, options: ConfigureOptions) -> None:
    """
    Configure Django REST Framework + drf-spectacular.

    Détecte si `forge_auth` est installé (présent dans INSTALLED_APPS du
    fichier settings courant) pour injecter la classe d'authentification JWT
    flexible de Forge à la place des classes DRF par défaut.
    """
    forge_auth_active = _is_forge_auth_installed(settings_path)

    if forge_auth_active:
        auth_classes = '        "forge_auth.authentification.JWTAuthenticationFlexible",'
    else:
        auth_classes = (
            '        "rest_framework.authentication.SessionAuthentication",\n'
            '        "rest_framework.authentication.BasicAuthentication",'
        )

    block = f"""
# forge: drf
REST_FRAMEWORK = {{
    "DEFAULT_AUTHENTICATION_CLASSES": (
{auth_classes}
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}}

SPECTACULAR_SETTINGS = {{
    "TITLE": "API",
    "DESCRIPTION": "API documentation",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "POSTPROCESSING_HOOKS": [],
    "SCHEMA_PATH_PREFIX_TRIM": False,
    "COMPONENT_NO_READ_ONLY_REQUIRED": False,
    "SECURITY": [{{"jwtFlexibleAuth": []}}],
    "APPEND_COMPONENTS": {{
        "securitySchemes": {{
            "jwtFlexibleAuth": {{
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT via cookie 'access' ou header 'Authorization: Bearer <token>'",
            }}
        }}
    }},
}}
"""
    append_raw_block(settings_path, block, guard_comment="# forge: drf")

    if forge_auth_active:
        typer.echo("  • DRF configuré avec forge_auth.JWTAuthenticationFlexible.")
    else:
        typer.echo("  • DRF configuré (auth DRF par défaut — installez forge-auth pour JWT).")
    typer.echo("  • drf-spectacular configuré.")


def _is_forge_auth_installed(settings_path: Path) -> bool:
    """
    Retourne True si `forge_auth` est présent dans le fichier settings.

    Utilise une recherche textuelle simple — suffisant car on cherche une
    chaîne litérale dans INSTALLED_APPS, sans avoir besoin d'importer Django.
    """
    return "forge_auth" in settings_path.read_text(encoding="utf-8")


def _configure_channels(settings_path: Path, options: ConfigureOptions) -> None:
    block = """
# forge: channels
ASGI_APPLICATION = "{project_name}.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("REDIS_URL", "redis://localhost:6379/0")],
        },
    }
}
"""
    append_raw_block(settings_path, block, guard_comment="# forge: channels")
    typer.echo("  • Django Channels configuré.")


def _configure_pgsql(settings_path: Path, options: ConfigureOptions) -> None:
    engine = (
        "django.contrib.gis.db.backends.postgis"
        if options.postgis
        else "django.db.backends.postgresql"
    )

    # Supprimer le bloc DATABASES sqlite généré par django-admin s'il existe
    _remove_sqlite_databases(settings_path)

    if options.dev:
        dev_service = options.dev
        block = f"""
# forge: pgsql
if DEBUG:
    DATABASES = {{
        "default": {{
            "ENGINE": "django.db.backends.{dev_service}",
            "NAME": BASE_DIR / "db.{dev_service}3",
        }}
    }}
else:
    DATABASES = {{
        "default": {{
            "ENGINE": "{engine}",
            "NAME": os.getenv("DB_NAME"),
            "USER": os.getenv("DB_USER"),
            "PASSWORD": os.getenv("DB_PASSWORD"),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }}
    }}
"""
    else:
        block = f"""
# forge: pgsql
DATABASES = {{
    "default": {{
        "ENGINE": "{engine}",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }}
}}
"""
    replace_or_append_raw_block(settings_path, block, guard_comment="# forge: pgsql")
    label = "PostgreSQL + PostGIS" if options.postgis else "PostgreSQL"
    typer.echo(f"  • {label} configuré.")


def _configure_mysql(settings_path: Path, options: ConfigureOptions) -> None:
    _remove_sqlite_databases(settings_path)

    if options.dev:
        dev_service = options.dev
        block = f"""
# forge: mysql
if DEBUG:
    DATABASES = {{
        "default": {{
            "ENGINE": "django.db.backends.{dev_service}",
            "NAME": BASE_DIR / "db.{dev_service}3",
        }}
    }}
else:
    DATABASES = {{
        "default": {{
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": env("DB_HOST", default="localhost"),
            "PORT": env("DB_PORT", default="3306"),
        }}
    }}
"""
    else:
        block = """
# forge: mysql
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="3306"),
    }
}
"""
    replace_or_append_raw_block(settings_path, block, guard_comment="# forge: mysql")
    typer.echo("  • MySQL configuré.")


def _remove_sqlite_databases(settings_path: Path) -> None:
    """
    Supprime le bloc `DATABASES` sqlite généré par django-admin si présent
    et non encore remplacé par Forge.

    Cherche le pattern exact que Django génère :
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
    """
    source = settings_path.read_text(encoding="utf-8")

    # Déjà remplacé par Forge
    if "# forge: pgsql" in source or "# forge: mysql" in source:
        return

    if "sqlite3" not in source:
        return

    # Trouver le début du bloc DATABASES sqlite
    import re
    pattern = re.compile(
        r"\n# Database.*?\nDATABASES\s*=\s*\{[^}]*sqlite3[^}]*\}[^}]*\}",
        re.DOTALL,
    )
    new_source = pattern.sub("", source)
    if new_source != source:
        settings_path.write_text(new_source, encoding="utf-8")


# ---------------------------------------------------------------------------
# Registre des services
# ---------------------------------------------------------------------------

_SERVICE_REGISTRY: dict[str, ServiceHandler] = {
    "redis": _configure_redis,
    "celery": _configure_celery,
    "drf": _configure_drf,
    "channels": _configure_channels,
    "pgsql": _configure_pgsql,
    "mysql": _configure_mysql,
}

AVAILABLE_SERVICES = sorted(_SERVICE_REGISTRY)


# ---------------------------------------------------------------------------
# Point d'entrée de la commande
# ---------------------------------------------------------------------------


def run(
    service: str,
    options: ConfigureOptions,
    project_root: Path | None = None,
) -> None:
    """
    Exécute `forge configure`.

    Parameters
    ----------
    service:
        Identifiant du service à configurer. Doit être une clé de
        :data:`_SERVICE_REGISTRY`.
    options:
        Options de la commande (voir :class:`~forge.commands._options.ConfigureOptions`).
    project_root:
        Racine du projet. Déduit depuis `manage.py` si `None`.
    """
    from forge.core.engine import find_manage_py

    root = project_root or find_manage_py().parent

    if service not in _SERVICE_REGISTRY:
        typer.echo(
            f"✗ Service '{service}' inconnu.\n"
            f"  Services disponibles : {', '.join(AVAILABLE_SERVICES)}",
            err=True,
        )
        raise typer.Exit(code=1)

    settings_path = _find_settings(root)

    typer.echo(f"→ Configuration de '{service}'...")
    _SERVICE_REGISTRY[service](settings_path, options)
    typer.echo(f"✓ '{service}' configuré avec succès.")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _find_settings(project_root: Path) -> Path:
    candidates = [
        p for p in project_root.rglob("settings.py")
        if "test" not in p.parts and "migrations" not in p.parts
    ]
    if not candidates:
        raise FileNotFoundError(f"settings.py introuvable dans {project_root}")
    return candidates[0]