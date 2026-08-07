"""Tests for ``lib.Federation_REST`` (Item 16 — REST upstream federation).

Covers:

* OpenAPI → Pydantic model importer (``$ref``, ``allOf``, ``oneOf``,
  enums, scalars).
* :class:`OperationSpec` discovery from ``paths`` + method tables.
* :class:`RESTUpstreamTransport` (path templating, query/body splitting).
* :func:`derive_external_models` (REST→GQL projection by binding
  imported Pydantic models to the transport so the existing
  ``Pydantic2Strawberry`` pipeline lifts them onto the GraphQL surface).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.federation.BLL_Federation_REST import (
    OPENAPI_TYPE_TO_PY,
    OpenAPIToPydanticResult,
    OperationSpec,
    RESTUpstreamTransport,
    derive_external_models,
    openapi_to_pydantic_models,
)


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeHTTPClient:
    """Captures every outbound HTTP call without doing any real I/O.

    Provides both async (used by ``RESTUpstreamTransport.send``) and sync
    (used by ``RESTUpstreamTransport.send_sync``) methods so the same
    fixture exercises both code paths.
    """

    def __init__(self, response: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.response = response

    def _record(self, method: str, url: str, kwargs: Dict[str, Any]) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response

    async def get(self, url: str, **kwargs: Any) -> Any:
        return self._record("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        return self._record("POST", url, kwargs)

    async def put(self, url: str, **kwargs: Any) -> Any:
        return self._record("PUT", url, kwargs)

    async def patch(self, url: str, **kwargs: Any) -> Any:
        return self._record("PATCH", url, kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Any:
        return self._record("DELETE", url, kwargs)


class _SyncFakeHTTPClient:
    """Synchronous twin of :class:`_FakeHTTPClient`."""

    def __init__(self, response: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.response = response

    def _record(self, method: str, url: str, kwargs: Dict[str, Any]) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._record("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._record("POST", url, kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        return self._record("PUT", url, kwargs)

    def patch(self, url: str, **kwargs: Any) -> Any:
        return self._record("PATCH", url, kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        return self._record("DELETE", url, kwargs)


# ---------------------------------------------------------------------------
# OpenAPI → Pydantic
# ---------------------------------------------------------------------------


def test_openapi_imports_simple_model():
    spec = {
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                }
            }
        }
    }
    result = openapi_to_pydantic_models(spec)
    user = result.models["User"]
    fields = user.model_fields
    assert fields["id"].annotation is str
    assert fields["name"].annotation is str
    # Optional fields wrap.
    from typing import Union, get_args, get_origin

    assert get_origin(fields["age"].annotation) is Union


def test_openapi_prefixes_models():
    spec = {
        "components": {
            "schemas": {"User": {"type": "object", "properties": {}}}
        }
    }
    result = openapi_to_pydantic_models(spec, prefix="Acme_")
    assert "Acme_User" in result.models
    assert "User" not in result.models


def test_openapi_resolves_refs():
    spec = {
        "components": {
            "schemas": {
                "Address": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "address": {"$ref": "#/components/schemas/Address"},
                    },
                },
            }
        }
    }
    result = openapi_to_pydantic_models(spec)
    user = result.models["User"]
    # Pydantic resolves the string forward reference via model_rebuild;
    # the resolved annotation is the Address class.
    address_field = user.model_fields["address"]
    from typing import Union, get_args, get_origin

    if get_origin(address_field.annotation) is Union:
        members = [m for m in get_args(address_field.annotation) if m is not type(None)]
        assert members[0].__name__ == "Address"
    else:
        assert getattr(address_field.annotation, "__name__", None) == "Address"


def test_openapi_handles_allof_flattening():
    spec = {
        "components": {
            "schemas": {
                "Base": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                },
                "User": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string"}},
                        },
                    ]
                },
            }
        }
    }
    result = openapi_to_pydantic_models(spec)
    user = result.models["User"]
    assert "id" in user.model_fields
    assert "name" in user.model_fields


def test_openapi_extracts_enums():
    from enum import Enum

    spec = {
        "components": {
            "schemas": {
                "Status": {
                    "type": "string",
                    "enum": ["active", "inactive"],
                }
            }
        }
    }
    result = openapi_to_pydantic_models(spec)
    assert "Status" in result.enums
    assert issubclass(result.enums["Status"], Enum)


def test_openapi_handles_arrays():
    from typing import List as _List, get_args, get_origin

    spec = {
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "tags": {"type": "array", "items": {"type": "string"}}
                    },
                }
            }
        }
    }
    result = openapi_to_pydantic_models(spec)
    user = result.models["User"]
    tags = user.model_fields["tags"].annotation
    # Optional[List[str]]
    from typing import Union

    if get_origin(tags) is Union:
        list_arg = next(a for a in get_args(tags) if get_origin(a) is list)
    else:
        list_arg = tags
    assert get_origin(list_arg) is list
    assert get_args(list_arg) == (str,)


def test_openapi_discovers_operations():
    spec = {
        "paths": {
            "/users/{id}": {
                "get": {
                    "operationId": "get_user",
                    "parameters": [{"name": "id", "in": "path"}],
                    "responses": {"200": {}},
                },
                "delete": {
                    "operationId": "delete_user",
                    "parameters": [{"name": "id", "in": "path"}],
                    "responses": {"204": {}},
                },
            },
            "/users": {
                "get": {"operationId": "list_users", "parameters": []},
                "post": {
                    "operationId": "create_user",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/User"}
                            }
                        }
                    },
                },
            },
        }
    }
    result = openapi_to_pydantic_models(spec)
    assert {"get_user", "delete_user", "list_users", "create_user"} <= set(
        result.operations.keys()
    )
    assert result.operations["get_user"].method == "GET"
    assert result.operations["create_user"].method == "POST"
    assert result.operations["create_user"].path_template == "/users"


def test_openapi_synthesizes_operation_id_when_missing():
    spec = {
        "paths": {
            "/widgets/{id}/parts": {"get": {"parameters": []}},
        }
    }
    result = openapi_to_pydantic_models(spec)
    # No operationId → method+path slug
    assert any(
        name.startswith("get_") for name in result.operations.keys()
    )


def test_openapi_handles_oneof_union():
    spec = {
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "integer"},
                            ]
                        }
                    },
                }
            }
        }
    }
    result = openapi_to_pydantic_models(spec)
    item = result.models["Item"]
    from typing import Union, get_args, get_origin

    ann = item.model_fields["value"].annotation
    # Optional wrap leaves Union at top.
    assert get_origin(ann) is Union


# ---------------------------------------------------------------------------
# RESTUpstreamTransport
# ---------------------------------------------------------------------------


@pytest.fixture
def transport_with_ops() -> RESTUpstreamTransport:
    ops = {
        "get_user": OperationSpec(
            name="get_user",
            method="GET",
            path_template="/users/{id}",
            parameters=[{"name": "id", "in": "path"}],
        ),
        "list_users": OperationSpec(
            name="list_users",
            method="GET",
            path_template="/users",
            parameters=[],
        ),
        "create_user": OperationSpec(
            name="create_user",
            method="POST",
            path_template="/users",
            parameters=[],
        ),
        "update_user": OperationSpec(
            name="update_user",
            method="PUT",
            path_template="/users/{id}",
            parameters=[{"name": "id", "in": "path"}],
        ),
        "delete_user": OperationSpec(
            name="delete_user",
            method="DELETE",
            path_template="/users/{id}",
            parameters=[{"name": "id", "in": "path"}],
        ),
    }
    http = _FakeHTTPClient(response={"id": "x"})
    transport = RESTUpstreamTransport(http, base_url="https://api.example.com", operations=ops)
    transport._http_for_test = http  # convenience for assertions
    return transport


async def test_transport_substitutes_path_args(transport_with_ops):
    await transport_with_ops.send(operation="get_user", path_args={"id": "abc"})
    call = transport_with_ops._http_for_test.calls[-1]
    assert call["url"] == "https://api.example.com/users/abc"
    assert call["method"] == "GET"


async def test_transport_query_args_for_get(transport_with_ops):
    await transport_with_ops.send(operation="list_users", query_args={"limit": 10})
    call = transport_with_ops._http_for_test.calls[-1]
    assert call["params"] == {"limit": 10}


async def test_transport_body_for_post(transport_with_ops):
    await transport_with_ops.send(
        operation="create_user", body={"name": "bob"}
    )
    call = transport_with_ops._http_for_test.calls[-1]
    assert call["json"] == {"name": "bob"}
    assert call["method"] == "POST"


async def test_transport_idempotency_key_passed_through(transport_with_ops):
    await transport_with_ops.send(
        operation="create_user",
        body={"name": "bob"},
        idempotency_key="key-42",
    )
    call = transport_with_ops._http_for_test.calls[-1]
    assert call["idempotency_key"] == "key-42"


async def test_transport_unknown_operation_raises(transport_with_ops):
    with pytest.raises(KeyError, match="bogus"):
        await transport_with_ops.send(operation="bogus")


# ---------------------------------------------------------------------------
# derive_external_models — REST → GQL projection
# ---------------------------------------------------------------------------


def test_derive_external_models_creates_subclass_pairs():
    spec = {
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                }
            }
        },
        "paths": {
            "/users/{id}": {
                "get": {"operationId": "get_user", "parameters": [{"name": "id", "in": "path"}]}
            }
        },
    }
    result = openapi_to_pydantic_models(spec)
    # ``derive_external_models`` synthesizes ``*_via_provider`` static methods
    # that call ``transport.send_sync``; provide a sync HTTP fake.
    http = _SyncFakeHTTPClient(response={"id": "abc", "name": "alice"})
    transport = RESTUpstreamTransport(http, "https://api.example.com", result.operations)
    derived = derive_external_models(pydantic_result=result, transport=transport)
    assert "User" in derived
    cls = derived["User"]
    assert hasattr(cls, "get_via_provider")
    # The synthesized method dispatches through the transport.
    payload = cls.get_via_provider(None, external_id="abc")
    assert payload == {"id": "abc", "name": "alice"}
    assert http.calls[-1]["url"] == "https://api.example.com/users/abc"


def test_derive_external_models_respects_crud_map():
    spec = {
        "components": {"schemas": {"Order": {"type": "object", "properties": {}}}},
        "paths": {
            "/widgets": {"get": {"operationId": "fetch_widgets"}},
            "/widgets/{id}": {"get": {"operationId": "fetch_widget"}},
        },
    }
    result = openapi_to_pydantic_models(spec)
    http = _SyncFakeHTTPClient(response=[{"id": "z"}])
    transport = RESTUpstreamTransport(http, "https://api.example.com", result.operations)
    derived = derive_external_models(
        pydantic_result=result,
        transport=transport,
        crud_map={"Order": {"list": "fetch_widgets"}},
    )
    cls = derived["Order"]
    rows = cls.list_via_provider(None)
    assert rows == [{"id": "z"}]
    assert http.calls[-1]["url"] == "https://api.example.com/widgets"
