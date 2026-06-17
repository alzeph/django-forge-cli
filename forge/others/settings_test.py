"""
Settings Django minimal utilisé exclusivement par pytest dans le repo
django-forge-cli. Ne fait pas partie du package distribué.
"""

SECRET_KEY = "test-secret-key-not-for-production"
DEBUG = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"