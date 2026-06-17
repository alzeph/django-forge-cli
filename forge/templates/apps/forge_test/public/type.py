from typing import (
    Literal, TypedDict, List, Tuple, Mapping,
    Dict, Any, Optional, Type, Sequence, Union
)
from django.http import QueryDict
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
_RequestData = Union[Mapping[str, Any], str,
                     Sequence[Tuple[str, Any]], QueryDict]


class ForgeModelFactoryParams(TypedDict, total=False):
    max_depth: int
    create_m2m: bool
    m2m_count: int
    max_retries: int
    person_name_fields: List[str]
    establishment_name_fields: List[str]
    text_description_fields: List[str]
    text_word_range: Tuple[int, int]
    fill_images: bool
    image_dimensions: Tuple[int, int]


class ReverseParams(TypedDict, total=False):
    urlconf: Optional[str]
    args: Optional[Sequence[Any]]
    kwargs: Optional[Dict[str, Any]]
    current_app: Optional[str]
    query: Optional[Union[Dict[str, Any], QueryDict]]
    fragment: Optional[str]


class FixtureJson(TypedDict, total=False):
    model: Type[models.Model]
    fields: Optional[List[str]]
    data: Optional[Dict[str, Any]]


class HTTPClientParams(TypedDict, total=False):
    fixture: FixtureJson
    content_type: str
    follow: bool
    secure: bool
    QUERY_STRING: str
    headers: Optional[Mapping[str, Any]]


class ResponseValidationParams(TypedDict, total=False):
    reverse_params: ReverseParams
    http_client_params: HTTPClientParams
    authenticated: bool
    expected_response: Type[Any]
    expected_fields: List[str]
    expected_value_of_fields: Dict[str, Any]
    expected_type_of_fields: Dict[str, Type[Any]]
    forbidden_fields: List[str]


class Fixture(TypedDict, total=False):
    object_name: str
    model: Type[models.Model]
    kwargs: Dict[str, Any]
    data: Any




class TestCaseConfig(TypedDict, total=False):
    user: Optional[User]
    test_name:str
    path_name: str
    authentificated: bool
    method: HttpMethod
    reverse_params: ReverseParams
    http_client_params: HTTPClientParams
    fixture: Fixture
    expected_responses: Dict[int, ResponseValidationParams]


class ConfigForgeCase(TypedDict, total=False):
    user: Optional[User]
    factory_params: Optional[ForgeModelFactoryParams]
    tests: Optional[List[TestCaseConfig]]
