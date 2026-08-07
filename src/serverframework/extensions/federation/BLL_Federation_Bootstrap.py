"""Boot-time federation wiring (Item 16).

Iterates over the loaded extensions, finds every concrete
:class:`AbstractGraphQLProvider` subclass with a configured upstream, runs
the introspect → transform → register → lift → bind pipeline, and integrates
the result with the framework's ``ModelRegistry`` so the lifted Pydantic
models flow through the existing ``Pydantic2{Strawberry,FastAPI}`` pipelines
and appear on BOTH inbound surfaces — REST and GraphQL — regardless of
whether the upstream is GraphQL or REST.

Two integration points:

* :func:`install_external_federation` (async) — runs the full federation
  pipeline. Use from FastAPI's lifespan event when extensions register
  their providers lazily.
* :func:`install_external_federation_sync` — runs the same pipeline
  synchronously. Used inside ``ModelRegistry.commit()`` so lifted models
  participate in the same Phase 1.5 → Phase 4 commit flow as hand-written
  ones. This is the canonical entry point for production startup.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Type
from urllib.parse import urlparse

from serverframework.extensions.federation.BLL_Federation_GQL import (
    FederatedSubgraph,
    MergedSchemaRegistry,
    SDLToPydanticResult,
    global_registry,
    project_gql_as_rest,
    reset_global_registry,
)
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


# ---------------------------------------------------------------------------
# Upstream URL validation (SSRF guard)
# ---------------------------------------------------------------------------


_DEFAULT_ALLOWED_SCHEMES = ("https",)


def _is_private_or_local(host: str) -> bool:
    """Return True if ``host`` resolves to a non-publicly-routable address."""
    try:
        addrinfos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable hostnames are rejected by the caller; treat as unsafe
        # to keep the SSRF guard conservative.
        return True
    for info in addrinfos:
        sockaddr = info[4]
        ip_str = sockaddr[0] if isinstance(sockaddr, tuple) else None
        if ip_str is None:
            return True
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def validate_upstream_url(url: str, *, allow_private: Optional[bool] = None) -> None:
    """Reject malformed URLs and (by default) URLs that point at private IPs.

    Raises ``ValueError`` describing the rejection reason; callers should
    catch and surface as a federation-bootstrap error rather than aborting
    the whole startup.

    Policy:
    - Production / staging: TLS required (https), public hostnames only.
      Both can be relaxed via ``FEDERATION_ALLOW_HTTP_UPSTREAMS=true``
      and ``FEDERATION_ALLOW_PRIVATE_UPSTREAMS=true`` for in-cluster
      federation that terminates TLS at a sidecar.
    - Local / CI / development: http and private hosts are allowed by
      default so test harnesses can exercise the pipeline without
      configuring extra env vars. Operators can still tighten via the
      same env vars.

    Args:
        url: The upstream URL declared by a federation provider.
        allow_private: Explicit override; when ``None`` policy is derived
            from environment.
    """
    if not url:
        raise ValueError("upstream_url is empty")
    parsed = urlparse(url)

    environment = (env("ENVIRONMENT") or "local").lower()
    is_dev_like = environment in ("local", "ci", "development")

    allow_http = (
        is_dev_like
        or (env("FEDERATION_ALLOW_HTTP_UPSTREAMS") or "").lower() == "true"
    )
    if parsed.scheme not in _DEFAULT_ALLOWED_SCHEMES and not (
        allow_http and parsed.scheme == "http"
    ):
        raise ValueError(
            f"upstream_url {url!r}: scheme {parsed.scheme!r} not allowed "
            f"(set FEDERATION_ALLOW_HTTP_UPSTREAMS=true to permit http)"
        )
    if not parsed.hostname:
        raise ValueError(f"upstream_url {url!r}: missing hostname")
    if allow_private is None:
        allow_private = is_dev_like or (
            (env("FEDERATION_ALLOW_PRIVATE_UPSTREAMS") or "").lower() == "true"
        )
    if not allow_private and _is_private_or_local(parsed.hostname):
        raise ValueError(
            f"upstream_url {url!r}: hostname resolves to a private/loopback "
            f"address; set FEDERATION_ALLOW_PRIVATE_UPSTREAMS=true to permit"
        )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_gql_providers() -> List[type]:
    """Walk the extension registry to find configured GraphQL providers."""

    try:
        from serverframework.extensions.AbstractExtensionProvider import (
            ExtensionRegistry,
        )
        from serverframework.extensions.AbstractGraphQLProvider import (
            AbstractGraphQLProvider,
        )
    except ImportError:
        return []

    found: List[type] = []
    extensions = getattr(ExtensionRegistry, "extensions", None) or []
    for ext in extensions:
        for prov in getattr(ext, "_providers", []) or []:
            if (
                isinstance(prov, type)
                and issubclass(prov, AbstractGraphQLProvider)
                and prov is not AbstractGraphQLProvider
            ):
                found.append(prov)
    return found


def _discover_rest_specs() -> List[Mapping[str, Any]]:
    """Walk the extension registry to find OpenAPI specs for REST upstreams.

    Each extension whose providers declare ``openapi_url`` (or carry a
    ``contracts/<provider>.openapi.json`` snapshot) yields one entry of the
    form ``{"name": <ext_or_provider>, "spec": <document>, "transport":
    <RESTUpstreamTransport>, "prefix": <namespace>}``. Extensions that don't
    expose a spec are skipped — there's nothing for the federation pipeline
    to do for them.
    """

    try:
        from serverframework.extensions.AbstractExtensionProvider import (
            ExtensionRegistry,
        )
    except ImportError:
        return []
    out: List[Mapping[str, Any]] = []
    extensions = getattr(ExtensionRegistry, "extensions", None) or []
    for ext in extensions:
        spec_provider = getattr(ext, "openapi_spec_provider", None)
        if spec_provider is None:
            continue
        try:
            descriptor = spec_provider()
        except Exception as exc:
            logger.debug(
                "OpenAPI spec_provider for %s raised: %s", ext.__name__, exc
            )
            continue
        if descriptor is None:
            continue
        out.append(descriptor)
    return out


# ---------------------------------------------------------------------------
# Async pipeline (used from lifespan)
# ---------------------------------------------------------------------------


async def install_external_federation(
    *,
    providers: Optional[Iterable[Any]] = None,
    registry: Optional[MergedSchemaRegistry] = None,
    skip_unconfigured: bool = True,
) -> Dict[str, Any]:
    """Run the federation pipeline for every concrete GraphQL provider.

    Returns ``{provider_name: lift_result}`` so callers can route the lifted
    Pydantic models into the model registry. Failures are logged and
    skipped — federation MUST NOT prevent the rest of the framework from
    starting; an unreachable upstream surfaces via :meth:`health_check`.
    """

    from serverframework.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )

    target_registry = registry or global_registry()
    discovered: List[type] = list(providers or _discover_gql_providers())

    lifted: Dict[str, Any] = {}
    for provider_cls in discovered:
        if not issubclass(provider_cls, AbstractGraphQLProvider):
            continue
        upstream = getattr(provider_cls, "upstream_url", "")
        if not upstream and skip_unconfigured:
            logger.debug(
                "Skipping %s — no upstream_url configured", provider_cls.__name__
            )
            continue
        try:
            validate_upstream_url(upstream)
        except ValueError as exc:
            logger.warning(
                "Federation rejected for %s: %s", provider_cls.__name__, exc
            )
            continue
        try:
            ingested = await provider_cls.register_with_registry(registry=target_registry)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(
                "Federation pipeline failed for %s: %s", provider_cls.__name__, exc
            )
            continue
        try:
            lift_result = provider_cls.lift_to_pydantic(ingested)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(
                "SDL→Pydantic lift failed for %s: %s", provider_cls.__name__, exc
            )
            lift_result = None
        lifted[provider_cls.__name__] = lift_result

    target_registry.build()
    return lifted


# ---------------------------------------------------------------------------
# Sync pipeline (used from ModelRegistry.commit())
# ---------------------------------------------------------------------------


class FederationCommitReport:
    """Outcome of :func:`install_external_federation_sync`.

    Carries everything the registry needs to integrate federated models with
    the existing pipelines: bound model classes, synthesized managers, and
    the FastAPI routers from GQL→REST projection.
    """

    __slots__ = ("models", "managers", "rest_routers", "subgraphs", "errors")

    def __init__(self) -> None:
        self.models: Dict[str, type] = {}
        self.managers: Dict[str, type] = {}
        self.rest_routers: List[Any] = []
        self.subgraphs: Dict[str, FederatedSubgraph] = {}
        self.errors: Dict[str, str] = {}


def install_external_federation_sync(
    *,
    model_registry: Any,
    providers: Optional[Iterable[Any]] = None,
    rest_descriptors: Optional[Iterable[Mapping[str, Any]]] = None,
    schema_registry: Optional[MergedSchemaRegistry] = None,
) -> FederationCommitReport:
    """Synchronous federation pipeline that integrates with ModelRegistry.

    Three jobs:

    1. For every configured ``AbstractGraphQLProvider``: introspect the GQL
       upstream, transform, register with ``MergedSchemaRegistry``, lift to
       Pydantic, synthesize an ``AbstractExternalManager`` per lifted model,
       and bind both with the framework's ``ModelRegistry``.
    2. For every configured REST upstream descriptor: import the OpenAPI
       spec, derive ``AbstractExternalModel`` subclasses bound to the
       transport, synthesize managers, and bind with ``ModelRegistry``.
    3. Mount GQL→REST projection routers onto the FastAPI app via the
       returned :class:`FederationCommitReport`.

    The returned report is consumed by :func:`build_app` which mounts the
    accumulated REST routers on the FastAPI app.
    """

    from serverframework.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )

    target = schema_registry or global_registry()
    report = FederationCommitReport()

    # --- GraphQL upstream pipeline ---
    for provider_cls in providers or _discover_gql_providers():
        if not issubclass(provider_cls, AbstractGraphQLProvider):
            continue
        upstream = getattr(provider_cls, "upstream_url", "")
        if not upstream:
            continue
        try:
            validate_upstream_url(upstream)
        except ValueError as exc:
            logger.warning(
                "GQL federation rejected for %s: %s", provider_cls.__name__, exc
            )
            report.errors[provider_cls.__name__] = str(exc)
            continue
        try:
            ingested = provider_cls.register_with_registry_sync(registry=target)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "GQL federation failed for %s: %s", provider_cls.__name__, exc
            )
            report.errors[provider_cls.__name__] = str(exc)
            continue
        try:
            lift = provider_cls.lift_to_pydantic(ingested)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "SDL→Pydantic lift failed for %s: %s", provider_cls.__name__, exc
            )
            report.errors[provider_cls.__name__ + ".lift"] = str(exc)
            continue
        _bind_lifted_models_for_gql(
            provider_cls=provider_cls,
            ingested=ingested,
            lift=lift,
            model_registry=model_registry,
            report=report,
        )
        # Mount GQL→REST projection so REST clients can hit the GQL upstream.
        subgraphs = {sg.name: sg for sg in target.subgraphs()}
        sg = subgraphs.get(provider_cls.__name__)
        if sg is not None and sg.transport is not None:
            try:
                router = project_gql_as_rest(
                    subgraph=sg,
                    transport=sg.transport,
                    prefix=f"/federated/{provider_cls.__name__.lower()}",
                )
                report.rest_routers.append(router)
                report.subgraphs[provider_cls.__name__] = sg
            except Exception as exc:
                logger.warning(
                    "GQL→REST projection failed for %s: %s",
                    provider_cls.__name__,
                    exc,
                )

    # --- REST upstream pipeline ---
    for descriptor in rest_descriptors or _discover_rest_specs():
        try:
            _bind_lifted_models_for_rest(
                descriptor=descriptor,
                model_registry=model_registry,
                report=report,
            )
        except Exception as exc:
            name = descriptor.get("name", "<unknown>")
            logger.warning("REST federation failed for %s: %s", name, exc)
            report.errors[str(name)] = str(exc)

    target.build()
    return report


def _bind_lifted_models_for_gql(
    *,
    provider_cls: type,
    ingested: Any,
    lift: SDLToPydanticResult,
    model_registry: Any,
    report: FederationCommitReport,
) -> None:
    """Synthesize managers for GQL-lifted models and bind both with the registry.

    The lift produces raw Pydantic models. To flow through the framework's
    existing REST + GQL pipelines we wrap each in an ``AbstractExternalManager``
    subclass whose ``Model`` points at the lifted model and whose
    ``*_via_provider`` static methods dispatch through the GQL transport.
    """

    transport = provider_cls.upstream_transport()  # type: ignore[attr-defined]
    for type_name, model_cls in (lift.models or {}).items():
        external_model_cls = _synthesize_gql_external_model(
            type_name=type_name,
            model_cls=model_cls,
            transport=transport,
        )
        manager_cls = _synthesize_external_manager(
            type_name=type_name, external_model_cls=external_model_cls
        )
        report.models[type_name] = external_model_cls
        report.managers[type_name] = manager_cls
        _safe_bind(model_registry, external_model_cls, type_name)


def _bind_lifted_models_for_rest(
    *,
    descriptor: Mapping[str, Any],
    model_registry: Any,
    report: FederationCommitReport,
) -> None:
    """Import a REST upstream's OpenAPI spec and bind every lifted model.

    ``descriptor`` is the dict produced by an extension's
    ``openapi_spec_provider``: ``{"name", "spec", "transport", "prefix"}``.
    ``transport`` is a fully-built :class:`RESTUpstreamTransport` so the
    derived external models share the same rotation/auth/rate-limit stack
    as the rest of the framework.
    """

    from serverframework.extensions.federation.BLL_Federation_REST import (
        derive_external_models,
        openapi_to_pydantic_models,
    )

    name = descriptor["name"]
    spec = descriptor["spec"]
    transport = descriptor["transport"]
    prefix = descriptor.get("prefix")
    crud_map = descriptor.get("crud_map")

    pydantic_result = openapi_to_pydantic_models(spec, prefix=prefix)
    derived = derive_external_models(
        pydantic_result=pydantic_result,
        transport=transport,
        crud_map=crud_map,
    )
    for type_name, external_model_cls in derived.items():
        manager_cls = _synthesize_external_manager(
            type_name=type_name, external_model_cls=external_model_cls
        )
        report.models[type_name] = external_model_cls
        report.managers[type_name] = manager_cls
        _safe_bind(model_registry, external_model_cls, type_name)


def _synthesize_gql_external_model(
    *,
    type_name: str,
    model_cls: type,
    transport: Any,
) -> type:
    """Wrap a GQL-lifted Pydantic model as an :class:`AbstractExternalModel`."""

    from serverframework.extensions.AbstractExternalModel import (
        AbstractExternalModel,
    )
    from serverframework.extensions.federation.BLL_Federation_GQL import (
        build_query_document,
        reconstruct_selection_set,
    )

    def _gql_get(provider_instance, external_id):
        # Default selection: every leaf scalar on the model.
        selection = " ".join(model_cls.model_fields.keys())
        document = build_query_document(
            operation=f'{type_name.lower()}(id: "{external_id}")',
            selection_body=selection,
        )
        try:
            response = transport.send_sync(query=document)
        except AttributeError:
            # Async-only transport — fall back to the event loop.
            import asyncio as _asyncio

            response = _asyncio.run(transport.send(query=document))
        if isinstance(response, dict):
            return (response.get("data") or {}).get(type_name.lower())
        return response

    def _gql_list(provider_instance, **kwargs):
        selection = " ".join(model_cls.model_fields.keys())
        document = build_query_document(
            operation=f"{_pluralize(type_name.lower())}",
            selection_body=selection,
        )
        try:
            response = transport.send_sync(query=document)
        except AttributeError:
            import asyncio as _asyncio

            response = _asyncio.run(transport.send(query=document))
        if isinstance(response, dict):
            return (response.get("data") or {}).get(_pluralize(type_name.lower())) or []
        return response or []

    def _unsupported_create(*_a, **_kw):  # pragma: no cover - defensive
        return None

    def _unsupported_update(*_a, **_kw):  # pragma: no cover - defensive
        return None

    def _unsupported_delete(*_a, **_kw):  # pragma: no cover - defensive
        return None

    bound = type(
        f"{type_name}External",
        (model_cls, AbstractExternalModel),
        {
            "external_resource": type_name.lower(),
            "raises_typed_errors": True,
            "create_via_provider": staticmethod(_unsupported_create),
            "get_via_provider": staticmethod(_gql_get),
            "list_via_provider": staticmethod(_gql_list),
            "update_via_provider": staticmethod(_unsupported_update),
            "delete_via_provider": staticmethod(_unsupported_delete),
        },
    )
    return bound


def _synthesize_external_manager(
    *, type_name: str, external_model_cls: type
) -> type:
    """Build an :class:`AbstractExternalManager` subclass for a derived model."""

    from serverframework.extensions.AbstractExternalModel import (
        AbstractExternalManager,
    )

    return type(
        f"{type_name}Manager",
        (AbstractExternalManager,),
        {
            "Model": external_model_cls,
            "ReferenceModel": external_model_cls,
            "NetworkModel": external_model_cls,
        },
    )


def _safe_bind(model_registry: Any, model_cls: type, name: str) -> None:
    """Bind a model to the registry, swallowing duplicates."""

    bind = (
        getattr(model_registry, "bind_model", None)
        or getattr(model_registry, "register_model", None)
    )
    if bind is None:
        logger.warning(
            "Model registry has no bind_model/register_model; %s won't appear on either surface",
            name,
        )
        return
    try:
        bind(model_cls)
    except TypeError:
        # Some implementations expect (model, name).
        try:
            bind(model_cls, name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Bind failed for %s: %s", name, exc)
    except Exception as exc:
        logger.debug("Bind failed for %s: %s", name, exc)


def _pluralize(s: str) -> str:
    """Naive English pluralization for upstream root field names."""

    if s.endswith("y"):
        return s[:-1] + "ies"
    if s.endswith("s"):
        return s
    return s + "s"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def reset_federation_state() -> None:
    """Test helper: clear the global merged schema registry."""

    reset_global_registry()


__all__ = [
    "install_external_federation",
    "install_external_federation_sync",
    "FederationCommitReport",
    "reset_federation_state",
]
