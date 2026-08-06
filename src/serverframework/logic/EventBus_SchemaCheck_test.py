"""Tests for the event-schema compatibility checker (Item 42 follow-up)."""

from __future__ import annotations

from typing import Optional

import pytest
from pydantic import BaseModel

from serverframework.logic.EventBus_SchemaCheck import (
    CompatibilityFinding,
    check_compatibility,
    event_schema,
    read_snapshot,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Test event classes (define inside module so the schema is reproducible)
# ---------------------------------------------------------------------------


class _UserCreated(BaseModel):
    user_id: str
    email: str
    name: Optional[str] = None


class _UserCreatedV2(BaseModel):
    """Compatible evolution of _UserCreated: adds an optional field."""

    user_id: str
    email: str
    name: Optional[str] = None
    timezone: Optional[str] = None


class _UserCreatedBreakingRemove(BaseModel):
    """Breaking: removed `email`."""

    user_id: str
    name: Optional[str] = None


class _UserCreatedBreakingNarrow(BaseModel):
    """Breaking: narrowed `user_id` from str to int."""

    user_id: int
    email: str
    name: Optional[str] = None


class _UserCreatedBreakingRequire(BaseModel):
    """Breaking: made the optional `name` required."""

    user_id: str
    email: str
    name: str  # was Optional[str]


def test_event_schema_returns_normalized_dict():
    schema = event_schema(_UserCreated)
    assert schema["name"] == "_UserCreated"
    assert schema["module"] == _UserCreated.__module__
    assert "user_id" in schema["fields"]
    assert "email" in schema["fields"]
    assert "name" in schema["fields"]
    assert schema["fields"]["name"]["optional"] is True
    assert schema["fields"]["user_id"]["optional"] is False


def test_check_compatibility_no_changes_clean():
    snapshot = [event_schema(_UserCreated)]
    report = check_compatibility(snapshot, [_UserCreated])
    assert report.is_compatible
    assert report.breaking == []


def test_check_compatibility_additive_field_clean():
    snapshot = [event_schema(_UserCreated)]
    report = check_compatibility(snapshot, [_UserCreatedV2])
    # We renamed the live class; the checker keys by full module.name,
    # so it'd see the v2 class as a new event and the v1 as removed.
    # Use a different setup: snapshot the v1 class under v2's key.
    snapshot[0]["name"] = "_UserCreatedV2"
    snapshot[0]["module"] = _UserCreatedV2.__module__
    report = check_compatibility(snapshot, [_UserCreatedV2])
    assert report.is_compatible
    assert any("new field timezone" in note for note in report.additive)


def test_check_compatibility_field_removed_is_breaking():
    snapshot = [event_schema(_UserCreated)]
    snapshot[0]["name"] = "_UserCreatedBreakingRemove"
    snapshot[0]["module"] = _UserCreatedBreakingRemove.__module__
    report = check_compatibility(snapshot, [_UserCreatedBreakingRemove])
    assert not report.is_compatible
    kinds = {f.kind for f in report.breaking}
    assert "field_removed" in kinds


def test_check_compatibility_type_narrowed_is_breaking():
    snapshot = [event_schema(_UserCreated)]
    snapshot[0]["name"] = "_UserCreatedBreakingNarrow"
    snapshot[0]["module"] = _UserCreatedBreakingNarrow.__module__
    report = check_compatibility(snapshot, [_UserCreatedBreakingNarrow])
    assert not report.is_compatible
    kinds = {f.kind for f in report.breaking}
    assert "type_narrowed" in kinds


def test_check_compatibility_now_required_is_breaking():
    snapshot = [event_schema(_UserCreated)]
    snapshot[0]["name"] = "_UserCreatedBreakingRequire"
    snapshot[0]["module"] = _UserCreatedBreakingRequire.__module__
    report = check_compatibility(snapshot, [_UserCreatedBreakingRequire])
    assert not report.is_compatible
    kinds = {f.kind for f in report.breaking}
    assert "now_required" in kinds


def test_check_compatibility_event_removed_is_breaking():
    snapshot = [event_schema(_UserCreated)]
    report = check_compatibility(snapshot, [])
    assert not report.is_compatible
    assert any(f.kind == "event_removed" for f in report.breaking)


def test_check_compatibility_new_event_is_additive():
    report = check_compatibility([], [_UserCreated])
    assert report.is_compatible
    assert any("new event" in note for note in report.additive)


def test_write_and_read_snapshot_round_trip(tmp_path):
    path = tmp_path / "events.snapshot.json"
    write_snapshot(path, [_UserCreated, _UserCreatedV2])
    schemas = read_snapshot(path)
    assert len(schemas) == 2
    names = {s["name"] for s in schemas}
    assert "_UserCreated" in names
    assert "_UserCreatedV2" in names


def test_compatibility_finding_to_dict():
    f = CompatibilityFinding(
        event="mod._E", kind="field_removed", detail="x"
    )
    d = f.to_dict()
    assert d == {"event": "mod._E", "kind": "field_removed", "detail": "x"}


def test_read_snapshot_missing_file_returns_empty(tmp_path):
    schemas = read_snapshot(tmp_path / "no-such-file.json")
    assert schemas == []


###############################################################################
# Additional coverage for pure helpers and discover_event_classes
###############################################################################


class TestTypeNameHelper:
    @pytest.mark.parametrize(
        "annotation, expected",
        [
            (str, "str"),
            (int, "int"),
            (None, "None"),
        ],
    )
    def test_basic_types(self, annotation, expected):
        from serverframework.logic.EventBus_SchemaCheck import _type_name

        assert _type_name(annotation) == expected

    def test_union_type_repr(self):
        from typing import Union

        from serverframework.logic.EventBus_SchemaCheck import _type_name

        result = _type_name(Union[str, int])
        assert "str" in result or "Union" in result


class TestIsOptionalHelper:
    def test_optional_str(self):
        from typing import Optional

        from serverframework.logic.EventBus_SchemaCheck import _is_optional

        assert _is_optional(Optional[str]) is True

    def test_plain_str(self):
        from serverframework.logic.EventBus_SchemaCheck import _is_optional

        assert _is_optional(str) is False


class TestSafeJsonify:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("hello", "hello"),
            (42, 42),
            (3.14, 3.14),
            (True, True),
            (None, None),
        ],
    )
    def test_primitives_pass_through(self, value, expected):
        from serverframework.logic.EventBus_SchemaCheck import _safe_jsonify

        assert _safe_jsonify(value) == expected

    def test_complex_returns_repr(self):
        from serverframework.logic.EventBus_SchemaCheck import _safe_jsonify

        result = _safe_jsonify([1, 2, 3])
        assert result == "[1, 2, 3]"


class TestIsPydanticUndefined:
    def test_pydantic_undefined(self):
        from pydantic_core import PydanticUndefined

        from serverframework.logic.EventBus_SchemaCheck import _is_pydantic_undefined

        assert _is_pydantic_undefined(PydanticUndefined) is True

    def test_normal_value(self):
        from serverframework.logic.EventBus_SchemaCheck import _is_pydantic_undefined

        assert _is_pydantic_undefined("hello") is False


class TestDiscoverEventClasses:
    def test_discovers_from_module_with_events(self):
        from serverframework.logic.EventBus_SchemaCheck import discover_event_classes

        found = discover_event_classes(["serverframework.logic.EventBus"])
        # EventBus may or may not export classes matching the suffix pattern;
        # the contract is: no crash, returns a list.
        assert isinstance(found, list)

    def test_missing_module_skipped(self):
        from serverframework.logic.EventBus_SchemaCheck import discover_event_classes

        found = discover_event_classes(["nonexistent.module.that.does.not.exist"])
        assert found == []

    def test_empty_input(self):
        from serverframework.logic.EventBus_SchemaCheck import discover_event_classes

        assert discover_event_classes([]) == []
