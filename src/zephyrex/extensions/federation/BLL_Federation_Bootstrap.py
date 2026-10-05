# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boot-time federation wiring (Item 16).

Iterates over the app's loaded extensions, finds every concrete
:class:`AbstractGraphQLProvider` subclass with a configured upstream, every
REST upstream descriptor and every federated source
(``BLL_Federation_Typed``), runs the introspect → transform → register →
lift → bind pipeline, and integrates the result with the framework's
``ModelRegistry``: each lifted model is bound table-less
(``ModelRegistry.bind_external``) with a typed manager, so it appears on
BOTH inbound surfaces — REST and GraphQL — regardless of whether the
upstream is GraphQL or REST, and no table is ever made for it.

Two integration points:

* :func:`install_external_federation` (async) — runs the GraphQL lift. Use
  from FastAPI's lifespan event when extensions register their providers
  lazily.
* :func:`bootstrap_federation` — the registry's federation hook, which the
  extension registers at on_initialize: :func:`install_external_federation_sync`
  inside ``ModelRegistry.commit()``, once the database is migrated and
  before the routers are generated. This is the canonical entry point for
  production startup.
"""

from __future__ import annotations

import asyncio
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.parse import urlparse

from fastapi import HTTPException

from zephyrex.extensions.AbstractExternalModel import _unwrap_provider_call
from zephyrex.extensions.federation.BLL_Federation_GQL import (
    FederatedSubgraph,
    GQLUpstreamTransport,
    MergedSchemaRegistry,
    SDLToPydanticResult,
    global_registry,
    project_gql_as_rest,
    reset_global_registry,
)
from zephyrex.extensions.federation.BLL_Federation_Typed import (
    AbstractFederatedSource,
    FederatedBinding,
    FederatedCall,
    FederatedQuery,
    FederatedSession,
    FederatedType,
    LiftedType,
    Operation,
    bind_federated_sources,
    bind_lifted,
    federated_sources,
    type_slugs,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

FEDERATION_EXTENSION = "federation"
# The field a GraphQL- or OpenAPI-lifted record is named by.
PROVIDER_KEY_FIELD = "id"

# ---------------------------------------------------------------------------
# Upstream URL validation (SSRF guard)
# ---------------------------------------------------------------------------


_DEFAULT_ALLOWED_SCHEMES = ("https",)


def _is_private_or_local(host: str) -> bool:
    """Return True if ``host`` resolves to a non-publicly-routable address.

    Delegates to ProviderHTTPClient's SSRF guard for consistency.
    """
    try:
        from zephyrex.lib.ProviderHTTPClient import validate_outbound_url

        validate_outbound_url(f"https://{host}/")
        return False
    except Exception:
        return True


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

    from zephyrex.lib.Environment import resolve_environment

    environment = resolve_environment()
    is_dev_like = environment in ("local", "ci", "development")

    allow_http = (
        is_dev_like or (env("FEDERATION_ALLOW_HTTP_UPSTREAMS") or "").lower() == "true"
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


def _loaded_extensions(model_registry: Any) -> List[type]:
    """The extension classes the app loads (none for a registry without an
    extension registry)."""
    extension_registry = getattr(model_registry, "extension_registry", None)
    return list(getattr(extension_registry, "extensions", None) or [])


def _discover_gql_providers(model_registry: Any) -> List[type]:
    """The GraphQL providers of the app's extensions."""

    from zephyrex.extensions.AbstractGraphQLProvider import AbstractGraphQLProvider

    found: List[type] = []
    for ext in _loaded_extensions(model_registry):
        for prov in getattr(ext, "_providers", []) or []:
            if (
                isinstance(prov, type)
                and issubclass(prov, AbstractGraphQLProvider)
                and prov is not AbstractGraphQLProvider
            ):
                found.append(prov)
    return found


def _discover_rest_specs(model_registry: Any) -> List[Mapping[str, Any]]:
    """The OpenAPI specs the app's extensions give for REST upstreams.

    Each extension whose providers declare ``openapi_url`` (or carry a
    ``contracts/<provider>.openapi.json`` snapshot) yields one entry of the
    form ``{"name": <ext_or_provider>, "spec": <document>, "transport":
    <RESTUpstreamTransport>, "prefix": <namespace>}``. Extensions that don't
    expose a spec are skipped — there's nothing for the federation pipeline
    to do for them.
    """

    out: List[Mapping[str, Any]] = []
    for ext in _loaded_extensions(model_registry):
        spec_provider = getattr(ext, "openapi_spec_provider", None)
        if spec_provider is None:
            continue
        try:
            descriptor = spec_provider()
        except Exception as exc:
            logger.debug("OpenAPI spec_provider for %s raised: %s", ext.__name__, exc)
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
    model_registry: Any = None,
) -> Dict[str, Any]:
    """Run the federation pipeline for every concrete GraphQL provider
    (``providers``, else those of ``model_registry``'s extensions).

    Returns ``{provider_name: lift_result}`` so callers can route the lifted
    Pydantic models into the model registry. Failures are logged and
    skipped — federation MUST NOT prevent the rest of the framework from
    starting; an unreachable upstream surfaces via :meth:`health_check`.
    """

    from zephyrex.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )

    target_registry = registry or global_registry()
    discovered: List[type] = list(providers or _discover_gql_providers(model_registry))

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
            logger.warning("Federation rejected for %s: %s", provider_cls.__name__, exc)
            continue
        try:
            ingested = await provider_cls.register_with_registry(
                registry=target_registry
            )
        except Exception as exc:
            logger.warning(
                "Federation pipeline failed for %s: %s", provider_cls.__name__, exc
            )
            continue
        try:
            lift_result = provider_cls.lift_to_pydantic(ingested)
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
    sources: Optional[Iterable[AbstractFederatedSource]] = None,
) -> FederationCommitReport:
    """Synchronous federation pipeline that integrates with ModelRegistry.

    Four jobs, each over what is given or, when nothing is, what the app's
    extensions declare:

    1. For every configured ``AbstractGraphQLProvider``: introspect the GQL
       upstream, transform, register with ``MergedSchemaRegistry``, lift to
       Pydantic, synthesize a typed manager per lifted model, and bind the
       model, table-less, with ``ModelRegistry.bind_external``.
    2. For every configured REST upstream descriptor: import the OpenAPI
       spec, derive ``AbstractExternalModel`` subclasses bound to the
       transport, synthesize managers, and bind them likewise.
    3. For every federated source (``BLL_Federation_Typed``): read its type
       catalogue, lift each type's JSON Schema and bind it likewise.
    4. Mount GQL→REST projection routers onto the FastAPI app via the
       returned :class:`FederationCommitReport`.

    The returned report is consumed by :func:`build_app` which mounts the
    accumulated REST routers on the FastAPI app.
    """

    from zephyrex.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )

    target = schema_registry or global_registry()
    report = FederationCommitReport()

    # --- GraphQL upstream pipeline ---
    for provider_cls in providers or _discover_gql_providers(model_registry):
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
    for descriptor in rest_descriptors or _discover_rest_specs(model_registry):
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

    # --- Federated sources: typed record types ---
    binding = bind_federated_sources(
        model_registry,
        federated_sources(model_registry) if sources is None else sources,
    )
    report.models.update(binding.models)
    report.managers.update(binding.managers)
    report.errors.update(binding.errors)

    target.build()
    return report


def bootstrap_federation(*, model_registry: Any) -> Optional[FederationCommitReport]:
    """The registry's federation hook (``ModelRegistry._bootstrap_federation``):
    the pipeline, for an app that loads this extension; None for one that
    does not (the hook table is process-wide)."""
    if FEDERATION_EXTENSION not in model_registry.loaded_extension_names():
        return None
    return install_external_federation_sync(model_registry=model_registry)


def _bind_lifted_models_for_gql(
    *,
    provider_cls: type,
    ingested: Any,
    lift: SDLToPydanticResult,
    model_registry: Any,
    report: FederationCommitReport,
) -> None:
    """Bind each GQL-lifted model, table-less, with a typed manager.

    The lift produces raw Pydantic models. Each is wrapped as an
    ``AbstractExternalModel`` whose ``*_via_provider`` static methods
    dispatch through the GQL transport, and served (read only: the upstream
    names no version a save could be held to) by the typed manager
    ``BLL_Federation_Typed.synthesize_manager`` makes, under the namespace
    of the provider's name.
    """

    transport = provider_cls.upstream_transport()  # type: ignore[attr-defined]
    _bind_upstream_models(
        model_registry=model_registry,
        source=_ProviderMethodsSource(provider_cls.__name__),
        upstream_models=[
            (
                type_name,
                _synthesize_gql_external_model(
                    type_name=type_name, model_cls=model_cls, transport=transport
                ),
                None,
                frozenset({Operation.LIST, Operation.GET}),
            )
            for type_name, model_cls in (lift.models or {}).items()
        ],
        report=report,
    )


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

    from zephyrex.extensions.federation.BLL_Federation_REST import (
        _guess_crud_actions,
        derive_external_models,
        openapi_to_pydantic_models,
    )

    name = descriptor["name"]
    spec = descriptor["spec"]
    transport = descriptor["transport"]
    prefix = descriptor.get("prefix")
    crud_map = dict(descriptor.get("crud_map") or {})

    pydantic_result = openapi_to_pydantic_models(spec, prefix=prefix)
    derived = derive_external_models(
        pydantic_result=pydantic_result,
        transport=transport,
        crud_map=crud_map,
    )

    def served(type_name: str) -> FrozenSet[Operation]:
        """The operations the spec has an operation for (a save is never
        served: the spec names no version to hold it to)."""
        actions = crud_map.get(type_name) or _guess_crud_actions(type_name)
        return frozenset(
            Operation(action)
            for action, operation in actions.items()
            if operation in pydantic_result.operations
        )

    _bind_upstream_models(
        model_registry=model_registry,
        source=_ProviderMethodsSource(str(name)),
        upstream_models=[
            (
                type_name,
                external_model_cls,
                pydantic_result.models[type_name],
                served(type_name),
            )
            for type_name, external_model_cls in derived.items()
        ],
        report=report,
    )


def _synthesize_gql_external_model(
    *,
    type_name: str,
    model_cls: type,
    transport: GQLUpstreamTransport,
) -> type:
    """Wrap a GQL-lifted Pydantic model as an :class:`AbstractExternalModel`."""

    from zephyrex.extensions.AbstractExternalModel import (
        AbstractExternalModel,
    )
    from zephyrex.extensions.federation.BLL_Federation_GQL import (
        build_query_document,
    )

    def _gql_get(provider_instance, external_id):
        # Default selection: every leaf scalar on the model.
        selection = " ".join(model_cls.model_fields.keys())
        document = build_query_document(
            operation=f'{type_name.lower()}(id: "{external_id}")',
            selection_body=selection,
        )
        response = transport.send_sync(query=document)
        if isinstance(response, dict):
            return (response.get("data") or {}).get(type_name.lower())
        return response

    def _gql_list(provider_instance, **kwargs):
        selection = " ".join(model_cls.model_fields.keys())
        document = build_query_document(
            operation=f"{_pluralize(type_name.lower())}",
            selection_body=selection,
        )
        response = transport.send_sync(query=document)
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


class _ProviderMethodsSession(FederatedSession):
    """Calls a lifted model's ``*_via_provider`` methods (the upstream's
    transport, synchronous) off the event loop. The upstream's credentials
    are the operator's: any signed-in requester is served."""

    def __init__(self, models: Mapping[str, Any]) -> None:
        self._models = models

    @staticmethod
    async def _call(model: Any, method: str, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(
            _unwrap_provider_call,
            model,
            method,
            getattr(model, f"{method}_via_provider"),
            *args,
            **kwargs,
        )

    async def list(
        self, type_name: str, query: FederatedQuery
    ) -> Sequence[Mapping[str, Any]]:
        model = self._models[type_name]
        params = model.to_external_query_format(
            dict(query.filters or {}), limit=query.page_length, offset=query.start
        )
        rows = await self._call(model, "list", None, **params)
        return [model.from_external_format(row) for row in rows or []]

    async def get(self, type_name: str, key: Any) -> Mapping[str, Any]:
        model = self._models[type_name]
        row = await self._call(model, "get", None, key)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No such {type_name}")
        found: Mapping[str, Any] = model.from_external_format(row)
        return found

    async def create(
        self, type_name: str, data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        model = self._models[type_name]
        row = await self._call(
            model, "create", None, **model.to_external_format(dict(data))
        )
        made: Mapping[str, Any] = model.from_external_format(row or {})
        return made


class _ProviderMethodsSource(AbstractFederatedSource):
    """A GraphQL or REST upstream whose types the federation lifted itself
    (from its SDL or OpenAPI document), served under the namespace of its
    provider's or descriptor's name."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._namespace = type_slugs([name])[0]
        self.models: Dict[str, Any] = {}

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def title(self) -> str:
        return self._name

    async def catalogue(self) -> Sequence[FederatedType]:
        return [
            FederatedType(name=type_name, json_schema={}, key_field=PROVIDER_KEY_FIELD)
            for type_name in self.models
        ]

    def session(self, call: FederatedCall) -> FederatedSession:
        return _ProviderMethodsSession(self.models)


def _bind_upstream_models(
    *,
    model_registry: Any,
    source: _ProviderMethodsSource,
    upstream_models: Sequence[Tuple[str, Any, Optional[Any], FrozenSet[Operation]]],
    report: FederationCommitReport,
) -> None:
    """Bind each ``(type name, external model, write payload, operations)``,
    table-less, served by ``source``. One that cannot be bound (a name
    already taken, no ``id`` to name a record by) is left out and logged."""
    for (type_name, model, write, operations), slug in zip(
        upstream_models, type_slugs([entry[0] for entry in upstream_models])
    ):
        source.models[type_name] = model
        federated = FederatedType(
            name=type_name,
            json_schema={},
            key_field=PROVIDER_KEY_FIELD,
            operations=operations,
        )
        binding = FederatedBinding()
        try:
            lifted = LiftedType.of_models(federated, slug, model, write)
            bind_lifted(model_registry, source, lifted, binding)
        except Exception as exc:
            logger.warning("%s: %s is not served: %s", source.title, type_name, exc)
            report.errors[f"{source.namespace}.{type_name}"] = str(exc)
            continue
        report.models.update(binding.models)
        report.managers.update(binding.managers)


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
    "bootstrap_federation",
    "install_external_federation",
    "install_external_federation_sync",
    "FederationCommitReport",
    "reset_federation_state",
]
