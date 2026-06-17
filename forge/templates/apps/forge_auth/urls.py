from django.urls import path, include
from rest_framework import routers
from forge_auth import views

router = routers.DefaultRouter()
router.register(r'groups', views.GroupViewSet, basename='groups')
router.register(r'users', views.UserViewSet, basename='users')

app_name = 'forge_auth'
urlpatterns = [
    path('forge_auth/', include(router.urls)),
]