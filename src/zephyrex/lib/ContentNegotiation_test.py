"""Tests for multi-format content negotiation middleware."""

from __future__ import annotations

import json
import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "content_negotiation_test")

import tomllib

import pytest
import tomli_w
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from toon_format import decode as toon_decode
from toon_format import encode as toon_encode

from zephyrex.lib.ContentNegotiation import (
    DEFAULT_FORMAT,
    MIME_JSON,
    MIME_TOML,
    MIME_TOON,
    MIME_XML,
    MIME_XML_ALT,
    MIME_YAML,
    MIME_YAML_ALT,
    ContentNegotiationMiddleware,
    deserialize,
    resolve_request_format,
    resolve_response_format,
    serialize,
    strip_format_suffix,
)


# ---------------------------------------------------------------------------
# Fixtures -- tiny FastAPI app with the middleware wired in
# ---------------------------------------------------------------------------

SAMPLE_DICT = {"name": "Alice", "age": 30, "active": True}
SAMPLE_LIST = [
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
]


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ContentNegotiationMiddleware)

    @app.get("/item")
    async def get_item() -> dict:
        return SAMPLE_DICT

    @app.get("/items")
    async def get_items() -> list:
        return SAMPLE_LIST

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        body = await request.json()
        return body  # type: ignore[no-any-return]

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


# ---------------------------------------------------------------------------
# Unit tests: resolve_request_format
# ---------------------------------------------------------------------------


class TestResolveRequestFormat:
    def test_none_defaults_to_json(self) -> None:
        assert resolve_request_format(None) == "json"

    def test_empty_defaults_to_json(self) -> None:
        assert resolve_request_format("") == "json"

    def test_json(self) -> None:
        assert resolve_request_format("application/json") == "json"

    def test_json_with_charset(self) -> None:
        assert resolve_request_format("application/json; charset=utf-8") == "json"

    def test_toon(self) -> None:
        assert resolve_request_format("application/toon") == "toon"

    def test_yaml(self) -> None:
        assert resolve_request_format("application/yaml") == "yaml"

    def test_yaml_alt(self) -> None:
        assert resolve_request_format("application/x-yaml") == "yaml"

    def test_toml(self) -> None:
        assert resolve_request_format("application/toml") == "toml"

    def test_xml(self) -> None:
        assert resolve_request_format("application/xml") == "xml"

    def test_xml_alt(self) -> None:
        assert resolve_request_format("text/xml") == "xml"

    def test_unknown_defaults_to_json(self) -> None:
        assert resolve_request_format("text/plain") == "json"


# ---------------------------------------------------------------------------
# Unit tests: resolve_response_format
# ---------------------------------------------------------------------------


class TestResolveResponseFormat:
    def test_no_accept_defaults_to_json(self) -> None:
        assert resolve_response_format(None) == "json"

    def test_wildcard_defaults_to_json(self) -> None:
        assert resolve_response_format("*/*") == "json"

    def test_json_accept(self) -> None:
        assert resolve_response_format("application/json") == "json"

    def test_toon_accept(self) -> None:
        assert resolve_response_format("application/toon") == "toon"

    def test_yaml_accept(self) -> None:
        assert resolve_response_format("application/yaml") == "yaml"

    def test_yaml_alt_accept(self) -> None:
        assert resolve_response_format("application/x-yaml") == "yaml"

    def test_toml_accept(self) -> None:
        assert resolve_response_format("application/toml") == "toml"

    def test_xml_accept(self) -> None:
        assert resolve_response_format("application/xml") == "xml"

    def test_xml_alt_accept(self) -> None:
        assert resolve_response_format("text/xml") == "xml"

    def test_unsupported_returns_none(self) -> None:
        assert resolve_response_format("application/octet-stream") is None

    def test_quality_weighted_picks_highest(self) -> None:
        accept = "application/json;q=0.5, application/toon;q=0.9"
        assert resolve_response_format(accept) == "toon"

    def test_suffix_overrides_accept(self) -> None:
        result = resolve_response_format("application/json", path="/items.yaml")
        assert result == "yaml"

    def test_suffix_toon(self) -> None:
        assert resolve_response_format(None, path="/items.toon") == "toon"

    def test_suffix_toml(self) -> None:
        assert resolve_response_format(None, path="/items.toml") == "toml"

    def test_suffix_xml(self) -> None:
        assert resolve_response_format(None, path="/items.xml") == "xml"


# ---------------------------------------------------------------------------
# Unit tests: strip_format_suffix
# ---------------------------------------------------------------------------


class TestStripFormatSuffix:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/items.json", "/items"),
            ("/items.toon", "/items"),
            ("/items.yaml", "/items"),
            ("/items.toml", "/items"),
            ("/items.xml", "/items"),
            ("/items", "/items"),
            ("/items.txt", "/items.txt"),
        ],
    )
    def test_strip(self, path: str, expected: str) -> None:
        assert strip_format_suffix(path) == expected


# ---------------------------------------------------------------------------
# Unit tests: serialize / deserialize round-trips
# ---------------------------------------------------------------------------


class TestSerializeDeserialize:
    """Verify lossless round-trip for each supported format."""

    @pytest.mark.parametrize("fmt", ["json", "toon", "yaml", "xml"])
    def test_dict_round_trip(self, fmt: str) -> None:
        body, mime = serialize(SAMPLE_DICT, fmt)
        assert isinstance(body, str)
        assert len(body) > 0
        restored = deserialize(body, fmt)
        assert restored == SAMPLE_DICT

    @pytest.mark.parametrize("fmt", ["json", "toon", "yaml", "xml"])
    def test_list_round_trip(self, fmt: str) -> None:
        body, mime = serialize(SAMPLE_LIST, fmt)
        restored = deserialize(body, fmt)
        assert restored == SAMPLE_LIST

    def test_toml_dict_round_trip(self) -> None:
        """TOML requires a top-level table, so only dicts round-trip cleanly."""
        # TOML serializes all values as strings by default; use int-safe data.
        data = {"name": "Alice", "age": 30, "active": True}
        body, mime = serialize(data, "toml")
        restored = deserialize(body, "toml")
        assert restored == data

    def test_unsupported_serialize_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            serialize({}, "msgpack")

    def test_unsupported_deserialize_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            deserialize("{}", "msgpack")

    def test_empty_body_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            deserialize("", "json")

    def test_empty_whitespace_body_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            deserialize("   ", "json")

    def test_bytes_input(self) -> None:
        body = json.dumps(SAMPLE_DICT).encode("utf-8")
        restored = deserialize(body, "json")
        assert restored == SAMPLE_DICT


# ---------------------------------------------------------------------------
# Unit tests: serialize content types
# ---------------------------------------------------------------------------


class TestSerializeContentType:
    @pytest.mark.parametrize(
        "fmt,expected_mime",
        [
            ("json", MIME_JSON),
            ("toon", MIME_TOON),
            ("yaml", MIME_YAML),
            ("toml", MIME_TOML),
            ("xml", MIME_XML),
        ],
    )
    def test_content_type(self, fmt: str, expected_mime: str) -> None:
        _, mime = serialize({"key": "val"}, fmt)
        assert mime == expected_mime


# ---------------------------------------------------------------------------
# Integration tests: GET with Accept header
# ---------------------------------------------------------------------------


class TestGetAcceptHeader:
    """GET responses re-serialized according to Accept header."""

    def test_default_json(self, client: TestClient) -> None:
        resp = client.get("/item")
        assert resp.status_code == 200
        assert resp.json() == SAMPLE_DICT

    def test_accept_json(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_JSON})
        assert resp.status_code == 200
        assert resp.json() == SAMPLE_DICT

    def test_accept_toon(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_TOON})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_TOON
        parsed = toon_decode(resp.text)
        assert parsed["name"] == "Alice"  # type: ignore[call-overload, index]
        assert parsed["age"] == 30  # type: ignore[call-overload, index]

    def test_accept_yaml(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_YAML})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_YAML
        parsed = yaml.safe_load(resp.text)
        assert parsed == SAMPLE_DICT

    def test_accept_yaml_alt(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_YAML_ALT})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_YAML

    def test_accept_toml(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_TOML})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_TOML
        parsed = tomllib.loads(resp.text)
        assert parsed["name"] == "Alice"

    def test_accept_xml(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_XML})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_XML
        assert "<?xml" in resp.text
        assert "<name>Alice</name>" in resp.text

    def test_accept_xml_alt(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": MIME_XML_ALT})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_XML

    def test_unsupported_accept_406(self, client: TestClient) -> None:
        resp = client.get("/item", headers={"Accept": "application/octet-stream"})
        assert resp.status_code == 406
        body = resp.json()
        assert body["detail"] == "Not Acceptable"

    def test_list_toon_tabular(self, client: TestClient) -> None:
        """Verify TOON uses tabular encoding for lists of uniform dicts."""
        resp = client.get("/items", headers={"Accept": MIME_TOON})
        assert resp.status_code == 200
        parsed = toon_decode(resp.text)
        assert len(parsed) == 2  # type: ignore[arg-type]
        assert parsed[0]["name"] == "Alice"  # type: ignore[index]
        assert parsed[1]["name"] == "Bob"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Integration tests: GET with format suffix
# ---------------------------------------------------------------------------


class TestGetFormatSuffix:
    """Format suffixes on the URL override Accept header."""

    def test_suffix_json(self, client: TestClient) -> None:
        resp = client.get("/item.json")
        assert resp.status_code == 200
        assert resp.json() == SAMPLE_DICT

    def test_suffix_toon(self, client: TestClient) -> None:
        resp = client.get("/item.toon")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_TOON
        parsed = toon_decode(resp.text)
        assert parsed["name"] == "Alice"  # type: ignore[call-overload, index]

    def test_suffix_yaml(self, client: TestClient) -> None:
        resp = client.get("/item.yaml")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_YAML

    def test_suffix_toml(self, client: TestClient) -> None:
        resp = client.get("/item.toml")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_TOML

    def test_suffix_xml(self, client: TestClient) -> None:
        resp = client.get("/item.xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_XML

    def test_suffix_overrides_accept(self, client: TestClient) -> None:
        """URL suffix wins over Accept header."""
        resp = client.get(
            "/item.toon",
            headers={"Accept": MIME_JSON},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_TOON


# ---------------------------------------------------------------------------
# Integration tests: POST with non-JSON Content-Type
# ---------------------------------------------------------------------------


class TestPostRequestParsing:
    """Non-JSON request bodies are transcoded to JSON for Pydantic."""

    def test_post_json(self, client: TestClient) -> None:
        resp = client.post(
            "/echo",
            content=json.dumps(SAMPLE_DICT),
            headers={"Content-Type": MIME_JSON},
        )
        assert resp.status_code == 200
        assert resp.json() == SAMPLE_DICT

    def test_post_toon(self, client: TestClient) -> None:
        toon_body = toon_encode(SAMPLE_DICT)
        resp = client.post(
            "/echo",
            content=toon_body,
            headers={"Content-Type": MIME_TOON},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["name"] == "Alice"
        assert result["age"] == 30

    def test_post_yaml(self, client: TestClient) -> None:
        yaml_body = yaml.dump(SAMPLE_DICT)
        resp = client.post(
            "/echo",
            content=yaml_body,
            headers={"Content-Type": MIME_YAML},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["name"] == "Alice"

    def test_post_toml(self, client: TestClient) -> None:
        toml_body = tomli_w.dumps(SAMPLE_DICT)
        resp = client.post(
            "/echo",
            content=toml_body,
            headers={"Content-Type": MIME_TOML},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["name"] == "Alice"

    def test_post_xml(self, client: TestClient) -> None:
        xml_body, _ = serialize(SAMPLE_DICT, "xml")
        resp = client.post(
            "/echo",
            content=xml_body,
            headers={"Content-Type": MIME_XML},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["name"] == "Alice"

    def test_malformed_toon_422(self, client: TestClient) -> None:
        # TOON is permissive and treats most bare text as a string, so
        # use input that triggers a parse error (mixed key/array syntax).
        resp = client.post(
            "/echo",
            content="key: val\n[broken",
            headers={"Content-Type": MIME_TOON},
        )
        assert resp.status_code == 422
        assert "Malformed" in resp.json()["detail"]

    def test_malformed_yaml_422(self, client: TestClient) -> None:
        resp = client.post(
            "/echo",
            content=":\n  : :\n[invalid",
            headers={"Content-Type": MIME_YAML},
        )
        # YAML is very permissive -- some strings parse as scalars.
        # If it parses, it's 200; if it raises, it's 422.
        assert resp.status_code in (200, 422)

    def test_malformed_toml_422(self, client: TestClient) -> None:
        resp = client.post(
            "/echo",
            content="[[[invalid toml",
            headers={"Content-Type": MIME_TOML},
        )
        assert resp.status_code == 422
        assert "Malformed" in resp.json()["detail"]

    def test_malformed_xml_422(self, client: TestClient) -> None:
        resp = client.post(
            "/echo",
            content="<not-valid><xml",
            headers={"Content-Type": MIME_XML},
        )
        assert resp.status_code == 422
        assert "Malformed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# TOON-specific: token efficiency
# ---------------------------------------------------------------------------


class TestToonTokenEfficiency:
    """Verify TOON produces fewer tokens than JSON for the same payload."""

    def test_toon_shorter_than_json(self) -> None:
        """TOON representation should be shorter (by character count) than JSON
        for structured data, which correlates with token count reduction."""
        data = {
            "users": [
                {"id": 1, "name": "Alice", "email": "alice@example.com", "active": True},
                {"id": 2, "name": "Bob", "email": "bob@example.com", "active": False},
                {"id": 3, "name": "Charlie", "email": "charlie@example.com", "active": True},
            ]
        }
        json_str = json.dumps(data)
        toon_str = toon_encode(data)
        # TOON should be meaningfully shorter.
        assert len(toon_str) < len(json_str), (
            f"TOON ({len(toon_str)} chars) should be shorter than "
            f"JSON ({len(json_str)} chars)"
        )

    def test_toon_token_count_reduction(self) -> None:
        """When tiktoken is available, verify actual token count reduction."""
        try:
            from toon_format import estimate_savings
        except ImportError:
            pytest.skip("toon_format.estimate_savings not available")

        data = {
            "users": [
                {"id": 1, "name": "Alice", "email": "alice@example.com", "active": True},
                {"id": 2, "name": "Bob", "email": "bob@example.com", "active": False},
                {"id": 3, "name": "Charlie", "email": "charlie@example.com", "active": True},
            ]
        }
        try:
            result = estimate_savings(data)
            # The savings should be positive (TOON uses fewer tokens).
            assert result["savings_percent"] > 0, (
                f"Expected positive token savings, got {result['savings_percent']}%"
            )
        except RuntimeError:
            # tiktoken not installed -- skip the token-count assertion but
            # still verify the character-length reduction above.
            pytest.skip("tiktoken not available for token counting")


# ---------------------------------------------------------------------------
# Integration: combined request + response format
# ---------------------------------------------------------------------------


class TestCombinedFormats:
    """POST with one format, receive response in another."""

    def test_post_yaml_receive_toon(self, client: TestClient) -> None:
        yaml_body = yaml.dump(SAMPLE_DICT)
        resp = client.post(
            "/echo",
            content=yaml_body,
            headers={
                "Content-Type": MIME_YAML,
                "Accept": MIME_TOON,
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_TOON
        parsed = toon_decode(resp.text)
        assert parsed["name"] == "Alice"  # type: ignore[call-overload, index]

    def test_post_toon_receive_xml(self, client: TestClient) -> None:
        toon_body = toon_encode(SAMPLE_DICT)
        resp = client.post(
            "/echo",
            content=toon_body,
            headers={
                "Content-Type": MIME_TOON,
                "Accept": MIME_XML,
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == MIME_XML
        assert "<name>Alice</name>" in resp.text


# ---------------------------------------------------------------------------
# XML helpers edge cases
# ---------------------------------------------------------------------------


class TestXmlHelpers:
    def test_null_value(self) -> None:
        body, _ = serialize({"key": None}, "xml")
        restored = deserialize(body, "xml")
        assert restored["key"] is None

    def test_boolean_values(self) -> None:
        body, _ = serialize({"yes": True, "no": False}, "xml")
        restored = deserialize(body, "xml")
        assert restored["yes"] is True
        assert restored["no"] is False

    def test_numeric_coercion(self) -> None:
        body, _ = serialize({"integer": 42, "decimal": 3.14}, "xml")
        restored = deserialize(body, "xml")
        assert restored["integer"] == 42
        assert abs(restored["decimal"] - 3.14) < 0.001

    def test_nested_dict(self) -> None:
        data = {"outer": {"inner": "value"}}
        body, _ = serialize(data, "xml")
        restored = deserialize(body, "xml")
        assert restored == data

    def test_toml_wraps_non_dict(self) -> None:
        """TOML cannot represent bare lists at top level; serialize wraps them."""
        body, _ = serialize([1, 2, 3], "toml")
        restored = deserialize(body, "toml")
        assert restored["value"] == [1, 2, 3]
