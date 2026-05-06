"""Programmatic federation-matrix test generator (Item 16).

For every external schema extension the framework knows about, this module
generates a concrete :class:`AbstractFederationMatrixTest` subclass at
import time. Pytest collects the generated classes alongside hand-written
tests, so adding a new external upstream automatically buys you 4 quadrants
× 5 CRUD = 20 cells of homologation coverage.

How discovery works:

* Every extension whose ``AbstractStaticExtension`` subclass declares one
  or more of the well-known schema-descriptor classvars is candidate:
    - ``federation_matrix_fixtures: Iterable[FederationFixture]`` —
      explicit fixtures (one per upstream type). Preferred when the
      extension already has well-defined seeded data.
    - ``openapi_spec_provider: Callable[[], dict]`` — returns the OpenAPI
      document; the generator synthesizes a minimum viable fixture.
    - ``graphql_sdl_provider: Callable[[], str]`` — returns the SDL; ditto.
* The discovered fixtures are turned into pytest classes named
  ``Test_Federation_<extension>_<type>_Matrix`` and inserted into the
  caller's module ``__dict__``.

Two execution modes:

* In-process upstreams (default). The generator can build a tiny ASGI app
  from the supplied SDL/OpenAPI to use as a deterministic upstream. This
  is the canonical CI path.
* Live upstreams. If the fixture's ``requires_credentials`` is True and
  ``credentials_present()`` returns True, the matrix runs against the real
  upstream URL; otherwise pytest auto-xfails the suite (per
  ``EXT.Test.External.md``).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from serverframework.extensions.AbstractFederationMatrixTest import (
    AbstractFederationMatrixTest,
    FederationFixture,
)
from serverframework.lib.Logging import logger


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_extension_fixtures() -> List[FederationFixture]:
    """Walk the loaded extensions and gather every advertised fixture.

    Extensions advertise their federation surface in any of three ways:

    1. ``federation_matrix_fixtures`` — explicit list of
       :class:`FederationFixture` instances. Preferred.
    2. ``openapi_spec_provider`` — callable returning an OpenAPI dict.
       The generator synthesizes a minimum-viable fixture (one type, no
       create/update payloads — only ``get`` and ``list`` are exercised).
    3. ``graphql_sdl_provider`` — callable returning a GraphQL SDL string.
       Same minimum-viable shape.

    Returns the aggregated fixture list.
    """

    try:
        from serverframework.extensions.AbstractExtensionProvider import (
            ExtensionRegistry,
        )
    except ImportError:
        return []

    fixtures: List[FederationFixture] = []
    extensions = getattr(ExtensionRegistry, "extensions", None) or []
    for ext in extensions:
        explicit = getattr(ext, "federation_matrix_fixtures", None)
        if explicit:
            try:
                fixtures.extend(list(explicit() if callable(explicit) else explicit))
            except Exception as exc:
                logger.debug(
                    "federation_matrix_fixtures for %s raised: %s", ext.__name__, exc
                )
        spec_provider = getattr(ext, "openapi_spec_provider", None)
        if callable(spec_provider):
            try:
                fixtures.extend(_fixtures_from_openapi(spec_provider, ext))
            except Exception as exc:
                logger.debug(
                    "openapi_spec_provider for %s raised: %s", ext.__name__, exc
                )
        sdl_provider = getattr(ext, "graphql_sdl_provider", None)
        if callable(sdl_provider):
            try:
                fixtures.extend(_fixtures_from_sdl(sdl_provider, ext))
            except Exception as exc:
                logger.debug(
                    "graphql_sdl_provider for %s raised: %s", ext.__name__, exc
                )
    return fixtures


def _fixtures_from_openapi(
    spec_provider: Callable[[], Any], ext: Any
) -> List[FederationFixture]:
    """Synthesize matrix fixtures from an extension's OpenAPI document."""

    spec = spec_provider()
    if not isinstance(spec, Mapping):
        return []
    transport_factory = getattr(ext, "federation_rest_transport_factory", None)
    if not callable(transport_factory):
        # No transport — we can't run the matrix. Skip cleanly.
        return []
    transport = transport_factory()
    schemas = (spec.get("components") or {}).get("schemas") or {}
    out: List[FederationFixture] = []
    for type_name in schemas.keys():
        out.append(
            FederationFixture(
                name=f"{ext.__name__}.{type_name}",
                upstream_kind="rest",
                transport=transport,
                sample_id=getattr(ext, "federation_sample_id", "sample-id"),
                type_name=type_name,
                sdl_or_spec=spec,
                operations_supported=list(
                    getattr(ext, "federation_supported_ops", ["get", "list"])
                ),
                requires_credentials=bool(
                    getattr(ext, "federation_requires_credentials", False)
                ),
                credentials_present=getattr(
                    ext, "federation_credentials_present", lambda: True
                ),
            )
        )
    return out


def _fixtures_from_sdl(
    sdl_provider: Callable[[], str], ext: Any
) -> List[FederationFixture]:
    """Synthesize matrix fixtures from an extension's GraphQL SDL."""

    from graphql import parse

    sdl = sdl_provider()
    if not sdl:
        return []
    transport_factory = getattr(ext, "federation_gql_transport_factory", None)
    if not callable(transport_factory):
        return []
    transport = transport_factory()
    document = parse(sdl)
    type_names: List[str] = []
    for d in document.definitions:
        if d.kind != "object_type_definition":
            continue
        if d.name.value in {"Query", "Mutation", "Subscription"}:
            continue
        type_names.append(d.name.value)
    out: List[FederationFixture] = []
    for type_name in type_names:
        out.append(
            FederationFixture(
                name=f"{ext.__name__}.{type_name}",
                upstream_kind="gql",
                transport=transport,
                sample_id=getattr(ext, "federation_sample_id", "sample-id"),
                type_name=type_name,
                sdl_or_spec=sdl,
                operations_supported=list(
                    getattr(ext, "federation_supported_ops", ["get", "list"])
                ),
                requires_credentials=bool(
                    getattr(ext, "federation_requires_credentials", False)
                ),
                credentials_present=getattr(
                    ext, "federation_credentials_present", lambda: True
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Test class generation
# ---------------------------------------------------------------------------


def generate_matrix_tests(
    *,
    fixtures: Optional[Iterable[FederationFixture]] = None,
    target_namespace: Optional[Dict[str, Any]] = None,
) -> Dict[str, type]:
    """Generate one :class:`AbstractFederationMatrixTest` subclass per fixture.

    ``fixtures`` defaults to the result of :func:`discover_extension_fixtures`,
    so ``generate_matrix_tests()`` with no args is the standard call site.
    ``target_namespace`` is the module ``__dict__`` to inject the generated
    classes into; in pytest this is typically the test file's globals so
    pytest's collection finds the classes automatically.

    Returns ``{class_name: class}`` for callers that prefer to inspect what
    was generated rather than rely on namespace injection.
    """

    fixtures = list(fixtures) if fixtures is not None else discover_extension_fixtures()
    out: Dict[str, type] = {}
    for fix in fixtures:
        cls_name = (
            "Test_Federation_"
            + fix.name.replace(".", "_").replace("-", "_")
            + "_Matrix"
        )
        cls = type(
            cls_name,
            (AbstractFederationMatrixTest,),
            {
                "_fixture_for_class": staticmethod(lambda f=fix: f),
                "fixture": (lambda self, f=fix: f),
            },
        )
        out[cls_name] = cls
        if target_namespace is not None:
            target_namespace[cls_name] = cls
    return out


__all__ = [
    "discover_extension_fixtures",
    "generate_matrix_tests",
]
