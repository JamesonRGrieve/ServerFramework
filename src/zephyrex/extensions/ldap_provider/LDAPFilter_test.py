# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filters as clients encode them (ldap3's own filter compiler) evaluated
against entries: three-valued logic, case folding, substrings, attributes
outside the released set, and the size and depth bounds; DNs parsed and
rendered with their escapes."""

from typing import Any, List, Tuple

import pytest
from ldap3 import DEREF_NEVER, SUBTREE
from ldap3.operation.search import search_operation
from pyasn1.codec.ber import decoder, encoder

from zephyrex.extensions.ldap_provider.LDAPDirectory import (
    Refused,
    ResultCode,
    parse,
    render,
)
from zephyrex.extensions.ldap_provider.LDAPFilter import (
    MAX_FILTER_DEPTH,
    MAX_FILTER_NODES,
    DirectoryEntry,
    FilterRefused,
    evaluate,
    parse_filter,
)
from zephyrex.extensions.ldap_provider.LDAPProtocol import SearchRequest

ENTRY = DirectoryEntry(
    "uid=alice,ou=people,dc=example,dc=org",
    {
        "objectClass": ("top", "person", "inetOrgPerson"),
        "uid": ("alice",),
        "cn": ("Alice  Liddell",),
        "mail": ("Alice@Example.org",),
        "secretAttribute": ("hidden",),
    },
)
RELEASED = frozenset({"objectclass", "uid", "cn", "mail"})


def asn1(text: str) -> Any:
    """``text`` as the server receives it: ldap3 builds the search request,
    which is BER-encoded and decoded again."""
    request = search_operation(
        "dc=example,dc=org", text, SUBTREE, DEREF_NEVER, [], 0, 0, False, True, False
    )
    decoded, _ = decoder.decode(encoder.encode(request), asn1Spec=SearchRequest())
    return decoded["filter"]


def outcome(text: str, entry: DirectoryEntry = ENTRY) -> Any:
    return evaluate(parse_filter(asn1(text)), entry, RELEASED)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("(uid=alice)", True),
        ("(uid=ALICE)", True),
        ("(uid=bob)", False),
        ("(cn=alice liddell)", True),
        ("(mail=alice@example.org)", True),
        ("(objectClass=inetOrgPerson)", True),
        ("(objectClass=*)", True),
        ("(sn=*)", False),
        ("(cn=Ali*)", True),
        ("(cn=*Lidd*)", True),
        ("(cn=*dell)", True),
        ("(cn=A*e*l)", True),
        ("(cn=A*x*l)", False),
        ("(uid>=aaa)", True),
        ("(uid<=aaa)", False),
        ("(uid~=alice)", True),
        ("(&(uid=alice)(mail=*))", True),
        ("(&(uid=alice)(uid=bob))", False),
        ("(|(uid=bob)(uid=alice))", True),
        ("(|(uid=bob)(uid=carol))", False),
        ("(!(uid=bob))", True),
        ("(!(uid=alice))", False),
    ],
)
def test_matching(text: str, expected: bool) -> None:
    assert outcome(text) is expected


def test_an_attribute_not_released_is_undefined_and_never_matches() -> None:
    assert outcome("(secretAttribute=hidden)") is None
    assert outcome("(secretAttribute=*)") is False
    assert outcome("(!(secretAttribute=hidden))") is None
    assert outcome("(&(uid=alice)(secretAttribute=hidden))") is None
    assert outcome("(|(uid=alice)(secretAttribute=hidden))") is True


def test_undefined_meets_three_valued_logic() -> None:
    assert outcome("(&(uid=bob)(secretAttribute=x))") is False
    assert outcome("(|(uid=bob)(secretAttribute=x))") is None


def test_extensible_match_is_undefined() -> None:
    assert outcome("(uid:caseExactMatch:=alice)") is None


def test_a_filter_with_too_many_parts_is_refused() -> None:
    wide = "(|" + "".join(f"(uid=u{i})" for i in range(MAX_FILTER_NODES)) + ")"
    with pytest.raises(FilterRefused):
        parse_filter(asn1(wide))
    fits = "(|" + "".join(f"(uid=u{i})" for i in range(MAX_FILTER_NODES - 1)) + ")"
    assert evaluate(parse_filter(asn1(fits)), ENTRY, RELEASED) is False


def test_a_filter_nested_too_deep_is_refused() -> None:
    deep = "(!" * MAX_FILTER_DEPTH + "(uid=alice)" + ")" * MAX_FILTER_DEPTH
    with pytest.raises(FilterRefused):
        parse_filter(asn1(deep))


def names(selection: Tuple[str, ...]) -> List[str]:
    return [name for name, _ in ENTRY.released(RELEASED, selection, False)]


def test_an_absurdly_deep_filter_is_refused_at_the_bound() -> None:
    deepest = 60
    with pytest.raises(FilterRefused):
        parse_filter(asn1("(!" * deepest + "(uid=alice)" + ")" * deepest))


@pytest.mark.parametrize(
    "encoded",
    [b"", b"\x04\x01x", b"\xa3\x03\x04\x01x", b"\xa0\x02\x04\x00", b"\x87\x01x\x00"],
)
def test_a_malformed_filter_is_refused(encoded: bytes) -> None:
    with pytest.raises(FilterRefused):
        parse_filter(encoded)


def test_every_nesting_decodes_to_its_tree() -> None:
    text = "(&(objectClass=*)(|(uid=bob)(!(cn=zed*)))(&(uid=alice))(mail=*))"
    assert outcome(text) is True
    tree = parse_filter(asn1(text))
    assert [child.kind for child in tree.children] == [
        "present",
        "or",
        "and",
        "present",
    ]
    assert tree.children[1].children[1].children[0].initial == "zed"


def test_selection_follows_the_request_and_the_released_set() -> None:
    assert names(()) == ["objectClass", "uid", "cn", "mail"]
    assert names(("*",)) == ["objectClass", "uid", "cn", "mail"]
    assert names(("MAIL", "secretAttribute")) == ["mail"]
    assert names(("1.1",)) == []
    assert names(("+",)) == []
    assert ENTRY.released(RELEASED, ("uid",), True) == [("uid", ())]


def test_dns_parse_with_escapes_and_render_back() -> None:
    parsed = parse(r"UID=a\,b\2Bc , ou=People,dc=example,dc=org")
    assert parsed == (
        ("uid", "a,b+c"),
        ("ou", "People"),
        ("dc", "example"),
        ("dc", "org"),
    )
    assert parse(render(parsed)) == parsed
    assert parse("") == ()


@pytest.mark.parametrize("bad", ["uid", "=x", "uid=a+cn=b,dc=x", "u id=a"])
def test_a_malformed_or_multi_valued_dn_is_refused(bad: str) -> None:
    with pytest.raises(Refused) as refused:
        parse(bad)
    assert refused.value.code == ResultCode.INVALID_DN_SYNTAX
