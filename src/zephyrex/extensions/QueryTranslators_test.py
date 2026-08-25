"""End-to-end tests for the query DSL translators."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from zephyrex.extensions.ExternalErrors import UnsupportedOperatorError
from zephyrex.extensions.QueryTranslators import (
    GraphQLFilterTranslator,
    IMAPSearchTranslator,
    KeyValueTranslator,
    MongoStyleTranslator,
    SOQLTranslator,
    StripeSearchTranslator,
)


class _SearchModel(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    age: int | None = None


class TestStripeSearchTranslator:
    def test_simple_exact(self):
        t = StripeSearchTranslator()
        out = t.translate({"name": "Premium", "is_active": True})
        # Stripe is "AND" between clauses.
        assert "name:Premium" in out
        assert "is_active:true" in out
        assert " AND " in out

    def test_quoted_when_value_has_space(self):
        t = StripeSearchTranslator()
        out = t.translate({"name": "Pro Plus"})
        assert out == 'name:"Pro Plus"'

    def test_unsupported_operator_raises(self):
        t = StripeSearchTranslator()
        with pytest.raises(UnsupportedOperatorError):
            t.translate({"name": {"inc": "Premium"}})


class TestKeyValueTranslator:
    def test_simple(self):
        t = KeyValueTranslator()
        assert t.translate({"q": "x", "id": 1}) == {"q": "x", "id": 1}

    def test_unsupported_op(self):
        t = KeyValueTranslator()
        with pytest.raises(UnsupportedOperatorError):
            t.translate({"q": {"gt": 5}})


class TestMongoStyleTranslator:
    def test_exact_collapses(self):
        t = MongoStyleTranslator()
        assert t.translate({"name": "Pro"}) == {"name": "Pro"}

    def test_operators(self):
        t = MongoStyleTranslator()
        out = t.translate({"age": {"gt": 18, "lt": 65}})
        assert out == {"age": {"$gt": 18, "$lt": 65}}

    def test_in_operator(self):
        t = MongoStyleTranslator()
        out = t.translate({"status": {"in_": ["a", "b"]}})
        assert out == {"status": {"$in": ["a", "b"]}}


class TestSOQLTranslator:
    def test_exact_and_inc(self):
        t = SOQLTranslator()
        out = t.translate({"Name": {"inc": "Acme"}, "AnnualRevenue": {"gt": 1_000_000}})
        # Both clauses should appear AND-joined.
        assert "Name LIKE '%Acme%'" in out
        assert "AnnualRevenue > 1000000" in out

    def test_in_emission(self):
        t = SOQLTranslator()
        out = t.translate({"Stage": {"in_": ["A", "B"]}})
        assert out == "Stage IN ('A', 'B')"

    def test_unsupported_op(self):
        t = SOQLTranslator()
        # `between` is unsupported by SOQLTranslator.
        with pytest.raises(UnsupportedOperatorError):
            t.translate({"x": {"between": (1, 5)}})


class TestGraphQLFilterTranslator:
    def test_simple_eq(self):
        t = GraphQLFilterTranslator()
        out = t.translate({"name": "Pro"})
        assert out == {"name": {"_eq": "Pro"}}

    def test_inc_becomes_ilike(self):
        t = GraphQLFilterTranslator()
        out = t.translate({"name": {"inc": "Pro"}})
        assert out == {"name": {"_ilike": "%Pro%"}}

    def test_starts_with(self):
        t = GraphQLFilterTranslator()
        out = t.translate({"name": {"starts_with": "Pro"}})
        assert out == {"name": {"_ilike": "Pro%"}}


class TestPydanticIntegration:
    def test_translates_pydantic_model(self):
        t = MongoStyleTranslator()
        m = _SearchModel(name="Premium", is_active=True)
        out = t.translate(m)
        assert out == {"name": "Premium", "is_active": True}


class TestIMAPSearchTranslator:
    def test_from_maps_to_imap_search(self):
        # The acceptance example: {"from": "alice"} -> SEARCH FROM alice.
        assert IMAPSearchTranslator().translate({"from": "alice"}) == "FROM alice"

    def test_multiple_fields_are_anded_by_juxtaposition(self):
        out = IMAPSearchTranslator().translate({"from": "alice", "subject": "hello"})
        assert out == "FROM alice SUBJECT hello"

    def test_value_with_space_is_quoted(self):
        out = IMAPSearchTranslator().translate({"subject": "quarterly report"})
        assert out == 'SUBJECT "quarterly report"'

    def test_contains_and_exact_both_emit_plain_key(self):
        t = IMAPSearchTranslator()
        assert t.translate({"subject": {"inc": "invoice"}}) == "SUBJECT invoice"
        assert t.translate({"subject": "invoice"}) == "SUBJECT invoice"

    def test_neq_wraps_criterion_in_not(self):
        assert (
            IMAPSearchTranslator().translate({"from": {"neq": "bob"}}) == "NOT FROM bob"
        )

    def test_unknown_field_becomes_header_criterion(self):
        assert (
            IMAPSearchTranslator().translate({"x-priority": "1"})
            == "HEADER x-priority 1"
        )

    def test_date_operators_map_to_since_before_on(self):
        from datetime import datetime

        t = IMAPSearchTranslator()
        d = datetime(2020, 1, 5)
        assert t.translate({"date": {"gte": d}}) == "SINCE 5-Jan-2020"
        assert t.translate({"date": {"lt": d}}) == "BEFORE 5-Jan-2020"
        assert t.translate({"date": d}) == "ON 5-Jan-2020"

    def test_size_operators_map_to_larger_smaller(self):
        t = IMAPSearchTranslator()
        assert t.translate({"size": {"gt": 1024}}) == "LARGER 1024"
        assert t.translate({"size": {"lte": 2048}}) == "SMALLER 2048"

    def test_unsupported_operator_raises(self):
        with pytest.raises(UnsupportedOperatorError):
            IMAPSearchTranslator().translate({"from": {"starts_with": "a"}})
