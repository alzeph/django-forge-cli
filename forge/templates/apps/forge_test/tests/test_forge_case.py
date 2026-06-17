"""
Tests de forge_case.py exécutables SANS environnement Django.

Cible : les fonctions pures module-level et la logique d'assertion
de ForgeCase, mockée pour ne jamais toucher Django.

Run : python -m pytest test_forge_case.py -v
"""
from __future__ import annotations
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import FileExtensionValidator

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import json
import django
from django.conf import settings


if not settings.configured:
    settings.configure(
        SECRET_KEY="secret-key-pour-les-tests",
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
        ],
        ROOT_URLCONF=__name__,  # Permet de définir des URLs directement ici si besoin
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    )
    django.setup()

import unittest
# Import cible — maintenant sans dépendances réelles
from forge_test.public.helpers import (  # noqa: E402
    ForgeCase,
)
from forge_test.public.helpers.forge_case import (
    _append_query_string,
    _parse_response_json,
    _resolve_nested_field,
    _safe_response_body,
)


def _make_response(json_data: Any = None, status: int = 200, content: bytes = b"") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.content = content
    if json_data is not None:
        r.json.return_value = json_data
    else:
        r.json.side_effect = ValueError("no JSON")
    return r


def _make_forge_case() -> ForgeCase:
    """Instancie ForgeCase sans setUp Django."""
    instance = ForgeCase.__new__(ForgeCase)
    instance.assertEqual = unittest.TestCase("__init__").__class__.assertEqual.__get__(
        unittest.TestCase(), unittest.TestCase
    )
    instance.assertIsNone = unittest.TestCase().assertIsNone
    instance.assertIsNotNone = unittest.TestCase().assertIsNotNone
    instance.assertIsInstance = unittest.TestCase().assertIsInstance
    instance.assertEqual = unittest.TestCase().assertEqual
    return instance


class TestResolveNestedField(unittest.TestCase):

    def test_flat_field_found(self) -> None:
        self.assertEqual(_resolve_nested_field({"id": 1}, "id"), 1)

    def test_flat_field_missing_returns_none(self) -> None:
        self.assertIsNone(_resolve_nested_field({"id": 1}, "name"))

    def test_nested_two_levels(self) -> None:
        data = {"user": {"profile": {"age": 30}}}
        self.assertEqual(_resolve_nested_field(data, "user.profile.age"), 30)

    def test_nested_partial_path_missing(self) -> None:
        data = {"user": {"name": "Alice"}}
        self.assertIsNone(_resolve_nested_field(data, "user.profile.age"))

    def test_nested_intermediate_not_dict(self) -> None:
        data = {"user": "string"}
        self.assertIsNone(_resolve_nested_field(data, "user.name"))

    def test_value_none_is_returned_as_none(self) -> None:
        # None est une valeur valide dans le dict
        data = {"key": None}
        self.assertIsNone(_resolve_nested_field(data, "key"))

    def test_empty_data(self) -> None:
        self.assertIsNone(_resolve_nested_field({}, "anything"))

    def test_nested_returns_dict(self) -> None:
        data = {"a": {"b": {"c": 42}}}
        self.assertEqual(_resolve_nested_field(data, "a.b"), {"c": 42})


class TestParseResponseJson(unittest.TestCase):

    def test_valid_json_response(self) -> None:
        r = _make_response(json_data={"id": 1})
        self.assertEqual(_parse_response_json(r), {"id": 1})

    def test_invalid_json_returns_none(self) -> None:
        r = _make_response(json_data=None)
        self.assertIsNone(_parse_response_json(r))

    def test_any_exception_returns_none(self) -> None:
        r = MagicMock()
        r.json.side_effect = RuntimeError("boom")
        self.assertIsNone(_parse_response_json(r))


class TestSafeResponseBody(unittest.TestCase):

    def test_returns_json_string_when_available(self) -> None:
        r = _make_response(json_data={"error": "bad"})
        self.assertIn("bad", _safe_response_body(r))

    def test_falls_back_to_content(self) -> None:
        r = _make_response(json_data=None, content=b"raw error")
        self.assertIn("raw error", _safe_response_body(r))

    def test_truncates_long_content(self) -> None:
        r = _make_response(json_data=None, content=b"x" * 500)
        self.assertLessEqual(len(_safe_response_body(r)), 300)


class TestAppendQueryString(unittest.TestCase):

    def test_dict_query(self) -> None:
        url = _append_query_string("/api/", {"page": 2, "size": 10})
        self.assertIn("page=2", url)
        self.assertIn("size=10", url)

    def test_string_query(self) -> None:
        url = _append_query_string("/api/", "page=1")
        self.assertEqual(url, "/api/?page=1")

    def test_empty_dict(self) -> None:
        url = _append_query_string("/api/", {})
        self.assertEqual(url, "/api/?")


class TestForgeCaseAssertions(unittest.TestCase):
    """
    On instancie ForgeCase directement et on réutilise
    les méthodes unittest.TestCase via héritage indirect.
    """

    def _case(self) -> ForgeCase:
        return _make_forge_case()

    # _assert_status_code

    def test_assert_status_code_passes(self) -> None:
        case = self._case()
        r = _make_response(status=200)
        case._assert_status_code(r, 200)  # ne doit pas lever

    def test_assert_status_code_fails(self) -> None:
        case = self._case()
        r = _make_response(status=404)
        with self.assertRaises(AssertionError):
            case._assert_status_code(r, 200)

    # _assert_fields_present

    def test_assert_fields_present_passes(self) -> None:
        case = self._case()
        case._assert_fields_present({"id": 1, "name": "Alice"}, ["id", "name"])

    def test_assert_fields_present_fails_on_missing(self) -> None:
        case = self._case()
        with self.assertRaises(AssertionError):
            case._assert_fields_present({"id": 1}, ["id", "email"])

    def test_assert_fields_present_nested(self) -> None:
        case = self._case()
        data = {"user": {"profile": {"age": 25}}}
        case._assert_fields_present(data, ["user.profile.age"])

    def test_assert_fields_present_nested_missing(self) -> None:
        case = self._case()
        data = {"user": {"name": "Bob"}}
        with self.assertRaises(AssertionError):
            case._assert_fields_present(data, ["user.profile.age"])

    # _assert_field_values

    def test_assert_field_values_passes(self) -> None:
        case = self._case()
        case._assert_field_values({"status": "active"}, {"status": "active"})

    def test_assert_field_values_fails(self) -> None:
        case = self._case()
        with self.assertRaises(AssertionError):
            case._assert_field_values({"status": "inactive"}, {"status": "active"})

    def test_assert_field_values_nested(self) -> None:
        case = self._case()
        data = {"user": {"role": "admin"}}
        case._assert_field_values(data, {"user.role": "admin"})

    # _assert_field_types

    def test_assert_field_types_passes(self) -> None:
        case = self._case()
        case._assert_field_types({"id": 1, "name": "Alice"}, {"id": int, "name": str})

    def test_assert_field_types_fails(self) -> None:
        case = self._case()
        with self.assertRaises(AssertionError):
            case._assert_field_types({"id": "not-an-int"}, {"id": int})

    # _assert_fields_absent

    def test_assert_fields_absent_passes(self) -> None:
        case = self._case()
        case._assert_fields_absent({"id": 1}, ["password", "secret"])

    def test_assert_fields_absent_fails_when_present(self) -> None:
        case = self._case()
        with self.assertRaises(AssertionError):
            case._assert_fields_absent({"id": 1, "password": "hash"}, ["password"])

    # _assert_response_body — intégration des assertions

    def test_assert_response_body_full(self) -> None:
        case = self._case()
        r = _make_response(json_data={"id": 1, "name": "Alice", "role": "admin"})
        params: dict = {
            "expected_fields": ["id", "name"],
            "expected_value_of_fields": {"role": "admin"},
            "expected_type_of_fields": {"id": int},
            "forbidden_fields": ["password"],
        }
        case._assert_response_body(r, params)  # ne doit pas lever

    def test_assert_response_body_skips_on_no_json(self) -> None:
        case = self._case()
        r = _make_response(json_data=None)
        case._assert_response_body(r, {"expected_fields": ["id"]})  # ne doit pas lever


class TestAttachAllTests(unittest.TestCase):

    def test_no_config_does_not_crash(self) -> None:
        """Une sous-classe sans config ne doit pas crasher."""
        class Sub(ForgeCase):
            config = {}
        test_methods = [m for m in dir(Sub) if m.startswith("test_")]
        self.assertEqual(test_methods, [])

    def test_tests_are_attached_per_status_code(self) -> None:
        """Chaque (test, status_code) génère un test distinct."""
        class Sub(ForgeCase):
            config = {
                "factory_params": {},
                "tests": [
                    {
                        "path_name": "my:endpoint",
                        "method": "GET",
                        "expected_responses": {
                            200: {"authenticated": True},
                            401: {"authenticated": False},
                        },
                    },
                    {
                        "path_name": "my:endpoint",
                        "method": "POST",
                        "expected_responses": {
                            201: {"authenticated": True},
                        },
                    },
                ],
            }
        attached = [m for m in dir(Sub) if m.startswith("test_")]
        self.assertIn("test_get_my_endpoint_success", attached)
        self.assertIn("test_get_my_endpoint_not_authenticated", attached)
        self.assertIn("test_post_my_endpoint_created", attached)
        self.assertEqual(len(attached), 3)

    def test_test_name_uses_test_name_field(self) -> None:
        """Si test_name est défini, il est utilisé comme base."""
        class Sub(ForgeCase):
            config = {
                "factory_params": {},
                "tests": [
                    {
                        "test_name": "users_list",
                        "path_name": "api:users-list",
                        "method": "GET",
                        "expected_responses": {
                            200: {"authenticated": True},
                            401: {"authenticated": False},
                        },
                    }
                ],
            }
        attached = [m for m in dir(Sub) if m.startswith("test_")]
        self.assertIn("test_get_users_list_success", attached)
        self.assertIn("test_get_users_list_not_authenticated", attached)

    def test_test_name_sanitizes_colons_and_dashes(self) -> None:
        class Sub(ForgeCase):
            config = {
                "factory_params": {},
                "tests": [
                    {
                        "path_name": "api:resource-list",
                        "method": "DELETE",
                        "expected_responses": {
                            204: {"authenticated": True},
                        },
                    }
                ],
            }
        attached = [m for m in dir(Sub) if m.startswith("test_delete")]
        self.assertEqual(len(attached), 1)
        self.assertNotIn(":", attached[0])
        self.assertNotIn("-", attached[0])

    def test_assert_response_is_removed(self) -> None:
        """_assert_response n'est plus utilisé — s'assurer qu'il n'existe pas ou est ignoré."""
        # La logique est maintenant inline dans _build_single_test
        # On vérifie juste que la génération de tests fonctionne sans lui
        class Sub(ForgeCase):
            config = {
                "factory_params": {},
                "tests": [
                    {
                        "path_name": "x:y",
                        "method": "GET",
                        "expected_responses": {500: {}},
                    }
                ],
            }
        attached = [m for m in dir(Sub) if m.startswith("test_")]
        self.assertIn("test_get_x_y_server_error", attached)

if __name__ == "__main__":
    unittest.main(verbosity=2)