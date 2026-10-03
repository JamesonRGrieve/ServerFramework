# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ASN.1 schema the server decodes requests with.

ldap3's RFC 4511 definitions serve a client: they encode requests and
decode responses. Their ``Filter`` cannot decode a filter, because
``And`` and ``Or`` have no component type and ``Not`` is captured by
``Filter`` before its tag and component are set. A pyasn1 schema cannot
refer to itself, and unrolling it is exponential to build, so a search
request carries its filter as an open type (:func:`raw_element`, the
filter's encoding) and ``LDAPFilter.parse_filter`` decodes it one level at a time
with the schemas here, within its own depth and size bounds.

Everything else is ldap3's: the leaf filter types and the other requests
decode correctly. Responses are encoded with ldap3's ``LDAPMessage``.
"""

from ldap3.protocol.rfc4511 import (
    AbandonRequest,
    AddRequest,
    AttributeSelection,
    BindRequest,
    CompareRequest,
    Controls,
    DelRequest,
    DerefAliases,
    ExtendedRequest,
    Integer0ToMax,
    LDAPDN,
    MessageID,
    ModifyDNRequest,
    ModifyRequest,
    Scope,
    TypesOnly,
    UnbindRequest,
)
from pyasn1.type import namedtype, tag, univ


def context(number: int) -> tag.Tag:
    """A constructed context-specific tag: ``[number]``."""
    return tag.Tag(tag.tagClassContext, tag.tagFormatConstructed, number)


def raw_element() -> univ.Any:
    """An untagged open type: the next element, encoding and all."""
    return univ.Any()


def filter_set(number: int) -> univ.SetOf:
    """``and`` ([0]) or ``or`` ([1]): a set of filters, each still raw."""
    return univ.SetOf(componentType=raw_element()).subtype(implicitTag=context(number))


def negated_filter() -> univ.Sequence:
    """``not`` ([2]): a tagged CHOICE is explicitly tagged, so its content
    is one whole filter, read here as a constructed [2] holding it."""
    return univ.Sequence(
        componentType=namedtype.NamedTypes(namedtype.NamedType("filter", raw_element()))
    ).subtype(implicitTag=context(2))


class SearchRequest(univ.Sequence):
    tagSet = univ.Sequence.tagSet.tagImplicitly(
        tag.Tag(tag.tagClassApplication, tag.tagFormatConstructed, 3)
    )
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("baseObject", LDAPDN()),
        namedtype.NamedType("scope", Scope()),
        namedtype.NamedType("derefAliases", DerefAliases()),
        namedtype.NamedType("sizeLimit", Integer0ToMax()),
        namedtype.NamedType("timeLimit", Integer0ToMax()),
        namedtype.NamedType("typesOnly", TypesOnly()),
        namedtype.NamedType("filter", raw_element()),
        namedtype.NamedType("attributes", AttributeSelection()),
    )


class RequestOp(univ.Choice):
    """The protocol operations a client sends."""

    componentType = namedtype.NamedTypes(
        namedtype.NamedType("bindRequest", BindRequest()),
        namedtype.NamedType("unbindRequest", UnbindRequest()),
        namedtype.NamedType("searchRequest", SearchRequest()),
        namedtype.NamedType("modifyRequest", ModifyRequest()),
        namedtype.NamedType("addRequest", AddRequest()),
        namedtype.NamedType("delRequest", DelRequest()),
        namedtype.NamedType("modDNRequest", ModifyDNRequest()),
        namedtype.NamedType("compareRequest", CompareRequest()),
        namedtype.NamedType("abandonRequest", AbandonRequest()),
        namedtype.NamedType("extendedReq", ExtendedRequest()),
    )


class RequestMessage(univ.Sequence):
    """An LDAPMessage as a client sends it."""

    componentType = namedtype.NamedTypes(
        namedtype.NamedType("messageID", MessageID()),
        namedtype.NamedType("protocolOp", RequestOp()),
        namedtype.OptionalNamedType("controls", Controls()),
    )
