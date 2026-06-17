from __future__ import annotations

from typing import Any, Dict, Optional, Union, List

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from forge_test.public.helpers import ForgeModelFactory
from forge_test.public.helpers.login_user_for_test import login_user_for_test
from forge_test.public.type import (
    ConfigForgeCase,
    Fixture,
    FixtureJson,
    HTTPClientParams,
    ResponseValidationParams,
    TestCaseConfig,
)

User = get_user_model()

print()

class ForgeCase(TestCase):
    """
    Classe de base pour les tests d'endpoints auto-générés.

    Chaque status code attendu dans `expected_responses` déclenche une requête
    indépendante avec ses propres `authenticated` et `reverse_params`.

    Usage :
        class MyTests(ForgeCase):
            config: ConfigForgeCase = {
                "factory_params": {...},
                "tests": [
                    {
                        "path_name": "api:resource-list",
                        "method": "GET",
                        "expected_responses": {
                            200: {
                                "authenticated": True,
                                "expected_fields": ["id", "name"],
                            },
                            401: {
                                "authenticated": False,
                            },
                        },
                    }
                ],
            }
    """

    config: ConfigForgeCase

    STATUS_SUFFIX: Dict[int, str] = {
        200: "success",
        201: "created",
        204: "no_content",
        400: "bad_request",
        401: "not_authenticated",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        422: "unprocessable",
        500: "server_error",
    }

    # ------------------------------------------------------------------
    # Django TestCase hooks
    # ------------------------------------------------------------------

    def setUp(self) -> None:
        super().setUp()
        self.factory = self._build_factory()
        self.user = self.config.get("user") or self.factory.create(User)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._validate_config()
        cls._attach_all_tests()

    # ------------------------------------------------------------------
    # Validation de la config
    # ------------------------------------------------------------------

    @classmethod
    def _validate_config(cls) -> None:
        if not hasattr(cls, "config"):
            return
        if not isinstance(cls.config, dict):
            raise TypeError("config must be a dict")
        tests = cls.config.get("tests")
        if tests is None:          # config={} ou tests absent → ok
            return
        if not isinstance(tests, list):
            raise TypeError("config['tests'] must be a list")
        factory_params = cls.config.get("factory_params")
        if factory_params is not None and not isinstance(factory_params, dict):
            raise TypeError("config['factory_params'] must be a dict")
        for test in tests:
            if not isinstance(test, dict):
                raise TypeError("each test must be a dict")
            if "fixture" in test:
                if not isinstance(test["fixture"], dict):
                    raise TypeError("fixture must be a dict")
                if "object_name" not in test["fixture"]:
                    raise TypeError("object_name is required in fixture")

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    def _build_factory(self) -> ForgeModelFactory:
        params = self.config.get("factory_params") or {}
        return ForgeModelFactory(**params)

    # ------------------------------------------------------------------
    # Client / auth
    # ------------------------------------------------------------------

    def _build_authenticated_client(self) -> Client:
        return login_user_for_test(self.user)

    def _build_anonymous_client(self) -> Client:
        return Client()

    def _resolve_client(self, authenticated: bool) -> Client:
        return self._build_authenticated_client() if authenticated else self._build_anonymous_client()

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    def _resolve_url(self, test: TestCaseConfig, per_response_reverse_params: Optional[Dict] = None) -> str:
        """
        Résout l'URL en fusionnant les reverse_params du test (niveau global)
        avec ceux du status code courant (priorité aux params par réponse).
        """
        # Fusion : params globaux du test écrasés par ceux de la réponse courante
        base_reverse = dict(test.get("reverse_params") or {})
        if per_response_reverse_params:
            base_reverse.update(per_response_reverse_params)

        query = base_reverse.pop("query", None)
        kwargs = base_reverse.pop("kwargs", {})
        kwargs = {
            key: self._resolve_internal_fixture(data)
            for key, data in (kwargs or {}).items()
        }
        base_reverse["kwargs"] = kwargs
        url = reverse(test["path_name"], **base_reverse)
        if query:
            url = _append_query_string(url, query)
        return url

    # ------------------------------------------------------------------
    # Fixture data
    # ------------------------------------------------------------------

    def _resolve_fixture_json_data(self, fixture_json: FixtureJson) -> Dict[str, Any]:
        if fixture_json.get("data"):
            return fixture_json["data"]
        return self.factory.generate_fields_dict(
            model=fixture_json["model"],
            fields=fixture_json.get("fields"),
        )

    def _resolve_fixture_instance(self, fixture: Fixture) -> Any:
        if fixture.get("data"):
            self.__setattr__(fixture["object_name"], fixture.get("data"))
            return fixture["data"]
        fixture_instance = self.factory.create(
            fixture["model"], **(fixture.get("kwargs") or {})
        )
        self.__setattr__(fixture["object_name"], fixture_instance)
        return fixture_instance

    def _resolve_internal_fixture(self, data: str) -> Any:
        
        
        if not isinstance(data, str) or not data.startswith("self."):
            return data

        # Extraire le nom de l'attribut racine (ex: "self.user.pk" -> "user")
        remaining = data[len("self."):].strip(".")
        normalized = (
            remaining.replace("[", ".").replace("]", "")
            .replace("'", "").replace('"', "")
        )
        parts = [p for p in normalized.split(".") if p]
        if not parts:
            return data

        root = parts[0]
        current_value = getattr(self, root, None)

        for part in parts[1:]:
            if current_value is None:
                break
            if isinstance(current_value, dict):
                current_value = current_value.get(part)
            else:
                current_value = getattr(current_value, part, None)

        return current_value
    # ------------------------------------------------------------------
    # HTTP request
    # ------------------------------------------------------------------

    def _extract_request_data(self, http_params: HTTPClientParams) -> Optional[Dict[str, Any]]:
        fixture_json = http_params.pop("fixture", None)
        if fixture_json is None:
            return None
        return self._resolve_fixture_json_data(fixture_json)

    def _send_request(self, url: str, method: str, http_params: HTTPClientParams) -> Any:
        import json as _json

        data = self._extract_request_data(http_params)

        if data is not None:
            http_params.setdefault("content_type", "application/json")
            if http_params["content_type"] == "application/json":
                data = _json.dumps(data)

        client_method = getattr(self.client, method.lower())
        return client_method(url, data, **http_params)

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def _assert_status_code(self, response: Any, expected_status: int) -> None:
        self.assertEqual(
            response.status_code,
            expected_status,
            msg=(
                f"Status attendu {expected_status},  code reçu {response.status_code}. "
                f"Body: {_safe_response_body(response)}"
            ),
        )

    def _assert_fields_present(self, data: Dict | List, fields: list[str]) -> None:
        for field in fields:
            # Sentinel pour distinguer "absent" de "valeur None"
            _MISSING = object()
            value = _resolve_nested_field(data, field, default=_MISSING)
            self.assertIsNot(
                value,
                _MISSING,
                msg=f"Champ attendu '{field}' absent de la réponse.",
            )

    def _assert_field_values(self, data: Dict | List, expected_values: Dict[str, Any]) -> None:
        for field, expected in expected_values.items():
            actual = _resolve_nested_field(data, field)
            self.assertEqual(
                actual, expected,
                msg=f"Champ '{field}': attendu {expected!r}, reçu {actual!r}.",
            )

    def _assert_field_types(self, data: Dict | List, expected_types: Dict[str, type]) -> None:
        for field, expected_type in expected_types.items():
            actual = _resolve_nested_field(data, field)
            self.assertIsInstance(
                actual, expected_type,
                msg=f"Champ '{field}': type attendu {expected_type.__name__}, reçu {type(actual).__name__}.",
            )

    def _assert_fields_absent(self, data: Dict | List, forbidden_fields: list[str]) -> None:
        for field in forbidden_fields:
            value = _resolve_nested_field(data, field)
            self.assertIsNone(
                value,
                msg=f"Champ interdit '{field}' présent dans la réponse.",
            )

    def _assert_response_expected(self, response: Any, expected_response: Any) -> None:
        self.assertIsInstance(
            response.data, expected_response,
            msg=(
                f"Body attendu {_safe_response_body(expected_response)}, "
                f"reçu {_safe_response_body(response)}."
            ),
        )

    def _assert_response_body(self, response: Any, params: ResponseValidationParams) -> None:
        data = _parse_response_json(response)
        if data is None:
            return
        if fields := params.get("expected_fields"):
            self._assert_fields_present(data, fields)
        if values := params.get("expected_value_of_fields"):
            self._assert_field_values(data, values)
        if types := params.get("expected_type_of_fields"):
            self._assert_field_types(data, types)
        if forbidden := params.get("forbidden_fields"):
            self._assert_fields_absent(data, forbidden)
        if expected_response := params.get("expected_response"):
            self._assert_response_expected(response, expected_response)

    # ------------------------------------------------------------------
    # Test generation
    # ------------------------------------------------------------------

    @classmethod
    def _build_single_test(cls, test: TestCaseConfig, status_code: int, validation_params: ResponseValidationParams):

        def test_func(self: ForgeCase) -> None:
            if user := test.get("user"):
                self.user = user

            if fixture := test.get("fixture"):
                self._resolve_fixture_instance(fixture)

            authenticated = validation_params.get("authenticated", False)
            per_response_reverse = validation_params.get("reverse_params")

            self.client = self._resolve_client(authenticated)
            url = self._resolve_url(test, per_response_reverse)

            # Fusion : http_client_params du test écrasés par ceux de la réponse courante
            http_params: HTTPClientParams = {
                **(test.get("http_client_params") or {}),
                **(validation_params.get("http_client_params") or {}),
            }

            response = self._send_request(url, test["method"], http_params)

            self._assert_status_code(response, status_code)
            self._assert_response_body(response, validation_params)

        return test_func
    
    @classmethod
    def _attach_test_to_class(cls, test_name: str, test_func) -> None:
        setattr(cls, test_name, test_func)

    @classmethod
    def _build_test_name(cls, test: TestCaseConfig, test_index: int, status_code: int) -> str:
        base = test.get("test_name") or (
            test.get("path_name", f"test_{test_index}").replace(":", "_").replace("-", "_")
        )
        method = test.get("method", "GET").lower()
        suffix = cls.STATUS_SUFFIX.get(status_code, str(status_code))
        return f"test_{method}_{base}_{suffix}"

    @classmethod
    def _attach_all_tests(cls) -> None:
        if not hasattr(cls, "config"):
            return
        tests = cls.config.get("tests") or []
        for test_index, test in enumerate(tests):
            expected_responses: Dict[int, ResponseValidationParams] = test.get("expected_responses") or {}
            for status_code, validation_params in expected_responses.items():
                name = cls._build_test_name(test, test_index, status_code)
                func = cls._build_single_test(test, status_code, validation_params)
                func.__name__ = name
                cls._attach_test_to_class(name, func)

# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _resolve_nested_field(data: Union[Dict, List], field_path: str, default: Any = None) -> Any:
    parts = field_path.split(".")
    current = data

    for part in parts:
        if isinstance(current, list):
            if not part.isdigit():
                return default
            idx = int(part)
            if idx < 0 or idx >= len(current):
                return default
            current = current[idx]
        elif isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        else:
            return default

    return current
def _parse_response_json(response: Any) -> Optional[Dict]:
    try:
        return response.json()
    except Exception:
        return None


def _safe_response_body(response: Any) -> str:
    try:
        return str(response.json())
    except Exception:
        return getattr(response, "content", b"").decode("utf-8", errors="replace")[:300]


def _append_query_string(url: str, query: Any) -> str:
    from urllib.parse import urlencode
    if isinstance(query, dict):
        return f"{url}?{urlencode(query)}"
    return f"{url}?{query}"