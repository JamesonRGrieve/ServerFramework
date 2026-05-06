"""External GraphQL provider abstract (Item 16).

A concrete subclass declares the upstream URL, the federation style, and the
auth strategy to use. The framework then:

1. Introspects the upstream (Apollo Federation v2 SDL when supported, plain
   introspection when not) on startup, with TTL refresh.
2. Runs a :class:`SchemaTransformer` pipeline (rename, prefix, hide, mask,
   override) over the raw upstream SDL.
3. Registers the transformed SDL into ``MergedSchemaRegistry`` so the merged
   schema includes the upstream's types.
4. Optionally lifts the upstream SDL into Pydantic models so the existing
   ``Pydantic2Strawberry`` and ``Pydantic2FastAPI`` pipelines project the
   upstream onto BOTH the local GraphQL surface and the local REST surface.

Concrete providers do not have to implement transport plumbing — they declare
their auth strategy and the framework wires :class:`ProviderHTTPClient`,
``RotationManager``, rate limits, and the response cache automatically.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Type,
)

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    HealthReport,
    HealthStatus,
)
from serverframework.extensions.AuthStrategy import AuthStrategy
from serverframework.extensions.federation.BLL_Federation_GQL import (
    APOLLO_SDL_QUERY,
    GQLUpstreamTransport,
    INTROSPECTION_QUERY,
    IngestedSchema,
    MergedSchemaRegistry,
    SDLToPydanticResult,
    SchemaTransformer,
    detect_federation_directives,
    global_registry,
    ingest_apollo_sdl,
    ingest_introspection,
    sdl_to_pydantic_models,
)
from serverframework.lib.Logging import logger
from serverframework.lib.ProviderHTTPClient import (
    ClientPolicy,
    ProviderHTTPClient,
    ProviderHTTPClientSync,
)
from serverframework.logic.BLL_Providers import ProviderInstanceModel


FederationStyle = Literal["apollo_v2", "stitching", "namespaced"]


class AbstractGraphQLProvider(AbstractStaticProvider):
    """Base class for GraphQL upstream providers.

    Concrete subclasses MUST declare:

    * :attr:`upstream_url` — the GraphQL endpoint to introspect and forward to
    * :attr:`federation_style` — apollo_v2 | stitching | namespaced
    * :attr:`type_namespace` — optional prefix applied to upstream type names
    * :attr:`auth_strategy_name` — inherited from :class:`AbstractStaticProvider`

    The class methods :meth:`introspect`, :meth:`register_with_registry`,
    and :meth:`lift_to_pydantic` cover the full federation pipeline; an
    extension's ``post_install`` hook wires them together at startup.
    """

    upstream_url: ClassVar[str] = ""
    federation_style: ClassVar[FederationStyle] = "stitching"
    type_namespace: ClassVar[Optional[str]] = None

    # Optional schema-transformer overrides applied to the raw upstream SDL.
    schema_rename: ClassVar[Dict[str, str]] = {}
    schema_hide_fields: ClassVar[Dict[str, set]] = {}
    schema_mask_arguments: ClassVar[Dict[str, Dict[str, set]]] = {}
    schema_override_resolvers: ClassVar[Dict[str, Callable[..., Any]]] = {}

    # Optional ``@cache(ttl=...)`` directives keyed by upstream type.
    persistent_cache_ttls: ClassVar[Dict[str, float]] = {}

    # Per-provider per-request response cache feature flag. Default on.
    enable_response_cache: ClassVar[bool] = True

    # When True, also lift the upstream SDL into Pydantic models for the
    # local REST/GraphQL surfaces (REST→GQL projection of the GQL upstream).
    lift_into_pydantic: ClassVar[bool] = True

    # Apollo Federation v2 — refusing to negotiate down to stitching when the
    # upstream advertises a subgraph SDL but a transformer would corrupt it.
    require_apollo_v2_when_advertised: ClassVar[bool] = True

    # ----- Lifecycle hooks -----------------------------------------------------

    @classmethod
    @abstractmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> AbstractProviderInstance:
        """Bond a provider instance and (re-)register the upstream subgraph.

        Concrete providers typically override this to bind their auth payload
        and call :meth:`register_with_registry` once the bond is established.
        The base implementation returns a vanilla
        :class:`AbstractProviderInstance` carrying the model.
        """

        return AbstractProviderInstance(instance)

    @classmethod
    def http_client(
        cls,
        *,
        instance: Optional[ProviderInstanceModel] = None,
        policy: Optional[ClientPolicy] = None,
        rate_limit: Optional[Any] = None,
    ) -> ProviderHTTPClient:
        """Build the bound HTTP client used by the federation transport."""

        if instance is None:
            instance = getattr(cls._instance, "model", None) if cls._instance else None
        auth_strategy: Optional[AuthStrategy] = None
        if instance is not None:
            try:
                auth_strategy = cls.build_auth_strategy(
                    instance, getattr(instance, "credentials", None)
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "GraphQL provider %s: auth strategy build failed: %s",
                    cls.__name__,
                    exc,
                )
        return ProviderHTTPClient(
            policy=policy or ClientPolicy(),
            auth_strategy=auth_strategy,
            rate_limit=rate_limit or cls.rate_limit,
            provider_name=cls.__name__,
            provider=cls,
        )

    @classmethod
    def http_client_sync(
        cls,
        *,
        instance: Optional[ProviderInstanceModel] = None,
        policy: Optional[ClientPolicy] = None,
        rate_limit: Optional[Any] = None,
    ) -> ProviderHTTPClientSync:
        if instance is None:
            instance = getattr(cls._instance, "model", None) if cls._instance else None
        auth_strategy: Optional[AuthStrategy] = None
        if instance is not None:
            try:
                auth_strategy = cls.build_auth_strategy(
                    instance, getattr(instance, "credentials", None)
                )
            except Exception:  # pragma: no cover - defensive
                auth_strategy = None
        return ProviderHTTPClientSync(
            policy=policy or ClientPolicy(),
            auth_strategy=auth_strategy,
            rate_limit=rate_limit or cls.rate_limit,
            provider_name=cls.__name__,
            provider=cls,
        )

    @classmethod
    def upstream_transport(
        cls, *, instance: Optional[ProviderInstanceModel] = None
    ) -> GQLUpstreamTransport:
        """Construct a :class:`GQLUpstreamTransport` for outbound calls."""

        client = cls.http_client(instance=instance)
        return GQLUpstreamTransport(http_client=client, upstream_url=cls.upstream_url)

    # ----- Introspection -------------------------------------------------------

    @classmethod
    async def introspect(
        cls, *, instance: Optional[ProviderInstanceModel] = None
    ) -> IngestedSchema:
        """Introspect the upstream and return an :class:`IngestedSchema`.

        For ``apollo_v2`` style, send the ``_service { sdl }`` probe; on
        success ingest the raw SDL with key-field detection. Otherwise fall
        back to a standard introspection query and reconstruct the SDL via
        ``graphql-core``'s ``build_client_schema``.
        """

        client = cls.http_client(instance=instance)
        if cls.federation_style == "apollo_v2":
            try:
                response = await client.post(
                    cls.upstream_url, json={"query": APOLLO_SDL_QUERY}
                )
                sdl = (
                    response.get("data", {}).get("_service", {}).get("sdl")
                    if isinstance(response, dict)
                    else None
                )
                if sdl:
                    return ingest_apollo_sdl(sdl)
                if cls.require_apollo_v2_when_advertised:
                    raise RuntimeError(
                        f"{cls.__name__} declared federation_style=apollo_v2 "
                        f"but upstream {cls.upstream_url!r} did not return "
                        f"a `_service.sdl`."
                    )
            except Exception as exc:
                if cls.require_apollo_v2_when_advertised:
                    raise
                logger.warning(
                    "Apollo Federation v2 probe failed for %s; falling back to "
                    "introspection: %s",
                    cls.__name__,
                    exc,
                )
        # Stitching / namespaced path.
        response = await client.post(
            cls.upstream_url, json={"query": INTROSPECTION_QUERY}
        )
        if not isinstance(response, dict) or "data" not in response:
            raise RuntimeError(
                f"{cls.__name__}: introspection returned unexpected envelope: {response!r}"
            )
        return ingest_introspection(response["data"])

    @classmethod
    def transformer(cls) -> SchemaTransformer:
        """Build the :class:`SchemaTransformer` used to rewrite upstream SDL."""

        return SchemaTransformer(
            rename=dict(cls.schema_rename),
            prefix=cls.type_namespace,
            hide_fields={k: set(v) for k, v in cls.schema_hide_fields.items()},
            mask_arguments={
                k: {fk: set(fv) for fk, fv in fields.items()}
                for k, fields in cls.schema_mask_arguments.items()
            },
            override_resolvers=dict(cls.schema_override_resolvers),
        )

    @classmethod
    async def register_with_registry(
        cls,
        *,
        instance: Optional[ProviderInstanceModel] = None,
        registry: Optional[MergedSchemaRegistry] = None,
    ) -> IngestedSchema:
        """Run the full pipeline and register with the merged registry.

        Returns the post-transformer :class:`IngestedSchema` so callers can
        snapshot SDL hashes for drift detection (Item 11).
        """

        ingested = await cls.introspect(instance=instance)
        return cls._post_introspection_register(ingested, instance, registry)

    @classmethod
    def introspect_sync(
        cls, *, instance: Optional[ProviderInstanceModel] = None
    ) -> IngestedSchema:
        """Synchronous twin of :meth:`introspect`.

        Used by :func:`Federation_Bootstrap.install_external_federation_sync`
        when the federation pipeline runs inside ``ModelRegistry.commit()``,
        which is itself synchronous. Blocks the caller for the duration of
        the upstream HTTP round trip — at startup that's an acceptable cost
        for the seam-time-once-per-process schema lift.
        """

        client = cls.http_client_sync(instance=instance)
        if cls.federation_style == "apollo_v2":
            try:
                response = client.post(
                    cls.upstream_url, json={"query": APOLLO_SDL_QUERY}
                )
                sdl = (
                    response.get("data", {}).get("_service", {}).get("sdl")
                    if isinstance(response, dict)
                    else None
                )
                if sdl:
                    return ingest_apollo_sdl(sdl)
                if cls.require_apollo_v2_when_advertised:
                    raise RuntimeError(
                        f"{cls.__name__} declared federation_style=apollo_v2 "
                        f"but upstream {cls.upstream_url!r} did not return "
                        f"a `_service.sdl`."
                    )
            except Exception as exc:
                if cls.require_apollo_v2_when_advertised:
                    raise
                logger.warning(
                    "Apollo Federation v2 probe failed for %s; falling back to "
                    "introspection: %s",
                    cls.__name__,
                    exc,
                )
        response = client.post(
            cls.upstream_url, json={"query": INTROSPECTION_QUERY}
        )
        if not isinstance(response, dict) or "data" not in response:
            raise RuntimeError(
                f"{cls.__name__}: introspection returned unexpected envelope: {response!r}"
            )
        return ingest_introspection(response["data"])

    @classmethod
    def register_with_registry_sync(
        cls,
        *,
        instance: Optional[ProviderInstanceModel] = None,
        registry: Optional[MergedSchemaRegistry] = None,
    ) -> IngestedSchema:
        """Synchronous twin of :meth:`register_with_registry`."""

        ingested = cls.introspect_sync(instance=instance)
        return cls._post_introspection_register(ingested, instance, registry)

    @classmethod
    def _post_introspection_register(
        cls,
        ingested: IngestedSchema,
        instance: Optional[ProviderInstanceModel],
        registry: Optional[MergedSchemaRegistry],
    ) -> IngestedSchema:
        """Shared transform → register tail used by both async and sync paths."""

        transformer = cls.transformer()
        rewritten_sdl = transformer.transform(ingested.sdl)
        if cls.federation_style == "apollo_v2":
            keys = detect_federation_directives(rewritten_sdl)
        else:
            keys = {}
        rewritten = IngestedSchema(
            sdl=rewritten_sdl,
            apollo_v2=ingested.apollo_v2,
            key_fields=keys or ingested.key_fields,
        )
        target = registry or global_registry()
        target.register(
            name=cls.__name__,
            ingested=rewritten,
            style=cls.federation_style,
            namespace=cls.type_namespace,
            transport=cls.upstream_transport(instance=instance).send,
            cache_ttls=cls.persistent_cache_ttls,
        )
        return rewritten

    @classmethod
    def lift_to_pydantic(cls, ingested: IngestedSchema) -> SDLToPydanticResult:
        """Lift the upstream SDL into Pydantic models.

        Concrete providers that want their upstream to appear on the local
        REST surface in addition to the GraphQL surface call this after
        :meth:`register_with_registry` and feed the returned models to the
        framework's ModelRegistry. The lift is opt-in via
        :attr:`lift_into_pydantic` (default True).
        """

        if not cls.lift_into_pydantic:
            return SDLToPydanticResult()
        return sdl_to_pydantic_models(ingested.sdl, prefix=cls.type_namespace)

    # ----- Health -------------------------------------------------------------

    @classmethod
    def health_check(cls) -> HealthReport:
        """Send a tiny ``__typename`` probe to confirm the upstream is reachable."""

        try:
            client = cls.http_client_sync()
            response = client.post(cls.upstream_url, json={"query": "{__typename}"})
            if isinstance(response, dict) and "data" in response:
                return HealthReport(HealthStatus.OK, detail="upstream introspection OK")
            return HealthReport(
                HealthStatus.DEGRADED, detail=f"unexpected envelope: {response!r}"
            )
        except Exception as exc:
            return HealthReport(HealthStatus.DOWN, detail=str(exc))


__all__ = ["AbstractGraphQLProvider", "FederationStyle"]
