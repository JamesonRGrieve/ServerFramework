"""Endpoint-level content negotiation tests.

Exercises the five supported response/request formats (JSON, TOON, YAML,
TOML, XML) against the real CRUD endpoints via the ``server`` fixture.
Verifies that the ``ContentNegotiationMiddleware`` (registered in
``build_app``) correctly transcodes Accept-header-driven responses and
Content-Type-driven request bodies for every format the framework
advertises.

Tests use the ``/health`` endpoint (no auth, simple dict) for basic
format verification, and the ``/v1/team`` CRUD endpoints (standard
GET/POST/PUT/DELETE) for authenticated format round-trips.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from serverframework.lib.ContentNegotiation import (
    MIME_JSON,
    MIME_TOML,
    MIME_TOON,
    MIME_XML,
    MIME_YAML,
    deserialize,
    serialize,
)

# ---------------------------------------------------------------------------
# Format registry -- single source of truth for parametrization
# ---------------------------------------------------------------------------

FORMATS = [
    pytest.param("json", MIME_JSON, id="json"),
    pytest.param("toon", MIME_TOON, id="toon"),
    pytest.param("yaml", MIME_YAML, id="yaml"),
    pytest.param("toml", MIME_TOML, id="toml"),
    pytest.param("xml", MIME_XML, id="xml"),
]

# TOML cannot represent None values, so entity responses (which contain
# nullable fields like parent_id, image_url, etc.) cause serialization
# to fall back to JSON. Use this subset for CRUD tests on real entities.
FORMATS_NULL_SAFE = [
    pytest.param("json", MIME_JSON, id="json"),
    pytest.param("toon", MIME_TOON, id="toon"),
    pytest.param("yaml", MIME_YAML, id="yaml"),
    pytest.param("xml", MIME_XML, id="xml"),
]


def _parse_response(body: str, fmt: str) -> Any:
    """Parse a response body according to its format key."""
    return deserialize(body, fmt)


def _serialize_body(data: Any, fmt: str) -> str:
    """Serialize data for a request body according to format key."""
    body, _ = serialize(data, fmt)
    return body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFormatNegotiation:
    """Content negotiation across all five formats on the health endpoint.

    The /health endpoint is unconditionally mounted and returns a simple
    ``{"status": "UP"}`` dict -- ideal for a format round-trip test because
    it needs no auth, no database entities, and the payload is small enough
    to survive all format round-trips without type coercion issues (TOML
    requires a top-level table, XML coerces scalars, etc.).
    """

    @pytest.mark.parametrize("fmt,mime", FORMATS)
    def test_get_health_accept_header(
        self, server: Any, fmt: str, mime: str
    ) -> None:
        """GET /health with each Accept header returns the correct format."""
        resp = server.get("/health", headers={"Accept": mime})
        assert resp.status_code == 200, (
            f"GET /health with Accept: {mime} returned {resp.status_code}: "
            f"{resp.text}"
        )

        if fmt == "json":
            # JSON is the passthrough path -- no transcoding, Content-Type
            # is set by FastAPI itself (application/json).
            data = resp.json()
        else:
            assert resp.headers["content-type"] == mime, (
                f"Expected Content-Type {mime}, got "
                f"{resp.headers.get('content-type')}"
            )
            data = _parse_response(resp.text, fmt)

        assert data["status"] == "UP", (
            f"Parsed {fmt} body should contain status=UP, got {data}"
        )

    @pytest.mark.parametrize(
        "suffix,fmt,mime",
        [
            pytest.param(".toon", "toon", MIME_TOON, id="suffix-toon"),
            pytest.param(".yaml", "yaml", MIME_YAML, id="suffix-yaml"),
            pytest.param(".toml", "toml", MIME_TOML, id="suffix-toml"),
            pytest.param(".xml", "xml", MIME_XML, id="suffix-xml"),
            pytest.param(".json", "json", MIME_JSON, id="suffix-json"),
        ],
    )
    def test_url_suffix_format(
        self, server: Any, suffix: str, fmt: str, mime: str
    ) -> None:
        """URL suffix (.toon, .yaml, etc.) overrides Accept header."""
        headers = {"Accept": MIME_JSON}  # explicitly ask for JSON
        resp = server.get(f"/health{suffix}", headers=headers)
        assert resp.status_code == 200, (
            f"GET /health{suffix} returned {resp.status_code}: {resp.text}"
        )

        if fmt != "json":
            assert resp.headers["content-type"] == mime, (
                f"URL suffix {suffix} should override Accept to {mime}, "
                f"got {resp.headers.get('content-type')}"
            )
            data = _parse_response(resp.text, fmt)
            assert data["status"] == "UP"

    def test_unsupported_accept_406(self, server: Any) -> None:
        """Accept header with an unsupported type returns 406."""
        resp = server.get(
            "/health", headers={"Accept": "application/octet-stream"}
        )
        assert resp.status_code == 406, (
            f"Expected 406 Not Acceptable, got {resp.status_code}: "
            f"{resp.text}"
        )


class TestFormatNegotiationCRUD:
    """Format negotiation on authenticated CRUD endpoints.

    Uses the ``admin_a`` / ``team_a`` session fixtures to exercise the
    full middleware stack on a real entity lifecycle: create via JSON,
    then retrieve in every format. Uses the ``/v1/team`` endpoints which
    support standard GET/POST/PUT/DELETE CRUD.
    """

    def _auth_headers(self, admin_a: Any) -> Dict[str, str]:
        """Return auth headers for a user fixture."""
        return {"Authorization": f"Bearer {admin_a.jwt}"}

    def _create_team_json(
        self, server: Any, admin_a: Any, name: str
    ) -> Dict[str, Any]:
        """Create a team via JSON POST and return the created entity dict."""
        payload = {
            "team": {
                "name": name,
                "description": f"Format test team: {name}",
            }
        }
        resp = server.post(
            "/v1/team",
            json=payload,
            headers=self._auth_headers(admin_a),
        )
        assert resp.status_code == 201, (
            f"JSON POST /v1/team failed: {resp.status_code} {resp.text}"
        )
        data = resp.json()
        return data.get("team", data)

    # -- GET single entity in each response format -------------------------

    @pytest.mark.parametrize("fmt,mime", FORMATS_NULL_SAFE)
    def test_get_team_by_id_format(
        self, server: Any, admin_a: Any, team_a: Any, fmt: str, mime: str
    ) -> None:
        """GET /v1/team/{id} with each Accept header and verify round-trip."""
        from faker import Faker

        faker = Faker()
        team = self._create_team_json(
            server, admin_a, f"GetFmt-{fmt}-{faker.random_int()}"
        )
        team_id = team["id"]

        headers = {**self._auth_headers(admin_a), "Accept": mime}
        resp = server.get(f"/v1/team/{team_id}", headers=headers)
        assert resp.status_code == 200, (
            f"GET /v1/team/{team_id} with Accept: {mime} returned "
            f"{resp.status_code}: {resp.text}"
        )

        if fmt == "json":
            data = resp.json()
        else:
            assert resp.headers["content-type"] == mime, (
                f"Expected Content-Type {mime}, got "
                f"{resp.headers.get('content-type')}"
            )
            data = _parse_response(resp.text, fmt)

        # Response is wrapped in {"team": {...}} or flat.
        team_data = data.get("team", data) if isinstance(data, dict) else data
        assert "id" in team_data, (
            f"Parsed {fmt} response should contain team with 'id', "
            f"got keys: {list(team_data.keys()) if isinstance(team_data, dict) else type(team_data)}"
        )
        assert str(team_data["id"]) == str(team_id), (
            f"Returned team ID should match created ID"
        )

    # -- GET list in each response format ----------------------------------

    @pytest.mark.parametrize("fmt,mime", FORMATS_NULL_SAFE)
    def test_get_team_list_format(
        self, server: Any, admin_a: Any, team_a: Any, fmt: str, mime: str
    ) -> None:
        """GET /v1/team (list) with each Accept header."""
        headers = {**self._auth_headers(admin_a), "Accept": mime}
        resp = server.get("/v1/team", headers=headers)
        assert resp.status_code == 200, (
            f"GET /v1/team with Accept: {mime} returned "
            f"{resp.status_code}: {resp.text}"
        )

        if fmt == "json":
            data = resp.json()
        else:
            assert resp.headers["content-type"] == mime, (
                f"Expected Content-Type {mime}, got "
                f"{resp.headers.get('content-type')}"
            )
            data = _parse_response(resp.text, fmt)

        # The list response is either a dict with a plural key or a bare list.
        if isinstance(data, dict):
            list_values = [v for v in data.values() if isinstance(v, list)]
            assert len(list_values) > 0, (
                f"Expected at least one list in {fmt} response dict, "
                f"got keys: {list(data.keys())}"
            )
        elif isinstance(data, list):
            pass  # Bare list is acceptable
        else:
            pytest.fail(
                f"Expected dict or list from {fmt} list response, "
                f"got {type(data)}"
            )

    # -- POST with each Content-Type (request body format) -----------------

    @pytest.mark.parametrize("fmt,mime", FORMATS)
    def test_post_team_request_format(
        self, server: Any, admin_a: Any, team_a: Any, fmt: str, mime: str
    ) -> None:
        """POST a new team with each Content-Type to verify request parsing."""
        from faker import Faker

        faker = Faker()
        team_name = f"PostFmt-{fmt}-{faker.random_int()}"
        payload = {
            "team": {
                "name": team_name,
                "description": f"Created via {fmt} content type",
            }
        }

        if fmt == "json":
            body = json.dumps(payload)
        else:
            body = _serialize_body(payload, fmt)

        headers = {
            **self._auth_headers(admin_a),
            "Content-Type": mime,
        }
        resp = server.post("/v1/team", content=body, headers=headers)

        assert resp.status_code == 201, (
            f"POST /v1/team with Content-Type: {mime} returned "
            f"{resp.status_code}: {resp.text}"
        )

        # Response is JSON by default since we did not set Accept.
        data = resp.json()
        team_data = data.get("team", data) if isinstance(data, dict) else data
        assert "id" in team_data, (
            f"Response should contain created team with 'id', got: {data}"
        )

    # -- POST create with response in each format -------------------------

    @pytest.mark.parametrize("fmt,mime", FORMATS_NULL_SAFE)
    def test_post_team_response_format(
        self, server: Any, admin_a: Any, team_a: Any, fmt: str, mime: str
    ) -> None:
        """POST a new team with JSON body and Accept each response format."""
        from faker import Faker

        faker = Faker()
        team_name = f"RespFmt-{fmt}-{faker.random_int()}"
        payload = {
            "team": {
                "name": team_name,
                "description": f"Response in {fmt}",
            }
        }

        headers = {
            **self._auth_headers(admin_a),
            "Content-Type": MIME_JSON,
            "Accept": mime,
        }
        resp = server.post(
            "/v1/team", content=json.dumps(payload), headers=headers
        )

        assert resp.status_code == 201, (
            f"POST /v1/team with Accept: {mime} returned "
            f"{resp.status_code}: {resp.text}"
        )

        if fmt == "json":
            data = resp.json()
        else:
            assert resp.headers["content-type"] == mime, (
                f"Expected Content-Type {mime}, got "
                f"{resp.headers.get('content-type')}"
            )
            data = _parse_response(resp.text, fmt)

        team_data = data.get("team", data) if isinstance(data, dict) else data
        assert "id" in team_data, (
            f"Response should contain created team with 'id', got: {data}"
        )

    # -- Combined: POST in one format, receive in another ------------------

    @pytest.mark.parametrize(
        "req_fmt,req_mime,resp_fmt,resp_mime",
        [
            pytest.param("yaml", MIME_YAML, "toon", MIME_TOON, id="yaml-to-toon"),
            pytest.param("toon", MIME_TOON, "xml", MIME_XML, id="toon-to-xml"),
            pytest.param("toml", MIME_TOML, "yaml", MIME_YAML, id="toml-to-yaml"),
            pytest.param("xml", MIME_XML, "json", MIME_JSON, id="xml-to-json"),
            pytest.param("json", MIME_JSON, "toon", MIME_TOON, id="json-to-toon"),
        ],
    )
    def test_post_cross_format(
        self,
        server: Any,
        admin_a: Any,
        team_a: Any,
        req_fmt: str,
        req_mime: str,
        resp_fmt: str,
        resp_mime: str,
    ) -> None:
        """POST with one Content-Type and Accept a different format."""
        from faker import Faker

        faker = Faker()
        team_name = f"Cross-{req_fmt}-{resp_fmt}-{faker.random_int()}"
        payload = {
            "team": {
                "name": team_name,
                "description": f"Sent as {req_fmt}, received as {resp_fmt}",
            }
        }

        body = _serialize_body(payload, req_fmt)
        headers = {
            **self._auth_headers(admin_a),
            "Content-Type": req_mime,
            "Accept": resp_mime,
        }
        resp = server.post("/v1/team", content=body, headers=headers)

        assert resp.status_code == 201, (
            f"POST {req_fmt}->{resp_fmt} returned {resp.status_code}: "
            f"{resp.text}"
        )

        if resp_fmt == "json":
            data = resp.json()
        else:
            assert resp.headers["content-type"] == resp_mime, (
                f"Expected Content-Type {resp_mime}, got "
                f"{resp.headers.get('content-type')}"
            )
            data = _parse_response(resp.text, resp_fmt)

        team_data = data.get("team", data) if isinstance(data, dict) else data
        assert "id" in team_data, (
            f"Cross-format response ({req_fmt}->{resp_fmt}) should contain "
            f"team with 'id', got: {data}"
        )

    # -- 406 on authenticated endpoint ------------------------------------

    def test_unsupported_accept_406_authenticated(
        self, server: Any, admin_a: Any, team_a: Any
    ) -> None:
        """Accept header with an unsupported type returns 406 on CRUD endpoint."""
        headers = {
            **self._auth_headers(admin_a),
            "Accept": "application/octet-stream",
        }
        resp = server.get("/v1/team", headers=headers)
        assert resp.status_code == 406, (
            f"Expected 406 Not Acceptable, got {resp.status_code}: "
            f"{resp.text}"
        )
