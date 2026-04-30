"""End-to-end tests for the FieldMapping pipeline."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel

from serverframework.extensions.FieldMappings import (
    CentsToDecimal,
    Compose,
    Custom,
    Decompose,
    DotPath,
    EnumRemap,
    FieldMapping,
    Rename,
    TimestampConvert,
    UnitConvert,
    apply_from_external,
    apply_to_external,
    normalize_mappings,
)


class TestNormalize:
    def test_dict_to_renames(self):
        out = normalize_mappings({"a": "A", "b": "B"})
        assert len(out) == 2
        assert all(isinstance(m, Rename) for m in out)
        assert out[0].internal == "a" and out[0].external == "A"

    def test_list_passthrough(self):
        rules = [Rename("a", "A")]
        assert normalize_mappings(rules) == rules

    def test_none_yields_empty(self):
        assert normalize_mappings(None) == []

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            normalize_mappings("not a list or dict")  # type: ignore[arg-type]


class TestRename:
    def test_round_trip(self):
        mappings = [Rename("name", "full_name")]
        ext = apply_to_external(mappings, {"name": "Alice"})
        assert ext == {"full_name": "Alice"}
        back = apply_from_external(mappings, ext)
        assert back == {"name": "Alice"}


class TestCompose:
    def test_join_first_last(self):
        mappings = [
            Compose(
                externals=["first_name", "last_name"],
                internal="full_name",
                fn=lambda f, l: f"{f} {l}",
                inverse_fn=lambda v: v.split(" ", 1),
            ),
        ]
        ext = apply_to_external(mappings, {"first_name": "Ada", "last_name": "Lovelace"})
        assert ext == {"full_name": "Ada Lovelace"}
        back = apply_from_external(mappings, ext)
        assert back == {"first_name": "Ada", "last_name": "Lovelace"}


class TestDecompose:
    def test_split_into_parts(self):
        mappings = [
            Decompose(
                external="address",
                internals=["street", "city"],
                fn=lambda s: s.split(", ", 1),
                inverse_fn=lambda street, city: f"{street}, {city}",
            ),
        ]
        ext = apply_to_external(mappings, {"street": "1 Main", "city": "Seattle"})
        assert ext == {"address": "1 Main, Seattle"}
        back = apply_from_external(mappings, ext)
        assert back == {"street": "1 Main", "city": "Seattle"}


class TestDotPath:
    def test_to_external_creates_nested(self):
        mappings = [DotPath("street", "address.line1")]
        ext = apply_to_external(mappings, {"street": "1 Main"})
        assert ext == {"address": {"line1": "1 Main"}}

    def test_from_external_reads_nested(self):
        mappings = [DotPath("street", "address.line1")]
        back = apply_from_external(mappings, {"address": {"line1": "1 Main"}})
        assert back == {"street": "1 Main"}


class TestUnitConvert:
    def test_int_factor(self):
        mappings = [UnitConvert("price", "amount", 100)]
        ext = apply_to_external(mappings, {"price": Decimal("5.50")})
        assert ext == {"amount": 550}
        back = apply_from_external(mappings, {"amount": 550})
        assert back["price"] == Decimal("5.50")

    def test_passthrough_none(self):
        mappings = [UnitConvert("price", "amount", 100)]
        ext = apply_to_external(mappings, {"price": None})
        assert ext == {"amount": None}


class TestCentsToDecimal:
    def test_round_trip(self):
        mappings = [CentsToDecimal("price", "amount")]
        ext = apply_to_external(mappings, {"price": Decimal("12.34")})
        assert ext == {"amount": 1234}
        back = apply_from_external(mappings, ext)
        assert back == {"price": Decimal("12.34")}


class TestEnumRemap:
    def test_round_trip(self):
        mappings = [EnumRemap("status", "state", {"active": "A", "inactive": "I"})]
        assert apply_to_external(mappings, {"status": "active"}) == {"state": "A"}
        assert apply_from_external(mappings, {"state": "I"}) == {"status": "inactive"}

    def test_non_unique_raises(self):
        with pytest.raises(ValueError):
            EnumRemap("s", "S", {"a": "X", "b": "X"})


class TestTimestampConvert:
    def test_iso_round_trip(self):
        mappings = [TimestampConvert("created", "created_at")]
        dt = datetime(2026, 4, 28, 10, 0, 0)
        ext = apply_to_external(mappings, {"created": dt})
        assert ext == {"created_at": dt.isoformat()}
        back = apply_from_external(mappings, ext)
        assert back == {"created": dt}

    def test_format_round_trip(self):
        mappings = [TimestampConvert("created", "created_at", "%Y-%m-%d")]
        dt = datetime(2026, 4, 28)
        ext = apply_to_external(mappings, {"created": dt})
        assert ext == {"created_at": "2026-04-28"}
        back = apply_from_external(mappings, ext)
        assert back == {"created": dt}


class TestCustom:
    def test_two_directions(self):
        mappings = [
            Custom(
                fn_to=lambda d: {"x_doubled": d["x"] * 2} if "x" in d else {},
                fn_from=lambda d: {"x": d["x_doubled"] // 2} if "x_doubled" in d else {},
            ),
        ]
        assert apply_to_external(mappings, {"x": 5}, pass_through_unmapped=False) == {"x_doubled": 10}
        assert apply_from_external(mappings, {"x_doubled": 10}, pass_through_unmapped=False) == {"x": 5}


class TestPipelineComposition:
    def test_combined_round_trip(self):
        mappings: list[FieldMapping] = [
            Rename("display_name", "name"),
            CentsToDecimal("price", "amount"),
            EnumRemap("status", "state", {"active": "A", "inactive": "I"}),
        ]
        internal = {
            "display_name": "Pro",
            "price": Decimal("9.99"),
            "status": "active",
            "id": "abc",  # passthrough
        }
        external = apply_to_external(mappings, internal)
        assert external == {"name": "Pro", "amount": 999, "state": "A", "id": "abc"}

        back = apply_from_external(mappings, external)
        assert back == {
            "display_name": "Pro",
            "price": Decimal("9.99"),
            "status": "active",
            "id": "abc",
        }


class _ExampleSearch(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class TestPipelineWithPydantic:
    def test_dump_then_apply(self):
        mappings = [Rename("name", "display_name")]
        m = _ExampleSearch(name="Premium", is_active=True)
        ext = apply_to_external(mappings, m.model_dump(exclude_none=True))
        assert ext == {"display_name": "Premium", "is_active": True}
