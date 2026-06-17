from django.test import Client
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken


def login_user_for_test(user: User) -> Client:
    client = Client()
    token = RefreshToken.for_user(user)
    access = str(token.access_token)
    refresh = str(token)

    # Header Authorization (JWTAuthentication standard)
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {access}"

    # Cookie HttpOnly (JWTCookieAuthentication)
    client.cookies["access"] = access
    client.cookies["access"]["httponly"] = True
    client.cookies["access"]["secure"] = True
    client.cookies["access"]["samesite"] = "None"

    client.cookies["refresh"] = refresh
    client.cookies["refresh"]["httponly"] = True
    client.cookies["refresh"]["secure"] = True
    client.cookies["refresh"]["samesite"] = "None"

    return client