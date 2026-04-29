"""Tests for ``lib.PII`` (Item 82).

These are pure-utility tests of a classification + orchestration module.
They are tagged ``@pytest.mark.unit`` per AGENTS.md: the module under test
has no BLL/extension surface — it's data-class introspection plus a small
in-memory registry — so the tests use no mocks at all.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from lib.PII import (
    DataExporter,
    ERASURE_EVENT_AUDIT_CLASS,
    ErasureOrchestrator,
    ErasureReport,
    PIIAnnotation,
    PIIClass,
    REDACTION_SENTINEL,
    enumerate_pii,
    pii,
    redact_pii,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# pii() helper
# ---------------------------------------------------------------------------


class TestPiiHelper:
    def test_returns_annotation_with_class(self):
        marker = pii(PIIClass.DIRECT_IDENTIFIER)
        assert isinstance(marker, PIIAnnotation)
        assert marker.klass == PIIClass.DIRECT_IDENTIFIER

    def test_rejects_non_class(self):
        with pytest.raises(TypeError):
            pii("direct_identifier")  # type: ignore[arg-type]

    def test_enum_string_value(self):
        # The enum is a (str, Enum) for serialization; verify the string value.
        assert PIIClass.DIRECT_IDENTIFIER.value == "direct_identifier"
        assert PIIClass.PSEUDONYMOUS.value == "pseudonymous"
        assert PIIClass.SENSITIVE.value == "sensitive"
        assert PIIClass.DERIVED.value == "derived"
        assert PIIClass.NONE.value == "none"


# ---------------------------------------------------------------------------
# Sample model used across enumerate/redact tests
# ---------------------------------------------------------------------------


class SampleUser(BaseModel):
    """Mixed-classification model exercising every PIIClass tier."""

    id: str  # NONE (default; not enumerated)
    email: Annotated[str, pii(PIIClass.DIRECT_IDENTIFIER)]
    phone: Annotated[str, pii(PIIClass.DIRECT_IDENTIFIER)]
    session_key: Annotated[str, pii(PIIClass.PSEUDONYMOUS)]
    ssn: Annotated[str, pii(PIIClass.SENSITIVE)]
    embedding_summary: Annotated[str, pii(PIIClass.DERIVED)]
    public_label: str  # NONE


class TestEnumeratePii:
    def test_enumerates_all_classified(self):
        result = enumerate_pii(SampleUser)
        names = [n for n, _ in result]
        assert "id" not in names
        assert "public_label" not in names
        assert names == ["email", "phone", "session_key", "ssn", "embedding_summary"]

    def test_classifications_match_declarations(self):
        result = dict(enumerate_pii(SampleUser))
        assert result["email"] == PIIClass.DIRECT_IDENTIFIER
        assert result["phone"] == PIIClass.DIRECT_IDENTIFIER
        assert result["session_key"] == PIIClass.PSEUDONYMOUS
        assert result["ssn"] == PIIClass.SENSITIVE
        assert result["embedding_summary"] == PIIClass.DERIVED

    def test_unannotated_model_returns_empty(self):
        class Plain(BaseModel):
            a: int
            b: str

        assert enumerate_pii(Plain) == []

    def test_rejects_non_pydantic(self):
        class NotAModel:
            pass

        with pytest.raises(TypeError):
            enumerate_pii(NotAModel)  # type: ignore[arg-type]

    def test_field_metadata_form_works(self):
        # Item 82 spec lists Field(..., metadata=[pii(...)]) as a supported form.
        class WithFieldMetadata(BaseModel):
            email: str = Field(..., metadata=[pii(PIIClass.DIRECT_IDENTIFIER)])
            note: str = "x"

        result = dict(enumerate_pii(WithFieldMetadata))
        assert result.get("email") == PIIClass.DIRECT_IDENTIFIER
        assert "note" not in result


# ---------------------------------------------------------------------------
# redact_pii
# ---------------------------------------------------------------------------


class TestRedactPii:
    def test_redacts_direct_identifiers(self):
        u = SampleUser(
            id="u1",
            email="alice@example.com",
            phone="+15551234",
            session_key="sk_abc",
            ssn="123-45-6789",
            embedding_summary="vec",
            public_label="member",
        )
        out = redact_pii(u)
        assert out["email"] == REDACTION_SENTINEL
        assert out["phone"] == REDACTION_SENTINEL
        assert out["ssn"] == REDACTION_SENTINEL

    def test_keeps_pseudonymous_and_derived(self):
        u = SampleUser(
            id="u1",
            email="x@y.z",
            phone="0",
            session_key="sk_keep",
            ssn="0",
            embedding_summary="kept",
            public_label="L",
        )
        out = redact_pii(u)
        assert out["session_key"] == "sk_keep"
        assert out["embedding_summary"] == "kept"

    def test_keeps_unclassified(self):
        u = SampleUser(
            id="u1",
            email="x@y.z",
            phone="0",
            session_key="0",
            ssn="0",
            embedding_summary="0",
            public_label="kept",
        )
        out = redact_pii(u)
        assert out["id"] == "u1"
        assert out["public_label"] == "kept"

    def test_rejects_non_instance(self):
        with pytest.raises(TypeError):
            redact_pii({"email": "x"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ErasureOrchestrator
# ---------------------------------------------------------------------------


class TestErasureOrchestrator:
    def test_runs_in_reverse_registration_order(self):
        order: list = []

        def make_eraser(name: str):
            def fn(user_id: str) -> None:
                order.append(name)

            return fn

        orch = ErasureOrchestrator()
        # Register in forward dependency order: A -> B -> C
        orch.register_extension_eraser("A", make_eraser("A"))
        orch.register_extension_eraser("B", make_eraser("B"))
        orch.register_extension_eraser("C", make_eraser("C"))

        report = orch.erase_user("user-1")
        assert order == ["C", "B", "A"]
        assert report.succeeded
        assert report.erased_extensions == ["C", "B", "A"]

    def test_idempotent(self):
        # Re-running should call the erasers again without raising.
        calls: list = []

        def fn(user_id: str) -> None:
            calls.append(user_id)

        orch = ErasureOrchestrator()
        orch.register_extension_eraser("ext", fn)

        r1 = orch.erase_user("u")
        r2 = orch.erase_user("u")
        assert r1.succeeded and r2.succeeded
        assert calls == ["u", "u"]

    def test_partial_failure_recorded(self):
        def good(user_id: str) -> None:
            return None

        def bad(user_id: str) -> None:
            raise RuntimeError("downstream gone")

        orch = ErasureOrchestrator()
        orch.register_extension_eraser("good", good)
        orch.register_extension_eraser("bad", bad)

        report = orch.erase_user("u-2")
        assert not report.succeeded
        assert "bad" in report.failed_extensions
        assert "downstream gone" in report.failed_extensions["bad"]
        # The good eraser still ran (note: reverse order means bad runs first).
        assert report.erased_extensions == ["good"]

    def test_report_to_dict(self):
        orch = ErasureOrchestrator()
        orch.register_extension_eraser("ext", lambda uid: None)
        rep = orch.erase_user("u-3")
        d = rep.to_dict()
        assert d["user_id"] == "u-3"
        assert d["succeeded"] is True
        assert d["audit_event_id"] == "erasure:u-3"

    def test_register_rejects_non_callable(self):
        orch = ErasureOrchestrator()
        with pytest.raises(TypeError):
            orch.register_extension_eraser("ext", "not callable")  # type: ignore[arg-type]

    def test_audit_class_outline(self):
        # Item 82: erasure_event class is pii_redactable=False.
        assert ERASURE_EVENT_AUDIT_CLASS["pii_redactable"] is False
        assert ERASURE_EVENT_AUDIT_CLASS["retention"] == "forever"
        assert ERASURE_EVENT_AUDIT_CLASS["name"] == "erasure_event"


# ---------------------------------------------------------------------------
# DataExporter
# ---------------------------------------------------------------------------


class TestDataExporter:
    def test_collects_per_extension_outputs(self):
        exp = DataExporter()
        exp.register_extension_exporter("auth", lambda uid: {"email": f"{uid}@x.y"})
        exp.register_extension_exporter("payment", lambda uid: {"plan": "free"})

        out = exp.export_user("u-1")
        assert out == {
            "auth": {"email": "u-1@x.y"},
            "payment": {"plan": "free"},
        }

    def test_failure_recorded_per_extension(self):
        exp = DataExporter()
        exp.register_extension_exporter("good", lambda uid: {"k": "v"})

        def bad(uid: str):
            raise RuntimeError("boom")

        exp.register_extension_exporter("bad", bad)

        out = exp.export_user("u-2")
        assert out["good"] == {"k": "v"}
        assert "_error" in out["bad"]
        assert "boom" in out["bad"]["_error"]

    def test_register_rejects_non_callable(self):
        exp = DataExporter()
        with pytest.raises(TypeError):
            exp.register_extension_exporter("x", 42)  # type: ignore[arg-type]
