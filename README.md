Voici la version complétée du `README.md`. Elle intègre désormais les sections techniques relatives à l'arborescence des fichiers, à la création de commandes/options, ainsi qu'à la modification du manifeste pour les contributeurs.

---

````markdown
# Django Forge

Django Forge est un méta-framework construit au-dessus de Django. Il a pour objectif de réduire le code répétitif (boilerplate) lors de l'initialisation et du développement d'un projet en automatisant les configurations complexes et en intégrant un écosystème d'applications réutilisables.

---

## Fonctionnalités principales

- **CLI Unique** : Remplace l'utilisation directe de `django-admin` et `manage.py`.
- **Auto-configuration** : Injection automatique des applications créées dans `INSTALLED_APPS` et routage automatique des URLs.
- **Gestionnaire de dépendances récursif** : Installation en cascade des modules internes et de leurs configurations requises.
- **Configuration de services en une commande** : Intégration simplifiée de bases de données et d'outils asynchrones (Celery, Redis, Channels).

---

## Modules disponibles

Chaque module est une bibliothèque publiée sur PyPI : `forge install <module>` l'installe depuis PyPI puis applique la **configuration minimale** dans `settings.py` / `urls.py` (aucun code n'est copié dans votre projet).

| Module | Paquet PyPI | Configuration appliquée | Python |
|---|---|---|---|
| `forge-auth` | `django-forge-auth` | `INSTALLED_APPS` (+ `token_blacklist`), `AUTH_USER_MODEL`, `FORGE_AUTH = {}`, route `api/` ; configure aussi DRF. Installe `forge-test`. | 3.12+ |
| `forge-test` | `django-forge-test` | aucune (helpers de test : `ForgeCase`, `ForgeModelFactory`) | 3.12+ |
| `forge-log` | `django-forge-log` | `INSTALLED_APPS` + `RequestContextMiddleware` (audit trail) | 3.12+ |
| `event-bus` | `django-event-bus` | `INSTALLED_APPS` + `EVENT_BUS = {"SERVICE_NAME": <projet>}` (broker en mémoire par défaut) | **3.13+** |
| `goroutine` | `django-goroutine` | `INSTALLED_APPS` ; installe `pycatch` | **3.13+** |
| `pycatch` | `pycatch-safe` | aucune (gestion d'erreurs `Result` : `Ok` / `Err` / `@catch`) | 3.12+ |

Les versions sont des bornes minimales (`>=`) ; une borne de pré-version (`rc`) autorise cette pré-version. Si l'interpréteur est trop ancien pour la chaîne demandée, `forge install` s'arrête **avant** de modifier le projet.

La CLI elle-même utilise `pycatch` pour retourner des `Result` plutôt que de lever des exceptions (résolution de dépendances, lancement de `pip`/`uv`, passe-plat Django).

---

## Structure du Projet (Arborescence des fichiers)

Pour garantir la maintenabilité et l'extensibilité du framework, le code source de la CLI respecte l'architecture standard suivante :

```text
django-forge-cli/
├── forge/                         # Package Python principal de la CLI
│   ├── __init__.py
│   ├── main.py                    # Point d'entrée Typer (Routage et mode passe-plat)
│   │
│   ├── core/                      # Logique métier et moteurs d'automatisation
│   │   ├── __init__.py
│   │   ├── engine.py              # Gestionnaire d'exécution des commandes Django
│   │   ├── dependency_resolver.py # Algorithme de résolution récursive des modules
│   │   └── config_manager.py      # Analyseur de syntaxe AST (LibCST) pour settings.py
│   │
│   ├── commands/                  # Modules de définition des commandes CLI
│   │   ├── __init__.py
│   │   ├── init.py                # Logique de 'forge init'
│   │   ├── add.py                 # Logique de 'forge add'
│   │   ├── install.py             # Logique de 'forge install'
│   │   └── configure.py           # Logique de 'forge configure'
│   │
│   ├── templates/                 # Blueprints et manifestes embarqués
│   │   ├── __init__.py
│   │   ├── project_base/          # Squelette de projet initial pour 'forge init'
│   │   └── apps/                  # Applications réutilisables et manifestes associés
│   │       ├── forge_auth/        # Un dossier par module ; manifest.json uniquement
│   │       │   └── manifest.json  # Paquet PyPI, config minimale, dépendances
│   │       ├── forge_test/
│   │       ├── forge_log/
│   │       ├── event_bus/
│   │       ├── goroutine/
│   │       └── pycatch/
│   │
│   ├── others/
│   │   └── settings_test.py       # Settings Django minimal pour la suite pytest
│   │
│   └── tests/                     # Tests de la CLI (moteur)
│       ├── test_command.py        # init, add, configure, install
│       └── test_core.py           # config_manager, dependency_resolver, engine
├── pyproject.toml                 # Métadonnées du package et déclaration du script 'forge'
└── README.md
```
````

---

## Installation

Pour installer Django Forge au sein de votre environnement virtuel actif :

```bash
uv add django-forge-cli

```

Le script génère automatiquement l'exécutable `forge` dès que l'environnement virtuel est activé, grâce à la section suivante du `pyproject.toml` :

```toml
[project.scripts]
forge = "forge.main:app"

```

---

## Référence de la CLI

### 1. Création de projet

Initialise une nouvelle structure de projet Django Forge.

```bash
forge init <project_name>

```

#### Options :

- `--install=forge-auth,forge-notification` : Installe et configure une liste d'applications réutilisables dès l'initialisation.
- `--template=<blueprint>` : Applique un **blueprint de projet** — un preset qui scaffolde un projet complet et pré-configuré en une commande. Voir ci-dessous.

### 1 bis. Blueprints de projet (`--template`)

Un blueprint enchaîne automatiquement la création du projet, des apps, l'installation des modules et la configuration des services, dans un ordre déterministe.

```bash
forge templates                       # liste les blueprints disponibles
forge init monshop --template saas    # scaffold complet en une commande
```

Blueprints fournis :

| Blueprint | Contenu |
|-----------|---------|
| `api`  | Django REST Framework + PostgreSQL (SQLite en dev) + app `core`. |
| `saas` | `forge-auth` (JWT) + DRF + PostgreSQL (SQLite en dev) + Celery + Redis + app `core`. |

**Ajouter son propre blueprint** — dépose un dossier `forge/templates/blueprints/<nom>/blueprint.json` :

```json
{
  "name": "saas",
  "description": "SaaS prêt à coder : auth JWT, DRF, PostgreSQL, Celery + Redis.",
  "apps": ["core"],
  "install": ["forge-auth"],
  "configure": [
    { "service": "drf" },
    { "service": "pgsql", "dev": "sqlite" },
    { "service": "celery" },
    { "service": "redis" }
  ]
}
```

- `apps` → un `forge add` par entrée ;
- `install` → un `forge install` par module (dépendances résolues récursivement) ;
- `configure` → un `forge configure` par service (`postgis` et `dev` optionnels).

Aucun code à écrire : le fichier `blueprint.json` suffit à enregistrer un nouveau template.

### 2. Création d'application

Génère une application locale auto-configurée.

```bash
forge add <app_name>

```

#### Options :

- `--no-urls` : Désactive la création automatique et le branchement du fichier `urls.py`.
- `--templates` : Crée l'arborescence standard `app_name/templates/app_name/`.
- `--templates=index.html,detail.html` : Crée l'arborescence, génère les fichiers HTML spécifiés (séparés par des virgules) ainsi qu'une vue et une route par page dans `views.py`/`urls.py` — chaque page est accessible immédiatement (ex : `/app_name/index/`), sans code à écrire. Chaque page générée étend `templates/base.html` (créé à la racine du projet par `forge init`). Sans effet combiné à `--no-urls` (pas de routeur local à brancher).

### 3. Installation de modules réutilisables

Installe un module officiel ainsi que son arbre de dépendances de manière récursive.

```bash
forge install <app_name>

```

### 4. Configuration des services

Configure l'infrastructure du projet et injecte les paramètres requis dans `settings.py`.

```bash
forge configure <service>

```

- Services disponibles : `redis`, `celery`, `drf`, `channels`, `pgsql`, `mysql`.

#### Options :

- `--postgis` : À utiliser avec `pgsql` pour activer le support géospatial.
- `--dev=<service>` : Permet de segmenter l'environnement (ex: `forge configure mysql --dev=sqlite`).

### 5. Commandes de l'écosystème Django (Mode Passe-plat)

Toutes les commandes non spécifiques à Forge sont interceptées et redirigées directement vers le gestionnaire natif de Django.

```bash
forge makemigrations
forge migrate
forge runserver

```

---

## Guide du Développeur : Étendre la CLI

### 1. Ajouter une nouvelle commande ou une option

Les commandes utilisent la bibliothèque **Typer**. Pour ajouter une commande ou enrichir une commande existante avec des options, modifiez le fichier correspondant dans le sous-dossier `forge/commands/`.

Exemple d'implémentation pour ajouter une option dans `forge/commands/add.py` :

```python
import typer
from typing import Optional

def add_command(
    app_name: str = typer.Argument(..., help="Nom de l'application à créer"),
    no_urls: bool = typer.Option(False, "--no-urls", help="Désactiver la création de urls.py"),
    templates: Optional[str] = typer.Option(None, help="Fichiers templates HTML séparés par des virgules")
):
    """
    Logique d'exécution de la commande 'forge add'
    """
    # Étape 1 : Appel à django-admin startapp
    # Étape 2 : Analyse des options conditionnelles
    if not no_urls:
        # Générer le fichier urls.py local et l'injecter dans le urls.py principal
        pass

    if templates is not None:
        # Traiter la chaîne (ex: "index.html,detail.html") et générer l'arborescence
        files = [f.strip() for f in templates.split(",") if f.strip()]
        pass

```

Chaque sous-module de commande est ensuite enregistré sur l'application principale dans `forge/main.py` :

```python
import typer
from forge.commands.add import add_command

app = typer.Typer()
app.command(name="add")(add_command)

```

### 2. Modification et structure du Manifeste d'application

Chaque application présente dans `forge/templates/apps/` doit impérativement posséder un fichier `manifest.json` à sa racine. Ce fichier pilote l'arbre de dépendances récursif exécuté par le moteur de `forge install`.

#### Structure du fichier `manifest.json` :

> ℹ️ L'exemple ci-dessous (`forge-notification`) est **illustratif** pour montrer la structure d'un manifeste. Ce module n'est pas fourni en standard : voir la table « Modules disponibles » pour les modules livrés.

```json
{
  "name": "forge-notification",
  "version": "1.0.0",
  "dependencies": ["forge-auth"],
  "configure": ["redis"],
  "env_required": ["NOTIFICATION_API_KEY"],
  "requires_python": "3.13",
  "python_packages": ["django-forge-notification>=1.0.0"],
  "installed_apps": ["forge_notification"],
  "middleware": ["forge_notification.middleware.NotificationMiddleware"],
  "settings": {"NOTIFICATION": {"SERVICE_NAME": "{project_name}"}},
  "urls": {"prefix": "api/", "include": "forge_notification.urls"}
}
```

| Clé | Rôle |
|---|---|
| `dependencies` | Autres modules Forge à installer d'abord |
| `configure` | Services à configurer (`forge configure <service>`) |
| `env_required` | Clés ajoutées (vides) dans `.env` si absentes |
| `python_packages` | Spécifications PyPI à installer |
| `installed_apps` / `middleware` | Ajoutés à `INSTALLED_APPS` / `MIDDLEWARE` |
| `settings` | Réglages posés dans `settings.py` s'ils sont absents (`{project_name}` est substitué) |
| `urls` | `include` branché dans le `urls.py` principal |
| `requires_python` | Version minimale de Python (`"3.13"`) |

#### Cycle de traitement du manifeste lors d'un `forge install` :

1. **Résolution des dépendances** : La CLI lit le tableau `"dependencies"`. Si une dépendance n'est pas installée, elle suspend l'installation en cours pour exécuter récursivement l'installation de la dépendance (dans l'exemple ci-dessus, `forge-auth` sera traité en premier, ce qui déploiera également `forge-test`).
2. **Exécution des configurations** : Le tableau `"configure"` appelle automatiquement les scripts du moteur de configuration associés (ici, l'équivalent de `forge configure redis`).
3. **Contrôle de l'environnement** : Les clés listées dans `"env_required"` sont vérifiées ou ajoutées comme structures vides dans le fichier `.env` du projet hôte pour alerter le développeur.
4. **Configuration minimale** : `"installed_apps"`, `"middleware"`, `"settings"` et `"urls"` sont appliqués de façon idempotente à `settings.py` et au `urls.py` principal.
5. **Installation des paquets Python** : Les spécifications listées dans `"python_packages"` sont installées depuis PyPI dans l'environnement courant (celui où tourne `forge`) via `uv pip install` (repli : `pip install`). Si `"requires_python"` n'est pas satisfait, l'installation s'arrête avant toute modification.

```

```
