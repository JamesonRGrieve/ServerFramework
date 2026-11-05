# SPDX-License-Identifier: AGPL-3.0-or-later
"""WebDAV pieces CalDAV (RFC 4791) and CardDAV (RFC 6352) providers share:
reading a REPORT's multistatus answer, and naming a resource in a
collection.

A resource's id is its name in its collection (``<uid>.ics``,
``<uid>.vcf``), so an id is always exactly one path segment: one that is
not (``../other``) is refused before any request is made.
"""

from typing import List, Tuple
from urllib.parse import quote, unquote, urljoin

from defusedxml import ElementTree

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)

DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"
CARDDAV = "{urn:ietf:params:xml:ns:carddav}"


    """Each ``(href, data)`` in a multistatus ``answer``, where ``data`` is
    the text of ``data_element`` (``CALDAV + "calendar-data"``,
    ``CARDDAV + "address-data"``); a response without data (the collection
    itself) is skipped."""
    try:
        root = ElementTree.fromstring(answer)
    except ElementTree.ParseError as exc:
        raise PermanentExternalError(f"the server's answer is not XML: {exc}") from exc
    found: List[Tuple[str, str]] = []
    for response in root.iter(f"{DAV}response"):
        href = response.findtext(f"{DAV}href") or ""
        data = response.findtext(f".//{data_element}") or ""
        if href and data.strip():
            found.append((href, data))
    return found


def resource_name(href: str) -> str:
    """The resource name an href ends in: the resource's id in its collection."""
    return unquote(href.rstrip("/").rsplit("/", 1)[-1])


    """A collection's URL, ending in ``/`` so resource names join onto it."""
    return url if url.endswith("/") else f"{url}/"


    """The URL of resource ``name`` in ``collection``; ``name`` must be one
    path segment."""
    if not name or "/" in name or name in (".", ".."):
        raise InvalidInputExternalError(f"{name!r} is not a resource name")
    return urljoin(collection_url(collection), quote(name))
