# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 filters and attribute paths (RFC 7644 §3.4.2.2 and §3.5.2).

``parse_filter`` reads a ``filter`` query parameter into an expression
tree; ``matches`` evaluates it against a resource as this server renders
it. ``parse_patch_path`` reads a PATCH operation's ``path``. Values are
JSON (strings with JSON escapes), operators and attribute names are
case-insensitive, ``and`` binds tighter than ``or``, and string
comparisons ignore case except on the case-exact attributes (ids)."""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from zephyrex.extensions.scim_consumer.SCIMErrors import (
    INVALID_FILTER,
    INVALID_PATH,
    INVALID_VALUE,
    ScimError,
    bad_request,
)

USER_URN = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_URN = "urn:ietf:params:scim:schemas:core:2.0:Group"
CORE_URNS = frozenset({USER_URN.lower(), GROUP_URN.lower()})

MAX_FILTER_LENGTH = 4096
MAX_FILTER_DEPTH = 32

COMPARE_OPERATORS = frozenset({"eq", "ne", "co", "sw", "ew", "gt", "lt", "ge", "le"})
ORDERING_OPERATORS = frozenset({"gt", "lt", "ge", "le"})
# Attributes whose string values compare exactly (RFC 7643 caseExact).
CASE_EXACT = frozenset({"id", "externalid", "members.value", "groups.value"})

_ATTRIBUTE_NAME = re.compile(r"^\$?[A-Za-z][A-Za-z0-9_-]*$")
_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<lbracket>\[)
      | (?P<rbracket>\])
      | (?P<string>"(?:[^"\\]|\\.)*")
      | (?P<word>[^\s()\[\]"]+)
    )""",
    re.VERBOSE,
)


@dataclass(frozen=True)
class Token:
    kind: str
    text: str


@dataclass(frozen=True)
class AttrPath:
    """``[urn:]attribute[.subAttribute]``."""

    urn: Optional[str]
    attribute: str
    sub_attribute: Optional[str] = None

    @property
    def dotted(self) -> str:
        if self.sub_attribute:
            return f"{self.attribute}.{self.sub_attribute}".lower()
        return self.attribute.lower()


@dataclass(frozen=True)
class Compare:
    path: AttrPath
    operator: str
    value: Any


@dataclass(frozen=True)
class Present:
    path: AttrPath


@dataclass(frozen=True)
class And:
    left: "Filter"
    right: "Filter"


@dataclass(frozen=True)
class Or:
    left: "Filter"
    right: "Filter"


@dataclass(frozen=True)
class Not:
    inner: "Filter"


@dataclass(frozen=True)
class ValuePath:
    """``attribute[filter]``: some value of a multi-valued attribute
    satisfies ``inner``, whose paths name its sub-attributes."""

    path: AttrPath
    inner: "Filter"


Filter = Union[Compare, Present, And, Or, Not, ValuePath]


@dataclass(frozen=True)
class PatchPath:
    """A PATCH ``path``: ``attrPath`` or ``valuePath[.subAttribute]``."""

    path: AttrPath
    value_filter: Optional[Filter] = None
    sub_attribute: Optional[str] = None


def _tokens(text: str, error_type: str) -> List[Token]:
    if len(text) > MAX_FILTER_LENGTH:
        raise bad_request(error_type, f"longer than {MAX_FILTER_LENGTH} characters")
    found: List[Token] = []
    position = 0
    while position < len(text):
        if not text[position:].strip():
            break
        match = _TOKEN.match(text, position)
        if match is None or match.lastgroup is None:
            raise bad_request(error_type, f"cannot read {text[position:]!r}")
        found.append(Token(match.lastgroup, match.group(match.lastgroup)))
        position = match.end()
    return found


def parse_attr_path(text: str, error_type: str = INVALID_FILTER) -> AttrPath:
    """``urn:…:User:name.givenName`` → urn, ``name``, ``givenName``."""
    urn: Optional[str] = None
    rest = text
    if text.lower().startswith("urn:"):
        cut = text.rfind(":")
        urn, rest = text[:cut], text[cut + 1 :]
    parts = rest.split(".")
    if len(parts) > 2 or not all(_ATTRIBUTE_NAME.match(part) for part in parts):
        raise bad_request(error_type, f"{text!r} is not an attribute path")
    return AttrPath(urn, parts[0], parts[1] if len(parts) == 2 else None)


class _Parser:
    def __init__(self, tokens: Sequence[Token], error_type: str) -> None:
        self.tokens = tokens
        self.position = 0
        self.depth = 0
        self.error_type = error_type

    def fail(self, detail: str) -> ScimError:
        return bad_request(self.error_type, detail)

    def peek(self) -> Optional[Token]:
        if self.position < len(self.tokens):
            return self.tokens[self.position]
        return None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise self.fail("ends too soon")
        self.position += 1
        return token

    def expect(self, kind: str) -> None:
        if self.take().kind != kind:
            raise self.fail(f"expected {kind}")

    def keyword(self, *words: str) -> Optional[str]:
        token = self.peek()
        if token is not None and token.kind == "word" and token.text.lower() in words:
            return token.text.lower()
        return None

    def nested(self) -> None:
        self.depth += 1
        if self.depth > MAX_FILTER_DEPTH:
            raise self.fail(f"nested deeper than {MAX_FILTER_DEPTH}")

    def done(self) -> bool:
        return self.peek() is None

    def disjunction(self) -> Filter:
        left = self.conjunction()
        while self.keyword("or"):
            self.take()
            left = Or(left, self.conjunction())
        return left

    def conjunction(self) -> Filter:
        left = self.unary()
        while self.keyword("and"):
            self.take()
            left = And(left, self.unary())
        return left

    def unary(self) -> Filter:
        if self.keyword("not"):
            self.take()
            self.nested()
            self.expect("lparen")
            inner = self.disjunction()
            self.expect("rparen")
            self.depth -= 1
            return Not(inner)
        token = self.peek()
        if token is not None and token.kind == "lparen":
            self.take()
            self.nested()
            inner = self.disjunction()
            self.expect("rparen")
            self.depth -= 1
            return inner
        return self.attribute_expression()

    def attribute_expression(self) -> Filter:
        token = self.take()
        if token.kind != "word":
            raise self.fail("expected an attribute")
        path = parse_attr_path(token.text, self.error_type)
        following = self.peek()
        if following is not None and following.kind == "lbracket":
            return self.value_path(path)
        operator = self.take()
        name = operator.text.lower()
        if operator.kind != "word" or (name != "pr" and name not in COMPARE_OPERATORS):
            raise self.fail(f"{operator.text!r} is not an operator")
        if name == "pr":
            return Present(path)
        value = self.value()
        _check_operands(name, value)
        return Compare(path, name, value)

    def value_path(self, path: AttrPath) -> ValuePath:
        if path.sub_attribute:
            raise self.fail("a value filter follows an attribute, not a sub-attribute")
        self.expect("lbracket")
        self.nested()
        inner = self.disjunction()
        self.expect("rbracket")
        self.depth -= 1
        return ValuePath(path, inner)

    def value(self) -> Any:
        token = self.take()
        if token.kind == "string":
            try:
                return json.loads(token.text)
            except json.JSONDecodeError:
                raise self.fail(f"{token.text} is not a JSON string") from None
        if token.kind == "word":
            word = token.text.lower()
            if word in ("true", "false", "null"):
                return json.loads(word)
            if _NUMBER.match(token.text):
                return json.loads(token.text)
        raise self.fail(f"{token.text!r} is not a value")


def parse_filter(text: str) -> Filter:
    """A ``filter`` expression, or a 400 ``invalidFilter``."""
    parser = _Parser(_tokens(text, INVALID_FILTER), INVALID_FILTER)
    if parser.done():
        raise parser.fail("the filter is empty")
    expression = parser.disjunction()
    if not parser.done():
        raise parser.fail(f"unexpected {parser.take().text!r}")
    return expression


def parse_patch_path(text: str) -> PatchPath:
    """A PATCH ``path``, or a 400 ``invalidPath``."""
    parser = _Parser(_tokens(text, INVALID_PATH), INVALID_PATH)
    token = parser.take()
    if token.kind != "word":
        raise parser.fail(f"{text!r} is not a path")
    path = parse_attr_path(token.text, INVALID_PATH)
    if parser.done():
        return PatchPath(path)
    value_path = parser.value_path(path)
    sub_attribute: Optional[str] = None
    if not parser.done():
        tail = parser.take()
        name = tail.text[1:]
        if (
            tail.kind != "word"
            or not tail.text.startswith(".")
            or not _ATTRIBUTE_NAME.match(name)
        ):
            raise parser.fail(f"unexpected {tail.text!r}")
        sub_attribute = name
    if not parser.done():
        raise parser.fail(f"unexpected {parser.take().text!r}")
    return PatchPath(path, value_path.inner, sub_attribute)


def lookup(container: Mapping[str, Any], name: str) -> Optional[str]:
    """The key ``container`` holds for ``name``, ignoring case."""
    if name in container:
        return name
    lowered = name.lower()
    for key in container:
        if key.lower() == lowered:
            return key
    return None


def get(container: Any, name: str) -> Any:
    if not isinstance(container, Mapping):
        return None
    key = lookup(container, name)
    return None if key is None else container[key]


def base_of(resource: Mapping[str, Any], path: AttrPath) -> Any:
    """Where ``path``'s attribute lives: the resource for core attributes,
    the extension's object for an extension schema's."""
    if path.urn is None or path.urn.lower() in CORE_URNS:
        return resource
    return get(resource, path.urn)


def values_at(resource: Mapping[str, Any], path: AttrPath) -> List[Any]:
    """The leaf values ``path`` names: a multi-valued complex attribute
    without a sub-attribute stands for its ``value`` sub-attribute."""
    value = get(base_of(resource, path), path.attribute)
    items = value if isinstance(value, list) else [value]
    found: List[Any] = []
    for item in items:
        if path.sub_attribute is not None:
            leaf = get(item, path.sub_attribute)
        elif isinstance(item, Mapping):
            leaf = get(item, "value")
        else:
            leaf = item
        if isinstance(leaf, list):
            found.extend(v for v in leaf if v is not None)
        elif leaf is not None:
            found.append(leaf)
    return found


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _check_operands(operator: str, expected: Any) -> None:
    """Refuse an operator that cannot apply to the value's type."""
    if expected is None or isinstance(expected, bool):
        if operator not in ("eq", "ne"):
            raise bad_request(
                INVALID_FILTER, f"{operator} compares neither null nor booleans"
            )
    elif isinstance(expected, (int, float)):
        if operator in ("co", "sw", "ew"):
            raise bad_request(INVALID_FILTER, f"{operator} compares strings")
    elif not isinstance(expected, str):
        raise bad_request(INVALID_FILTER, f"cannot compare with {expected!r}")


def _compare_one(actual: Any, operator: str, expected: Any, case_exact: bool) -> bool:
    """Whether one attribute value is ``operator`` ``expected`` (an
    equality test for ``ne``, which the caller negates)."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return (
            isinstance(actual, bool)
            and isinstance(expected, bool)
            and actual == expected
        )
    if isinstance(expected, str):
        if not isinstance(actual, str):
            return False
        left, right = (
            (actual, expected) if case_exact else (actual.lower(), expected.lower())
        )
        if operator in ("eq", "ne"):
            return left == right
        if operator == "co":
            return right in left
        if operator == "sw":
            return left.startswith(right)
        if operator == "ew":
            return left.endswith(right)
        return _ordered(left, operator, right)
    if not isinstance(actual, (int, float)):
        return False
    if operator in ("eq", "ne"):
        return bool(actual == expected)
    return _ordered(actual, operator, expected)


def _ordered(left: Any, operator: str, right: Any) -> bool:
    if operator == "gt":
        return bool(left > right)
    if operator == "ge":
        return bool(left >= right)
    if operator == "lt":
        return bool(left < right)
    return bool(left <= right)


def _compare(resource: Mapping[str, Any], node: Compare, parent: Optional[str]) -> bool:
    values = values_at(resource, node.path)
    if node.value is None:
        return bool(values) if node.operator == "ne" else not values
    dotted = f"{parent}.{node.path.dotted}" if parent else node.path.dotted
    case_exact = dotted in CASE_EXACT
    hits = any(_compare_one(v, node.operator, node.value, case_exact) for v in values)
    return not hits if node.operator == "ne" else hits


def matches(
    expression: Filter, resource: Mapping[str, Any], parent: Optional[str] = None
) -> bool:
    """Whether ``resource`` satisfies ``expression``. ``parent`` names the
    multi-valued attribute when ``resource`` is one of its values."""
    if isinstance(expression, And):
        return matches(expression.left, resource, parent) and matches(
            expression.right, resource, parent
        )
    if isinstance(expression, Or):
        return matches(expression.left, resource, parent) or matches(
            expression.right, resource, parent
        )
    if isinstance(expression, Not):
        return not matches(expression.inner, resource, parent)
    if isinstance(expression, Present):
        return any(_present(v) for v in values_at(resource, expression.path))
    if isinstance(expression, ValuePath):
        value = get(base_of(resource, expression.path), expression.path.attribute)
        items = value if isinstance(value, list) else [value]
        return any(
            matches(expression.inner, item, expression.path.attribute.lower())
            for item in items
            if isinstance(item, Mapping)
        )
    return _compare(resource, expression, parent)


def equality_seed(expression: Filter) -> Optional[Dict[str, Any]]:
    """The values a filter of ``a eq x [and b eq y …]`` pins, so a PATCH
    that targets a value no element holds yet can add that element (as
    ``emails[type eq "work"].value`` adds the work address); None for any
    other filter."""
    if isinstance(expression, And):
        left, right = equality_seed(expression.left), equality_seed(expression.right)
        if left is None or right is None:
            return None
        return {**left, **right}
    if (
        isinstance(expression, Compare)
        and expression.operator == "eq"
        and expression.path.sub_attribute is None
        and expression.path.urn is None
    ):
        return {expression.path.attribute: expression.value}
    return None


SORT_ORDERS = frozenset({"ascending", "descending"})


def sort_resources(
    resources: Sequence[Dict[str, Any]],
    sort_by: Optional[str],
    sort_order: Optional[str],
) -> Tuple[Dict[str, Any], ...]:
    """``resources`` ordered by ``sortBy``'s first value (RFC 7644
    §3.4.2.3): ascending unless ``sortOrder`` says descending, strings
    ignoring case except on case-exact attributes, resources without a
    value last either way."""
    if sort_by is None or not sort_by.strip():
        return tuple(resources)
    order = (sort_order or "ascending").strip().lower()
    if order not in SORT_ORDERS:
        raise bad_request(INVALID_VALUE, "sortOrder is ascending or descending")
    path = parse_attr_path(sort_by.strip(), INVALID_VALUE)
    case_exact = path.dotted in CASE_EXACT

    def first(resource: Dict[str, Any]) -> Any:
        values = values_at(resource, path)
        if not values or isinstance(values[0], (dict, list)):
            return None
        value = values[0]
        return value.lower() if isinstance(value, str) and not case_exact else value

    keyed = [(first(resource), resource) for resource in resources]
    present = [pair for pair in keyed if pair[0] is not None]
    missing = [resource for key, resource in keyed if key is None]
    try:
        present.sort(key=lambda pair: pair[0], reverse=order == "descending")
    except TypeError:
        raise bad_request(INVALID_VALUE, f"{sort_by} holds unlike values") from None
    return tuple(resource for _, resource in present) + tuple(missing)


def filter_resources(
    resources: Sequence[Dict[str, Any]], text: Optional[str]
) -> Tuple[Dict[str, Any], ...]:
    """The resources a ``filter`` parameter selects (all when absent)."""
    if text is None or not text.strip():
        return tuple(resources)
    expression = parse_filter(text)
    return tuple(r for r in resources if matches(expression, r))
