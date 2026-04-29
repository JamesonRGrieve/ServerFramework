"""Tests for migration ownership detection (Item 24)."""

from __future__ import annotations

from types import SimpleNamespace

from extensions.MigrationOwnership import (
    env_is_table_owned_by_extension,
    list_table_ownership,
    print_ownership,
)


def _table_with_info(extension=None):
    """Build a minimal duck-typed table object."""
    return SimpleNamespace(
        info={"extension": extension} if extension else {},
        class_=None,
    )


def test_info_dict_takes_precedence():
    table = _table_with_info(extension="payment")
    assert env_is_table_owned_by_extension(table) == "payment"


def test_no_info_no_class_returns_none():
    table = _table_with_info()
    assert env_is_table_owned_by_extension(table) is None


def test_file_path_inferred_from_module():
    class FakeModel:
        __module__ = "extensions.payment.BLL_Payment"

    table = SimpleNamespace(info={}, class_=FakeModel)
    assert env_is_table_owned_by_extension(table) == "payment"


def test_file_path_inferred_from_synthesized_name():
    """ExtensionLoader-loaded modules use the legacy
    ``extensions.<name>.<file>`` __name__, so the regex must match it."""
    class FakeModel:
        __module__ = "extensions.auth_mfa.BLL_AuthMFA"

    table = SimpleNamespace(info={}, class_=FakeModel)
    assert env_is_table_owned_by_extension(table) == "auth_mfa"


def test_core_module_returns_none():
    class FakeModel:
        __module__ = "logic.BLL_Auth"

    table = SimpleNamespace(info={}, class_=FakeModel)
    assert env_is_table_owned_by_extension(table) is None


def test_list_table_ownership():
    class CoreModel:
        __module__ = "database.DB_Core"

    class ExtModel:
        __module__ = "extensions.payment.BLL_Payment"

    metadata = SimpleNamespace(
        tables={
            "users": SimpleNamespace(info={}, class_=CoreModel),
            "subscriptions": SimpleNamespace(info={}, class_=ExtModel),
        }
    )
    result = list_table_ownership(metadata)
    assert result == {"users": None, "subscriptions": "payment"}


def test_print_ownership_renders_string(capsys):
    rendered = print_ownership({"users": None, "subscriptions": "payment"})
    captured = capsys.readouterr().out
    assert "users" in rendered
    assert "subscriptions" in rendered
    assert "<core>" in rendered
    assert "payment" in rendered
    assert rendered in captured
