from django.contrib import admin
from django.urls import path, include

from .welcome import forge_welcome

urlpatterns = [
    # Page d'accueil d'onboarding Django Forge (affichée en DEBUG uniquement).
    # Remplacez cette route par votre propre vue quand vous construisez votre app.
    path("", forge_welcome, name="forge-welcome"),
    path("admin/", admin.site.urls),
]
