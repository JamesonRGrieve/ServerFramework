"""Tests for the C-2 SOQL injection fix in ``SOQLTranslator``.

The translator now validates field names against the SOQL identifier
shape (``[A-Za-z][A-Za-z0-9_]*`` per dot-segment) and uses Salesforce-
correct string escaping (``\\\\`` then ``\\'``). LIKE patterns also
escape ``%`` and ``_`` so a substring containing them cannot widen the
match.
"""

from __future__ import annotations

import pytest


def _translator():
    from serverframework.extensions.QueryTranslators import SOQLTranslator

    return SOQLTranslator()


def test_soql_field_name_must_be_identifier():
    """A field name with a space, semicolon, or quote is refused — the
    interpolation site would otherwise be a SOQL injection."""
    t = _translator()
    for bad in (
        "1=1; DROP TABLE Account",
        "Name OR 1=1",
        "Account.Name; --",
        "Name'; --",
    ):
        with pytest.raises(ValueError):
            t.translate({bad: "x"})


def test_soql_field_name_relationship_traversal_allowed():
    """``Account.Name`` is a legitimate SOQL relationship traversal."""
    t = _translator()
    where = t.translate({"Account.Name": "Acme"})
    assert "Account.Name = 'Acme'" in where


def test_soql_string_escapes_quote_and_backslash():
    """A value containing ``'`` is escaped per Salesforce SOQL rules
    (``\\'`` not the generic SQL ``''``). A backslash is doubled
    *first* so the injected ``\\`` of the quote-escape is not itself
    re-escaped."""
    t = _translator()
    where = t.translate({"Name": "Acme's \\test"})
    # Backslash doubled, quote backslash-escaped.
    assert where == "Name = 'Acme\\'s \\\\test'"


def test_soql_like_escapes_wildcards():
    """``%`` and ``_`` in a substring are wildcards in LIKE — without
    escaping, ``%`` widens the match arbitrarily."""
    t = _translator()
    where = t.translate({"Name": {"inc": "100%_off"}})
    # Wildcards inside the user-supplied substring are escaped...
    assert "100\\%\\_off" in where
    # ...while the surrounding ``%`` literals (the contains semantics)
    # remain unescaped at the boundary.
    assert where.startswith("Name LIKE '%")
    assert where.endswith("%'")


def test_soql_like_does_not_widen_with_payload():
    """Concrete attack: a value of ``%' OR Name LIKE '`` MUST be quoted
    so the surrounding LIKE structure is intact and the comment / OR
    cannot escape."""
    t = _translator()
    where = t.translate({"Name": {"inc": "%' OR Name LIKE '"}})
    # The injected sequence remains escaped — the embedded quote becomes
    # ``\\'`` and the embedded ``%`` becomes ``\\%`` so the outer LIKE
    # statement is intact.
    assert "\\%\\' OR Name LIKE \\'" in where


def test_soql_in_clause_quotes_each_value():
    t = _translator()
    where = t.translate({"Name": {"in_": ["a", "b", "c"]}})
    assert where == "Name IN ('a', 'b', 'c')"


def test_soql_numeric_render_unquoted():
    t = _translator()
    where = t.translate({"Amount": {"gte": 100}})
    assert where == "Amount >= 100"


def test_soql_null_handling():
    t = _translator()
    where = t.translate({"Description": {"is_null": True}})
    assert where == "Description = NULL"
    where2 = t.translate({"Description": {"is_null": False}})
    assert where2 == "Description != NULL"
