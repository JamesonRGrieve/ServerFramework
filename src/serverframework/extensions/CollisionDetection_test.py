"""Tests for extension field collision detection (Item 23)."""

from __future__ import annotations

import pytest
from pydantic import Field
from pydantic.fields import FieldInfo

from serverframework.extensions.CollisionDetection import (
    FieldCollisionError,
    detect_extension_field_collisions,
    get_registered_fields,
    register_extension_field,
    reset_registry,
)


def setup_function(_):
    reset_registry()


def teardown_function(_):
    reset_registry()


def _info(default=None, description="x", annotation=str) -> FieldInfo:
    info = FieldInfo(default=default, description=description)
    info.annotation = annotation
    return info


def test_no_collision_no_error():
    register_extension_field(
        extension_name="payment",
        model_name="UserModel",
        field_name="external_payment_id",
        field_info=_info(),
        source_file="ext/payment/BLL.py",
        source_line=42,
    )
    detect_extension_field_collisions()  # no raise


def test_identical_duplicate_accepted():
    info1 = _info(default=None, description="external", annotation=str)
    info2 = _info(default=None, description="external", annotation=str)
    register_extension_field(
        "payment", "UserModel", "external_payment_id", info1, "a.py", 1
    )
    register_extension_field(
        "billing", "UserModel", "external_payment_id", info2, "b.py", 2
    )
    # Identical FieldInfo equality: Pydantic v2 FieldInfo compares value.
    detect_extension_field_collisions()


def test_conflict_raises_with_both_sources():
    info1 = _info(default=None, description="A", annotation=str)
    info2 = _info(default=None, description="B", annotation=int)
    register_extension_field(
        "payment", "UserModel", "external_payment_id", info1,
        "src/extensions/payment/BLL_Payment.py", 42,
    )
    register_extension_field(
        "legacy_billing", "UserModel", "external_payment_id", info2,
        "src/extensions/legacy_billing/BLL_LegacyBilling.py", 99,
    )
    with pytest.raises(FieldCollisionError) as exc:
        detect_extension_field_collisions()
    msg = str(exc.value)
    assert "UserModel" in msg
    assert "external_payment_id" in msg
    assert "payment" in msg
    assert "legacy_billing" in msg
    assert "42" in msg or "99" in msg  # at least one line number reported


def test_legacy_origins_dict_layered_in():
    # No registered detailed entries; only legacy mapping says two
    # extensions touched the same field. Detection must still report.
    with pytest.raises(FieldCollisionError):
        detect_extension_field_collisions(
            extension_field_origins={
                ("UserModel", "external_payment_id"): ["payment", "legacy_billing"],
            },
        )


def test_legacy_origins_single_source_no_error():
    detect_extension_field_collisions(
        extension_field_origins={
            ("UserModel", "external_payment_id"): ["payment"],
        }
    )


def test_get_registered_fields_returns_copy():
    register_extension_field(
        "payment", "UserModel", "field1", _info(), "a.py", 1
    )
    snapshot = get_registered_fields()
    assert ("UserModel", "field1") in snapshot
    snapshot.clear()
    # Underlying registry untouched.
    assert ("UserModel", "field1") in get_registered_fields()
