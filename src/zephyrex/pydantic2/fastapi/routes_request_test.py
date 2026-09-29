# SPDX-License-Identifier: AGPL-3.0-or-later
"""How custom routes read a request: its JSON body and the comma-separated
query parameters a route method declares."""

import asyncio
import inspect
from typing import Any, List, Optional

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from zephyrex.pydantic2.fastapi.routes import _json_body, _list_query_args


def _request(body: bytes = b"", query: str = "") -> Request:
    async def receive() -> Any:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "query_string": query.encode(),
        },
        receive,
    )


@pytest.mark.parametrize(
    "body, expected",
    [(b"", {}), (b'{"a": 1}', {"a": 1}), (b"[1, 2]", [1, 2])],
    ids=["absent-is-empty-object", "object", "array"],
)
def test_json_body(body, expected):
    assert asyncio.run(_json_body(_request(body))) == expected


def test_json_body_rejects_malformed_json():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_json_body(_request(b"{not json")))
    assert exc.value.status_code == 400


def _takes_both(include: Optional[List[str]] = None, fields: Optional[Any] = None):
    return include, fields


def _takes_neither(id: str):
    return id


def test_list_query_args_forwards_declared_params_only():
    request = _request(query="include=user,%20role,&fields=id&other=x")
    assert _list_query_args(request, inspect.signature(_takes_both)) == {
        "include": ["user", "role"],
        "fields": ["id"],
    }
    assert _list_query_args(request, inspect.signature(_takes_neither)) == {}


def test_list_query_args_omits_empty_values():
    request = _request(query="include=,&fields=")
    assert _list_query_args(request, inspect.signature(_takes_both)) == {}
