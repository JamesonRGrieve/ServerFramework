"""Tests for ``extensions.SchemaDrift`` (Item 11).

Pure utility tests of the structural diff. Tagged ``@pytest.mark.unit``
since the module under test exercises no BLL/extension surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extensions.SchemaDrift import (
    DiffEntry,
    compare_response_snapshots,
    diff_openapi,
    is_breaking,
    record_response_snapshot,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# DiffEntry / is_breaking
# ---------------------------------------------------------------------------


class TestDiffEntry:
    def test_to_dict_round_trip(self):
        e = DiffEntry(kind="breaking", path="components.schemas.User", detail="removed")
        assert e.to_dict() == {
            "kind": "breaking",
            "path": "components.schemas.User",
            "detail": "removed",
        }


class TestIsBreaking:
    def test_no_diff(self):
        assert is_breaking([]) is False

    def test_only_non_breaking(self):
        diff = [DiffEntry(kind="non_breaking", path="x", detail="added")]
        assert is_breaking(diff) is False

    def test_with_breaking(self):
        diff = [
            DiffEntry(kind="non_breaking", path="x", detail="added"),
            DiffEntry(kind="breaking", path="y", detail="removed"),
        ]
        assert is_breaking(diff) is True


# ---------------------------------------------------------------------------
# diff_openapi — structural OpenAPI diff
# ---------------------------------------------------------------------------


def _write_spec(tmp_path: Path, name: str, spec: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(spec))
    return p


class TestDiffOpenAPI:
    def test_no_committed_snapshot_returns_baseline_marker(self, tmp_path: Path):
        result = diff_openapi(tmp_path / "missing.json", {"paths": {}})
        assert len(result) == 1
        assert result[0].kind == "non_breaking"
        assert "no committed snapshot" in result[0].detail

    def test_identical_specs_have_no_diff(self, tmp_path: Path):
        spec = {
            "paths": {"/x": {"get": {"summary": "x"}}},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    }
                }
            },
        }
        p = _write_spec(tmp_path, "spec.json", spec)
        result = diff_openapi(p, spec)
        assert result == []

    def test_removed_field_is_breaking(self, tmp_path: Path):
        committed = {
            "paths": {},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "email": {"type": "string"},
                        },
                    }
                }
            },
        }
        live = {
            "paths": {},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    }
                }
            },
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert is_breaking(result)
        assert any(
            e.kind == "breaking"
            and "email" in e.path
            and "removed" in e.detail
            for e in result
        )

    def test_added_field_is_non_breaking(self, tmp_path: Path):
        committed = {
            "paths": {},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    }
                }
            },
        }
        live = {
            "paths": {},
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                        },
                    }
                }
            },
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert not is_breaking(result)
        assert any(
            e.kind == "non_breaking"
            and "name" in e.path
            and "added" in e.detail
            for e in result
        )

    def test_narrowed_type_is_breaking(self, tmp_path: Path):
        committed = {
            "components": {"schemas": {"X": {"type": "string"}}},
        }
        live = {
            "components": {"schemas": {"X": {"type": "integer"}}},
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert is_breaking(result)
        assert any(
            e.kind == "breaking" and "narrowed" in e.detail for e in result
        )

    def test_widened_type_is_non_breaking(self, tmp_path: Path):
        committed = {
            "components": {"schemas": {"X": {"type": "integer"}}},
        }
        live = {
            "components": {"schemas": {"X": {"type": "number"}}},
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert not is_breaking(result)
        assert any(
            e.kind == "non_breaking" and "widened" in e.detail for e in result
        )

    def test_removed_enum_value_is_breaking(self, tmp_path: Path):
        committed = {
            "components": {
                "schemas": {
                    "Status": {
                        "type": "string",
                        "enum": ["active", "pending", "closed"],
                    }
                }
            },
        }
        live = {
            "components": {
                "schemas": {
                    "Status": {"type": "string", "enum": ["active", "closed"]}
                }
            },
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert is_breaking(result)
        assert any(
            e.kind == "breaking"
            and "pending" in e.detail
            and "removed" in e.detail
            for e in result
        )

    def test_added_enum_value_is_non_breaking(self, tmp_path: Path):
        committed = {
            "components": {
                "schemas": {
                    "Status": {"type": "string", "enum": ["active", "closed"]}
                }
            },
        }
        live = {
            "components": {
                "schemas": {
                    "Status": {
                        "type": "string",
                        "enum": ["active", "closed", "expired"],
                    }
                }
            },
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert not is_breaking(result)

    def test_removed_path_is_breaking(self, tmp_path: Path):
        committed = {"paths": {"/users": {"get": {}}}}
        live = {"paths": {}}
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert is_breaking(result)

    def test_added_path_is_non_breaking(self, tmp_path: Path):
        committed = {"paths": {}}
        live = {"paths": {"/users": {"get": {}}}}
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert not is_breaking(result)

    def test_newly_required_field_is_breaking(self, tmp_path: Path):
        committed = {
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "email": {"type": "string"},
                        },
                        "required": ["id"],
                    }
                }
            },
        }
        live = {
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "email": {"type": "string"},
                        },
                        "required": ["id", "email"],
                    }
                }
            },
        }
        p = _write_spec(tmp_path, "spec.json", committed)
        result = diff_openapi(p, live)
        assert is_breaking(result)
        assert any("newly required" in e.detail for e in result)


# ---------------------------------------------------------------------------
# Response snapshot helpers
# ---------------------------------------------------------------------------


class TestRecordResponseSnapshot:
    def test_strips_default_fields(self):
        live = {
            "id": "abc",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "name": "Alice",
        }
        out = record_response_snapshot(live)
        assert out["id"] == "<normalized>"
        assert out["created_at"] == "<normalized>"
        assert out["updated_at"] == "<normalized>"
        assert out["name"] == "Alice"

    def test_strips_at_depth(self):
        live = {
            "user": {"id": "u1", "name": "n"},
            "items": [{"id": "i1", "value": 5}, {"id": "i2", "value": 7}],
        }
        out = record_response_snapshot(live)
        assert out["user"]["id"] == "<normalized>"
        assert out["user"]["name"] == "n"
        assert out["items"][0]["id"] == "<normalized>"
        assert out["items"][0]["value"] == 5
        assert out["items"][1]["id"] == "<normalized>"

    def test_custom_normalize_list(self):
        live = {"x": "remove me", "y": "keep me"}
        out = record_response_snapshot(live, normalize=["x"])
        assert out["x"] == "<normalized>"
        assert out["y"] == "keep me"

    def test_empty_normalize_list_keeps_everything(self):
        live = {"id": "a", "value": 1}
        out = record_response_snapshot(live, normalize=[])
        assert out == {"id": "a", "value": 1}


class TestCompareResponseSnapshots:
    def test_identical_no_diff(self):
        snap = {"a": 1, "b": {"c": 2}}
        assert compare_response_snapshots(snap, snap) == []

    def test_removed_field_breaking(self):
        committed = {"a": 1, "b": 2}
        live = {"a": 1}
        diff = compare_response_snapshots(committed, live)
        assert is_breaking(diff)
        assert any(e.path == "b" and "removed" in e.detail for e in diff)

    def test_added_field_non_breaking(self):
        committed = {"a": 1}
        live = {"a": 1, "b": 2}
        diff = compare_response_snapshots(committed, live)
        assert not is_breaking(diff)
        assert any(e.path == "b" and "added" in e.detail for e in diff)

    def test_type_mismatch_breaking(self):
        committed = {"a": "1"}
        live = {"a": 1}
        diff = compare_response_snapshots(committed, live)
        assert is_breaking(diff)

    def test_nested_list_first_element(self):
        committed = {"items": [{"a": 1, "b": 2}]}
        live = {"items": [{"a": 1}]}
        diff = compare_response_snapshots(committed, live)
        assert is_breaking(diff)
        assert any("items[0].b" in e.path for e in diff)
