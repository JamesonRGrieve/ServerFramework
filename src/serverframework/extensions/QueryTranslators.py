"""
Search DSL translators for external APIs.

Item 8 — Search DSL translation.

Internal search uses typed search models (`StringSearchModel`,
`NumericalSearchModel`, etc.) carrying typed operators (`exact`, `inc`,
`gt`, `lt`, `in_`, etc.). This module ships translators that convert
those models into the wire shape various upstreams expect.

Operators are recognized by name in either of two forms:
1. As a key on a search-field sub-dict, e.g. `{"name": {"inc": "Premium"}}`.
2. As a Pydantic-modeled field on a search submodel; the translator
   reads `model.dump()` and looks for the same operator names.

A translator declares its `supported_operators: ClassVar[Set[str]]`.
Asking it to translate an operator outside that set raises
`UnsupportedOperatorError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Mapping, Set, Tuple, Union

from pydantic import BaseModel

from serverframework.extensions.ExternalErrors import UnsupportedOperatorError


# Canonical operator names used by the framework's search models.
ALL_OPERATORS: Set[str] = {
    "exact",
    "neq",
    "inc",  # contains / substring
    "starts_with",
    "ends_with",
    "gt",
    "gte",
    "lt",
    "lte",
    "in_",
    "not_in",
    "is_null",
    "between",
}


def _to_dict(search_model: Union[BaseModel, Mapping[str, Any], None]) -> Dict[str, Any]:
    """Coerce a search input to a flat dict of `{field: value_or_op_dict}`."""

    if search_model is None:
        return {}
    if isinstance(search_model, BaseModel):
        return search_model.model_dump(exclude_none=True)
    if isinstance(search_model, Mapping):
        return {k: v for k, v in search_model.items() if v is not None}
    raise TypeError(
        f"search_model must be a BaseModel or a Mapping; got {type(search_model).__name__}"
    )


def _normalize_field(value: Any) -> List[Tuple[str, Any]]:
    """Return a list of `(operator, value)` pairs for one field.

    A scalar value collapses to `[("exact", value)]`. A dict whose keys
    are operator names is enumerated as is. Anything else is treated as
    `("exact", value)`.
    """

    if isinstance(value, dict) and value and all(k in ALL_OPERATORS for k in value.keys()):
        return list(value.items())
    return [("exact", value)]


class AbstractQueryDSLTranslator(ABC):
    """Convert a typed search model into the upstream's query DSL."""

    supported_operators: ClassVar[Set[str]] = set(ALL_OPERATORS)

    def translate(self, search_model: Union[BaseModel, Mapping[str, Any], None]) -> Any:
        """Public entry point; calls `_translate` after validation."""

        flat = _to_dict(search_model)
        # Pre-flight unsupported-operator check.
        for field_name, value in flat.items():
            for op, _ in _normalize_field(value):
                if op not in self.supported_operators:
                    raise UnsupportedOperatorError(
                        f"Operator {op!r} on field {field_name!r} is not supported by "
                        f"{self.__class__.__name__}",
                    )
        return self._translate(flat)

    @abstractmethod
    def _translate(self, flat: Dict[str, Any]) -> Any:
        """Subclasses implement the actual emission here."""


# ---------------------------------------------------------------------------
# Concrete translators
# ---------------------------------------------------------------------------


class StripeSearchTranslator(AbstractQueryDSLTranslator):
    """Stripe search query string: `field:value AND field2:"value 2"`.

    Stripe's search syntax supports limited operators; this translator
    advertises only the subset Stripe accepts.
    """

    supported_operators: ClassVar[Set[str]] = {"exact", "neq", "gt", "gte", "lt", "lte"}

    def _translate(self, flat: Dict[str, Any]) -> str:
        clauses: List[str] = []
        for field_name, value in flat.items():
            for op, v in _normalize_field(value):
                clauses.append(self._format_clause(field_name, op, v))
        return " AND ".join(clauses)

    @staticmethod
    def _format_clause(field: str, op: str, value: Any) -> str:
        op_map = {
            "exact": ":",
            "neq": ":-",  # Stripe uses `-field:value` syntax
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }
        token = op_map[op]
        rendered_value = StripeSearchTranslator._render_value(value)
        if op == "neq":
            # Stripe negation syntax is `-field:value`.
            return f"-{field}:{rendered_value}"
        return f"{field}{token}{rendered_value}"

    @staticmethod
    def _render_value(value: Any) -> str:
        if isinstance(value, str) and (" " in value or '"' in value):
            escaped = value.replace('"', '\\"')
            return f'"{escaped}"'
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


class KeyValueTranslator(AbstractQueryDSLTranslator):
    """Flat `?field=value&field2=value2` translator.

    Equality only; non-`exact` operators raise.
    """

    supported_operators: ClassVar[Set[str]] = {"exact"}

    def _translate(self, flat: Dict[str, Any]) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for field_name, value in flat.items():
            for _op, v in _normalize_field(value):
                params[field_name] = v
        return params


class MongoStyleTranslator(AbstractQueryDSLTranslator):
    """Mongo-style operator dict: `{field: {"$gt": 1}}`."""

    supported_operators: ClassVar[Set[str]] = {
        "exact",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "in_",
        "not_in",
        "is_null",
    }

    OPERATOR_MAP: ClassVar[Dict[str, str]] = {
        "neq": "$ne",
        "gt": "$gt",
        "gte": "$gte",
        "lt": "$lt",
        "lte": "$lte",
        "in_": "$in",
        "not_in": "$nin",
    }

    def _translate(self, flat: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for field_name, value in flat.items():
            pairs = _normalize_field(value)
            if len(pairs) == 1 and pairs[0][0] == "exact":
                out[field_name] = pairs[0][1]
                continue
            field_dict: Dict[str, Any] = {}
            for op, v in pairs:
                if op == "exact":
                    field_dict["$eq"] = v
                elif op == "is_null":
                    field_dict["$exists"] = not bool(v)
                else:
                    field_dict[self.OPERATOR_MAP[op]] = v
            out[field_name] = field_dict
        return out


class SOQLTranslator(AbstractQueryDSLTranslator):
    """Salesforce SOQL `WHERE` clause emitter (the `WHERE` keyword is
    not included; the caller composes it into a full query)."""

    supported_operators: ClassVar[Set[str]] = {
        "exact",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "in_",
        "not_in",
        "inc",  # rendered as LIKE '%value%'
        "starts_with",
        "ends_with",
        "is_null",
    }

    def _translate(self, flat: Dict[str, Any]) -> str:
        clauses: List[str] = []
        for field_name, value in flat.items():
            for op, v in _normalize_field(value):
                clauses.append(self._format_clause(field_name, op, v))
        return " AND ".join(clauses)

    @staticmethod
    def _format_clause(field: str, op: str, value: Any) -> str:
        if op == "exact":
            return f"{field} = {SOQLTranslator._render(value)}"
        if op == "neq":
            return f"{field} != {SOQLTranslator._render(value)}"
        if op == "gt":
            return f"{field} > {SOQLTranslator._render(value)}"
        if op == "gte":
            return f"{field} >= {SOQLTranslator._render(value)}"
        if op == "lt":
            return f"{field} < {SOQLTranslator._render(value)}"
        if op == "lte":
            return f"{field} <= {SOQLTranslator._render(value)}"
        if op == "in_":
            rendered = ", ".join(SOQLTranslator._render(x) for x in value)
            return f"{field} IN ({rendered})"
        if op == "not_in":
            rendered = ", ".join(SOQLTranslator._render(x) for x in value)
            return f"{field} NOT IN ({rendered})"
        if op == "inc":
            return f"{field} LIKE '%{SOQLTranslator._escape(value)}%'"
        if op == "starts_with":
            return f"{field} LIKE '{SOQLTranslator._escape(value)}%'"
        if op == "ends_with":
            return f"{field} LIKE '%{SOQLTranslator._escape(value)}'"
        if op == "is_null":
            return f"{field} = NULL" if value else f"{field} != NULL"
        raise AssertionError(f"unhandled SOQL operator {op!r}")  # defensive

    @staticmethod
    def _render(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        return f"'{SOQLTranslator._escape(str(value))}'"

    @staticmethod
    def _escape(value: str) -> str:
        return str(value).replace("'", "\\'")


class GraphQLFilterTranslator(AbstractQueryDSLTranslator):
    """Hasura/PostGraphile-style filter object:
    `{field: {_eq: value}, field2: {_gt: 1}}`."""

    supported_operators: ClassVar[Set[str]] = {
        "exact",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "in_",
        "not_in",
        "inc",
        "starts_with",
        "ends_with",
        "is_null",
    }

    OPERATOR_MAP: ClassVar[Dict[str, str]] = {
        "exact": "_eq",
        "neq": "_neq",
        "gt": "_gt",
        "gte": "_gte",
        "lt": "_lt",
        "lte": "_lte",
        "in_": "_in",
        "not_in": "_nin",
        "inc": "_ilike",
        "starts_with": "_ilike",
        "ends_with": "_ilike",
        "is_null": "_is_null",
    }

    def _translate(self, flat: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for field_name, value in flat.items():
            field_dict: Dict[str, Any] = {}
            for op, v in _normalize_field(value):
                gql_op = self.OPERATOR_MAP[op]
                if op == "inc":
                    field_dict[gql_op] = f"%{v}%"
                elif op == "starts_with":
                    field_dict[gql_op] = f"{v}%"
                elif op == "ends_with":
                    field_dict[gql_op] = f"%{v}"
                else:
                    field_dict[gql_op] = v
            out[field_name] = field_dict
        return out


__all__ = [
    "AbstractQueryDSLTranslator",
    "StripeSearchTranslator",
    "KeyValueTranslator",
    "MongoStyleTranslator",
    "SOQLTranslator",
    "GraphQLFilterTranslator",
    "ALL_OPERATORS",
]
