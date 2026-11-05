# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory entries and the search filters evaluated against them.

A search request's RFC 4511 ``Filter`` is decoded a level at a time into
a small tree (:func:`parse_filter`), refusing filters past
:data:`MAX_FILTER_NODES` nodes or :data:`MAX_FILTER_DEPTH` levels before
decoding any deeper, then evaluated per entry with the protocol's
three-valued logic (TRUE, FALSE, Undefined as ``None``).

Only released attributes take part: an assertion on any other attribute
is Undefined, so a filter cannot probe a value the directory does not
show. Matching is case-insensitive (the directory's attributes all use
``caseIgnoreMatch`` or ``caseIgnoreIA5Match``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ldap3.protocol.rfc4511 import (
    ApproxMatch,
    EqualityMatch,
    GreaterOrEqual,
    LessOrEqual,
    Present,
    SubstringFilter,
)
from pyasn1.codec.ber import decoder
from pyasn1.error import PyAsn1Error

from zephyrex.extensions.ldap_provider.LDAPProtocol import filter_set, negated_filter

MAX_FILTER_NODES = 64
MAX_FILTER_DEPTH = 12
MAX_ASSERTION_BYTES = 1024

# Attribute selectors with a meaning of their own (RFC 4511 4.5.1.8).
ALL_USER_ATTRIBUTES = "*"
NO_ATTRIBUTES = "1.1"
ALL_OPERATIONAL_ATTRIBUTES = "+"


    """A filter the directory will not evaluate: too large or too deep."""


    """A filter that is not a well-formed LDAP filter."""


    """One entry: its DN and its attributes (name -> values)."""

    dn: str
    attributes: Mapping[str, Tuple[str, ...]]

        """The values of attribute ``name`` (any case), or None."""
        wanted = name.lower()
        for attribute, found in self.attributes.items():
            if attribute.lower() == wanted:
                return found
        return None

        """The attributes to return for ``selection`` (the request's
        attribute list), limited to ``released`` (lower-case names)."""
        wanted = [name.lower() for name in selection]
        everything = not wanted or ALL_USER_ATTRIBUTES in wanted
        if not everything and all(
            name in (NO_ATTRIBUTES, ALL_OPERATIONAL_ATTRIBUTES) for name in wanted
        ):
            return []
        chosen = []
        for attribute, found in self.attributes.items():
            name = attribute.lower()
            if name not in released or not found:
                continue
            if everything or name in wanted:
                chosen.append((attribute, () if types_only else found))
        return chosen


    """One filter node. ``kind`` is the RFC 4511 choice name; leaves carry
    ``attribute`` and their assertion, branches carry ``children``."""

    kind: str
    attribute: str = ""
    value: str = ""
    initial: Optional[str] = None
    middle: Tuple[str, ...] = ()
    final: Optional[str] = None
    children: Tuple["Node", ...] = field(default_factory=tuple)


    data = bytes(raw)
    if len(data) > MAX_ASSERTION_BYTES:
        raise FilterRefused(f"an assertion value is over {MAX_ASSERTION_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FilterMalformed("an assertion value is not UTF-8") from exc


    def __init__(self) -> None:
        self.nodes = 0

        self.nodes += 1
        if self.nodes > MAX_FILTER_NODES:
            raise FilterRefused(f"the filter has over {MAX_FILTER_NODES} parts")
        if depth > MAX_FILTER_DEPTH:
            raise FilterRefused(f"the filter nests over {MAX_FILTER_DEPTH} levels")


def parse_filter(encoded: Any) -> Node:
    """A search request's filter (its BER encoding, as
    ``LDAPProtocol.SearchRequest`` leaves it) as a :class:`Node`."""
    return _parse(bytes(encoded), _Budget(), 1)


# The first octet of each kind of filter (RFC 4511 4.5.1, IMPLICIT TAGS).
_KINDS: Dict[int, Tuple[str, Callable[[], Any]]] = {
    0xA0: ("and", lambda: filter_set(0)),
    0xA1: ("or", lambda: filter_set(1)),
    0xA2: ("notFilter", negated_filter),
    0xA3: ("equalityMatch", EqualityMatch),
    0xA4: ("substringFilter", SubstringFilter),
    0xA5: ("greaterOrEqual", GreaterOrEqual),
    0xA6: ("lessOrEqual", LessOrEqual),
    0x87: ("present", Present),
    0xA8: ("approxMatch", ApproxMatch),
}
_EXTENSIBLE_MATCH = 0xA9


    try:
        value, rest = decoder.decode(encoded, asn1Spec=spec)
    except PyAsn1Error as exc:
        raise FilterMalformed("the filter is not a well-formed LDAP filter") from exc
    if rest:
        raise FilterMalformed("the filter is not a well-formed LDAP filter")
    return value


    budget.spend(depth)
    if not encoded:
        raise FilterMalformed("the filter is empty")
    if encoded[0] == _EXTENSIBLE_MATCH:
        # No extensible matching rules are offered.
        return Node("extensibleMatch")
    if encoded[0] not in _KINDS:
        raise FilterMalformed("the filter is not a well-formed LDAP filter")
    kind, spec = _KINDS[encoded[0]]
    component = _decode(encoded, spec())
    if kind in ("and", "or"):
        return Node(
            kind,
            children=tuple(
                _parse(bytes(child), budget, depth + 1) for child in component
            ),
        )
    if kind == "notFilter":
        inner = _parse(bytes(component["filter"]), budget, depth + 1)
        return Node(kind, children=(inner,))
    if kind in ("equalityMatch", "greaterOrEqual", "lessOrEqual", "approxMatch"):
        return Node(
            kind,
            attribute=_text(component["attributeDesc"]),
            value=_text(component["assertionValue"]),
        )
    if kind == "substringFilter":
        initial: Optional[str] = None
        final: Optional[str] = None
        middle: List[str] = []
        for part in component["substrings"]:
            text = _text(part.getComponent())
            position = part.getName()
            if position == "initial":
                initial = text
            elif position == "final":
                final = text
            else:
                middle.append(text)
        return Node(
            kind,
            attribute=_text(component["type"]),
            initial=initial,
            middle=tuple(middle),
            final=final,
        )
    return Node(kind, attribute=_text(component))


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


    text = _fold(value)
    position = 0
    if node.initial is not None:
        initial = _fold(node.initial)
        if not text.startswith(initial):
            return False
        position = len(initial)
    for part in node.middle:
        found = text.find(_fold(part), position)
        if found < 0:
            return False
        position = found + len(_fold(part))
    if node.final is not None:
        final = _fold(node.final)
        return len(text) - position >= len(final) and text.endswith(final)
    return True


    undefined = False
    for result in results:
        if result is False:
            return False
        if result is None:
            undefined = True
    return None if undefined else True


    undefined = False
    for result in results:
        if result is True:
            return True
        if result is None:
            undefined = True
    return None if undefined else False


    """``node`` against ``entry``: True, False, or None for Undefined."""
    if node.kind == "and":
        return _all(evaluate(child, entry, released) for child in node.children)
    if node.kind == "or":
        return _any(evaluate(child, entry, released) for child in node.children)
    if node.kind == "notFilter":
        inner = evaluate(node.children[0], entry, released)
        return None if inner is None else not inner
    if node.kind == "extensibleMatch":
        return None
    if node.attribute.lower() not in released:
        return False if node.kind == "present" else None
    values = entry.values(node.attribute)
    if not values:
        return False
    if node.kind == "present":
        return True
    if node.kind == "substringFilter":
        return any(_substring_match(node, value) for value in values)
    asserted = _fold(node.value)
    if node.kind in ("equalityMatch", "approxMatch"):
        return any(_fold(value) == asserted for value in values)
    if node.kind == "greaterOrEqual":
        return any(_fold(value) >= asserted for value in values)
    return any(_fold(value) <= asserted for value in values)
