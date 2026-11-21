# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--remote URL``: commands sent to a running server's REST API with the
root key (``X-API-Key``) instead of run in-process."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import httpx

from zephyrex.cli.Context import (
    REMOTE_KEY_ENV,
    CLIError,
    ExitCode,
    exit_code_for_status,
)

REMOTE_TIMEOUT_SECONDS = 60.0


class RemoteClient:
    """A REST session against one server. Each call answers the response's
    payload with its one-key envelope (``{"team": {...}}``) unwrapped, or
    raises ``CLIError`` carrying the status's exit code."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.last_etag: Optional[str] = None

    @classmethod
    def connect(cls, url: str, key: str) -> "RemoteClient":
        """A client for ``url``; with no key it refuses its first call."""
        headers = {"Accept": "application/json"}
        if key:
            headers["X-API-Key"] = key
        return cls(
            httpx.Client(
                base_url=url.rstrip("/"),
                headers=headers,
                timeout=REMOTE_TIMEOUT_SECONDS,
            )
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        if "X-API-Key" not in self.http.headers:
            raise CLIError(
                ExitCode.USAGE, f"--remote needs the root key in {REMOTE_KEY_ENV}"
            )
        try:
            response = self.http.request(
                method,
                path,
                json=body,
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers=dict(headers or {}),
            )
        except httpx.HTTPError as err:
            raise CLIError(ExitCode.ERROR, f"{method} {path} failed: {err}")
        payload = _json(response)
        self.last_etag = response.headers.get("ETag")
        if response.status_code >= 400:
            detail = (
                payload.get("detail", payload) if isinstance(payload, dict) else payload
            )
            raise CLIError(
                exit_code_for_status(response.status_code),
                f"{method} {path} answered {response.status_code}",
                {
                    "status": response.status_code,
                    "detail": detail,
                    "etag": response.headers.get("ETag"),
                },
            )
        return unwrap(payload)


def unwrap(payload: Any) -> Any:
    """The envelope's payload: ``{"team": {...}, "_links": …}`` -> ``{...}``
    (the one key beside HAL's ``_``-keys), ``{"teams": [...], <pagination>}``
    -> ``[...]`` (the one list-valued key); anything else as it is."""
    if not isinstance(payload, dict):
        return payload
    keys = [k for k in payload if not k.startswith("_")]
    if len(keys) == 1:
        return payload[keys[0]]
    lists = [k for k in keys if isinstance(payload[k], list)]
    return payload[lists[0]] if len(lists) == 1 else payload


def _json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text
