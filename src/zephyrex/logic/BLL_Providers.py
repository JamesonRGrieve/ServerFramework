# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio
import inspect
import random
import time
import uuid
import warnings
from dataclasses import dataclass
from decimal import Decimal
from typing import (
    Any,
    Callable,
    ClassVar,
    Collection,
    Dict,
    Generator,
    Iterator,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

import stringcase
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_serializer, model_validator


    """Shared name-length validator for Create models."""
    if instance.name and len(instance.name) < 2:
        raise ValueError(f"{entity_label} must be at least 2 characters long")
    return instance


from zephyrex.database.DatabaseManager import DatabaseManager
from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Metrics import get_metrics_backend
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.logic.BLL_Extensions import AbilityModel, ExtensionModel


    """Raised by `validate_manager_constructors` when a manager violates the
    documented `model_registry`-first contract (Item 70)."""


    """Derive a canonical provider name from a provider class.

    Checks for an explicit ``name`` attribute first; falls back to stripping
    ``Provider`` / ``PRV_`` from the class name.  When *warn_missing* is True
    a warning is logged on the fallback path (useful during seeding).
    """
    if hasattr(provider_class, "name") and provider_class.name:
        return str(provider_class.name)
    name: str = provider_class.__name__.replace("Provider", "").replace("PRV_", "")
    if warn_missing:
        logger.warning(
            "Provider class %s missing 'name' attribute, using %s",
            provider_class.__name__,
            name,
        )
    return name


    """Rotation step: call the rotated callable with this provider instance.
    The driver sends back the result or throws the raised exception into the
    step generator, where the rotation policy handles it."""

    provider_instance: Any
    args: tuple
    kwargs: Dict[str, Any]


    """Rotation step: back off for ``seconds`` before continuing."""

    seconds: float


RotationSteps = Generator[Union[_CallProvider, _Wait], Any, Any]


    """Run rotation steps synchronously (``RotationManager.rotate``)."""
    try:
        step = next(steps)
        while True:
            if isinstance(step, _Wait):
                time.sleep(step.seconds)
                step = steps.send(None)
                continue
            try:
                result = callable_func(
                    step.provider_instance, *step.args, **step.kwargs
                )
                step = steps.throw(exc)
            else:
                step = steps.send(result)
    except StopIteration as done:
        return done.value


    """Run rotation steps on the event loop (``RotationManager.arotate``).

    The callable's awaitable is awaited inside the attempt, so a failing
    coroutine is retried/advanced by the rotation policy, and backoff waits
    do not block the loop."""
    try:
        step = next(steps)
        while True:
            if isinstance(step, _Wait):
                await asyncio.sleep(step.seconds)
                step = steps.send(None)
                continue
            try:
                result = callable_func(
                    step.provider_instance, *step.args, **step.kwargs
                )
                if inspect.isawaitable(result):
                    result = await result
                step = steps.throw(exc)
            else:
                step = steps.send(result)
    except StopIteration as done:
        return done.value


    """Canonical name of an extension's root rotation: each underscore-separated
    part PascalCased (``auth_mfa`` -> ``Root_Auth_Mfa``). The single source for
    the rotation seeder, the rotation/provider-instance link seeder and
    ``AbstractStaticExtension.root``."""
    parts = (stringcase.pascalcase(part) for part in extension_name.split("_"))
    return f"Root_{'_'.join(parts)}"


# The scope the operator's seeded ``Root_<Provider>`` instances are in.
ROOT_INSTANCE_SCOPE = "root"


def root_instance_name(provider_name: str) -> str:
    """Name of the operator's instance the framework seeds for a provider
    (``stripe`` -> ``Root_Stripe``). The single source for the instance
    seeder and the rotation/provider-instance link seeder."""
    return f"Root_{stringcase.pascalcase(provider_name)}"


    """Whether the provider takes any setting from the server's environment
    (a declared setting naming a variable, or a legacy ``_env``). Only such
    a provider gets a seeded ``Root_<Provider>``: that instance exists to
    hold the operator's environment default. One with none (ERPNext) is set
    up deliberately, in the scope its operator chooses (operator decision),
    so no instance shared with everyone appears on its own."""
    if getattr(provider_class, "_env", None):
        return True
    return any(
        getattr(setting, "env", None)
        for setting in getattr(provider_class, "instance_settings", ()) or ()
    )


    """Return the ``ExtensionRegistry`` from *model_registry*, or ``None``
    when it is absent or empty.  Replaces the 3-line guard that was
    duplicated across every ``seed_data`` classmethod.
    """
    if (
        model_registry
        and hasattr(model_registry, "extension_registry")
        and model_registry.extension_registry
    ):
        return model_registry.extension_registry
    return None


def _iter_extension_providers(
    model_registry: Any,
) -> Iterator[Tuple[str, list]]:
    """Yield ``(ext_name, providers)`` tuples from *model_registry*'s
    ``ExtensionRegistry``.  Yields nothing when the registry is absent or
    empty.
    """
    ext_reg = _get_extension_registry(model_registry)
    if ext_reg is not None:
        yield from ext_reg.extension_providers.items()


# Item 2 — per-process auth cooldown tracker. Module-level so the
# multi-instance state survives RotationManager construction across
# requests. Keyed by `provider_instance_id`; value is the monotonic time
# at which the cooldown expires. Multi-PROCESS deployments accept
# independent per-process state until Item 69's DistributedCounter
# is wired through.
_AUTH_COOLDOWNS: Dict[str, float] = {}


    """Test helper: clear the in-process auth cooldown tracker."""
    _AUTH_COOLDOWNS.clear()


# Item 51 — sticky-session routing primitives.
    """Per-call routing hint passed to `RotationManager.rotate`.

    Item 51: when `stickiness_key` is non-None, the rotation system pins
    repeated calls within that key to the first provider that succeeded
    for it (scoped by `(stickiness_key, ability)`), until the pin expires
    or the pinned provider becomes unhealthy. Default behavior (linear
    failover) is unchanged when no hint is supplied. Canary routing is
    intentionally NOT a field here — per Item 3 it remains L7's job.
    """

    stickiness_key: Optional[str] | None = None


# Item 51 — in-memory sticky-session cache. Keyed by
# `(stickiness_key, ability)`; value is `(provider_instance_id, expires_at)`
# where `expires_at` is a monotonic-time deadline. Multi-PROCESS
# deployments accept session-affinity loss across processes (acceptable
# for many use cases) or back this with Redis (out of scope here).
_STICKY_SESSIONS: Dict[Tuple[str, Optional[str]], Tuple[str, float]] = {}


    """Configurable TTL via env, default 600s."""
    raw = env("STICKY_SESSION_TTL_SECONDS")
    if not raw:
        return 600.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 600.0


    """Test helper: clear the in-process sticky-session cache."""
    _STICKY_SESSIONS.clear()


    """Return the pinned provider_instance_id for `(stickiness_key, ability)`
    if present and not expired; otherwise None. Expired entries are evicted."""
    key = (stickiness_key, ability)
    pinned = _STICKY_SESSIONS.get(key)
    if pinned is None:
        return None
    pi_id, expires_at = pinned
    if time.monotonic() >= expires_at:
        _STICKY_SESSIONS.pop(key, None)
        return None
    return pi_id


def _sticky_set(
    stickiness_key: str, ability: Optional[str], provider_instance_id: str
) -> None:
    """Pin `provider_instance_id` for `(stickiness_key, ability)` with the
    configured TTL."""
    expires_at = time.monotonic() + _sticky_ttl_seconds()
    _STICKY_SESSIONS[(stickiness_key, ability)] = (
        str(provider_instance_id),
        expires_at,
    )


    """Drop the pin for `(stickiness_key, ability)` (e.g. on health failure
    or pinned-provider error)."""
    _STICKY_SESSIONS.pop((stickiness_key, ability), None)


    """Walk each manager class's `__init__` signature and assert the first
    non-self parameter is named `model_registry`.

    Per Item 70: every BLL manager must accept `model_registry` as its
    first constructor parameter. This function is intentionally NOT wired
    into framework startup — see Item 70's notes. Use it from a CI-time
    test or call manually during a registry-finalize pass.

    Raises:
        ManagerContractError: First offending manager's name + actual signature.
    """
    for cls in managers:
        sig = inspect.signature(cls.__init__)  # type: ignore[misc]
        params = [p for p in sig.parameters.values() if p.name != "self"]
        if not params:
            raise ManagerContractError(
                f"{cls.__name__}.__init__ has no parameters; expected first to be 'model_registry'"
            )
        first = params[0].name
        if first != "model_registry":
            raise ManagerContractError(
                f"{cls.__name__}.__init__ first parameter is {first!r}; "
                "must be 'model_registry' (Item 70 contract)"
            )


class ProviderModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    name: str = Field(..., description="The name")
    friendly_name: Optional[str] | None = None
    agent_settings_json: Optional[str] | None = None

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A Provider represents an external provider of data or functionality. It represents an extension provider. Settings should exclude model name and api key, as those are stored in provider instance as fields."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")
    seed_list: ClassVar[List[Dict[str, Any]]] = []

        """Dynamically discover and return provider seed data."""
        try:
            logger.debug("Discovering providers for seeding...")

            seed_data = []
            found_any = False

            for ext_name, providers in _iter_extension_providers(model_registry):
                found_any = True
                logger.debug(
                    f"Processing {len(providers)} providers for extension: {ext_name}"
                )

                for provider_class in providers:
                    provider_name = _resolve_provider_name(
                        provider_class, warn_missing=True
                    )

                    provider_data = {
                        "name": provider_name,
                        "friendly_name": f"{provider_name} Provider",
                        "agent_settings_json": None,
                        "system": True,
                    }
                    seed_data.append(provider_data)
                    logger.debug(
                        f"Added provider {provider_name} to seed data for extension {ext_name}"
                    )

            if not found_any:
                logger.debug("No loaded extension ships providers; nothing to seed")

            logger.debug(f"Total providers in seed data: {len(seed_data)}")
            return seed_data

            logger.error(f"Error discovering providers for seeding: {e}")
            return []

        name: str
        friendly_name: Optional[str] | None = None
        agent_settings_json: Optional[str] | None = None
        system: bool = False

        @model_validator(mode="after")
        def validate_name_length(self):
            return _validate_name_min_length(self, "Provider name")

        name: Optional[str] | None = None
        friendly_name: Optional[str] | None = None
        agent_settings_json: Optional[str] | None = None
        system: Optional[bool] | None = None

        friendly_name: Optional[StringSearchModel] | None = None
        agent_settings_json: Optional[StringSearchModel] | None = None
        system: Optional[bool] | None = None


    """One environment setting a provider reads. A secret's value is never
    returned; ``set`` says whether it has one."""

    key: str
    secret: bool
    set: bool
    value: Optional[str] = None


    """A live upstream check, from ``health_check`` (cached briefly)."""

    status: Literal["ok", "degraded", "down"]
    detail: str


    provider: str
    extension: str
    configured: bool
    settings: List[RootProviderSetting]
    # Only when asked for: it calls every listed provider's upstream.
    health: Optional[RootProviderHealth] = None


    providers: List[RootProviderStatus]


    key: str
    description: str
    env: Optional[str] = Field(
        None, description="Environment variable it falls back to"
    )
    default: Optional[str] = None
    write_only: bool = Field(False, description="A secret: never returned once set")
    field: Optional[str] = Field(
        None, description="An instance column (api_key, model_name), not a setting row"
    )
    multiline: bool = Field(
        False, description="The value spans lines (a PEM key, a JSON credentials file)"
    )


    provider_id: str
    provider: str
    settings: List[InstanceSettingInfo]


    """Whose records a provider instance's work produces: the requester to
    write them as, and the user and team they belong to."""

    requester_id: str
    user_id: Optional[str] = None
    team_id: Optional[str] = None


    """The owner of a provider instance (an account, a store): its user,
    else whoever created it, else root. Records an instance's work
    produces are written as this owner, so whoever can see the instance
    sees them; a root instance's stay root's."""
    instance = ProviderInstanceManager(
    ).get(id=provider_instance_id)
    return InstanceOwner(
    )


    """The loaded provider class called ``name``, among the attached app's
    extensions; None when no loaded extension has one."""
    extension_registry = getattr(model_registry, "extension_registry", None)
    for providers in (
        extension_registry.extension_providers.values() if extension_registry else []
    ):
        for provider_class in providers:
            if getattr(provider_class, "name", None) == name:
                return provider_class
    return None


    """Each environment variable a provider reads, mapped to whether its value
    is a secret. A typed ``Settings`` model (Item 90) is authoritative: its
    ``SecretStr`` fields are secret. Otherwise the legacy ``_env`` dict names
    the variables. Either way a credential-like name is also treated as
    secret, so a mis-typed field can't print its value."""
    from pydantic import SecretStr

    from zephyrex.lib.Credentials import is_secret_setting
    from zephyrex.lib.TypeUnions import unwrap_optional

    settings_model = getattr(provider_cls, "Settings", None)
    env_field_map = getattr(settings_model, "env_field_map", None)
    if settings_model is not None and callable(env_field_map) and env_field_map():
        fields = settings_model.model_fields
        return {
            env_name: is_secret_setting(env_name)
            or (
                field_name in fields
                and unwrap_optional(fields[field_name].annotation) is SecretStr
            )
            for field_name, env_name in env_field_map().items()
        }
    legacy_env = getattr(provider_cls, "_env", None) or {}
    return {key: is_secret_setting(key) for key in dict.keys(legacy_env)}


def _provider_health(provider_cls: Any) -> RootProviderHealth:
    """The provider's cached health; a check that raises is itself down."""
    try:
        report = provider_cls.cached_health_check()
    except Exception as exc:  # noqa: BLE001 - a failing check is a down provider
        return RootProviderHealth(
            status="down", detail=f"health check failed: {type(exc).__name__}"
        )
    return RootProviderHealth(status=report.status.value, detail=report.detail)


    """How each loaded provider's environment (root) configuration stands,
    optionally for one extension and with a live health check."""
    statuses = []
    for extension_name, providers in sorted(extension_providers.items()):
        if extension is not None and extension_name != extension:
            continue
        for provider_cls in providers:
            settings = []
            for key, secret in sorted(_provider_env_settings(provider_cls).items()):
                raw = env(key)
                value = None if raw is None else str(raw)
                settings.append(
                    RootProviderSetting(
                        key=key,
                        secret=secret,
                        set=bool(value and value.strip()),
                        value=None if secret else value,
                    )
                )
            statuses.append(
                RootProviderStatus(
                    provider=provider_cls.name,
                    extension=extension_name,
                    configured=provider_cls.is_configured(),
                    settings=settings,
                    health=_provider_health(provider_cls) if health else None,
                )
            )
    return RootProviderStatusResponse(providers=statuses)


    _model = ProviderModel

    # RouterMixin configuration for testing
    prefix: ClassVar[Optional[str]] = "/v1/provider"
    tags: ClassVar[Optional[List[str]]] = ["Provider Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "api_key_auth"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ) -> None:
        """
        Initialize ProviderManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            db: Database session (optional)
            db_manager: Database manager instance (required)
            model_registry: Model registry for dynamic model handling (optional)
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._extensions = None
        self._instances = None
        self._rotations = None

    @custom_route(
        method="GET",
        path="/root/status",
        output_model=RootProviderStatusResponse,
        authentication_type="jwt",
        summary="Environment configuration of every loaded provider (root only)",
    )
    def root_status_route(
        self, extension: Optional[str] = None, health: bool = False
    ) -> RootProviderStatusResponse:
        """Which providers are configured from the environment, setting by
        setting; secrets report only whether they are set. ``?extension=``
        narrows to one extension's providers; ``?health=true`` adds each
        one's live upstream check."""
        from zephyrex.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(status_code=403, detail="Root only")
        extension_registry = self.model_registry.extension_registry
        return root_provider_status(
            extension_registry.extension_providers if extension_registry else {},
            extension=extension,
            health=health,
        )

    @custom_route(
        method="GET",
        path="/{provider_id}/settings",
        output_model=ProviderSettingsCatalogue,
        authentication_type="jwt",
        summary="The settings each instance of this provider reads",
    )
    def settings_catalogue_route(self, provider_id: str) -> ProviderSettingsCatalogue:
        """What a client offers when configuring an instance: each setting's
        key, meaning, environment fallback and default, and whether it is a
        write-only secret."""
        provider = self.get(id=provider_id)
        provider_class = provider_class_named(self.model_registry, provider.name)
        declared = provider_class.instance_settings if provider_class else ()
        return ProviderSettingsCatalogue(
            provider_id=provider.id,
            provider=provider.name,
            settings=[
                InstanceSettingInfo(
                    key=setting.key,
                    description=setting.description,
                    env=setting.env,
                    default=setting.default,
                    write_only=setting.secret,
                    field=setting.field,
                    multiline=setting.multiline,
                )
                for setting in declared
            ],
        )

        """Get the provider extension manager."""
        if self._extensions is None:
            # Import locally to avoid circular imports
            self._extensions = ProviderExtensionManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._extensions  # type: ignore[return-value]

        """Get the provider instance manager."""
        if self._instances is None:
            # Import locally to avoid circular imports
            self._instances = ProviderInstanceManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._instances  # type: ignore[return-value]

        """Get the rotation manager."""
        if self._rotations is None:
            # Import locally to avoid circular imports
            self._rotations = RotationManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._rotations  # type: ignore[return-value]

    @staticmethod
    def list_runtime_providers():
        return ["OpenAI", "AGInYourPC"]

        if provider_name == "OpenAI":
            return {"OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-4"}
        elif provider_name == "AGInYourPC":
            return {"AGINYOURPC_API_KEY": "", "AGINYOURPC_AI_MODEL": "local"}
        return {}

        """
        Static method to list rotations without requiring a manager instance.
        Used for extension root rotation lookup.

        Args:
            db_manager: DatabaseManager instance for database operations
        """
        session = db_manager.get_session()

        try:
            return cls.Model.DB(db_manager.Base).list(
                db=session,
                return_type="dto",
                override_dto=cls.Model,
                **{k: v for k, v in kwargs.items() if k != "created_by_user_id"},
            )
        finally:
            session.close()


class ProviderExtensionModel(
    ApplicationModel,
    UpdateMixinModel,
    ProviderModel.Reference,
    ExtensionModel.Reference,
    metaclass=ModelMeta,
):
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderExtension represents Provider support for an Extension."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

        """Dynamically create provider-extension links based on discovered providers and extensions."""
        try:
            logger.debug("Creating provider-extension links for seeding...")

            seed_data = []
            found_any = False

            for ext_name, providers in _iter_extension_providers(model_registry):
                found_any = True
                for provider_class in providers:
                    provider_name = _resolve_provider_name(provider_class)

                    link_data = {
                        "_provider_name": provider_name,  # Will be resolved to provider_id
                        "_extension_name": ext_name,  # Will be resolved to extension_id
                    }
                    seed_data.append(link_data)
                    logger.debug(
                        f"Added ProviderExtension link for provider '{provider_name}' and extension '{ext_name}'"
                    )

            if not found_any:
                logger.debug(
                    "No loaded extension ships providers; no provider links to seed"
                )

            logger.debug(
                f"Total provider-extension links in seed data: {len(seed_data)}"
            )
            return seed_data

            logger.error(f"Error creating provider-extension links: {e}")
            return []

        provider_id: str
        extension_id: str

        provider_id: Optional[str] | None = None
        extension_id: Optional[str] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
    ):
        provider_id: Optional[StringSearchModel] | None = None
        extension_id: Optional[StringSearchModel] | None = None


    _model = ProviderExtensionModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/provider/extension"
    tags: ClassVar[Optional[List[str]]] = ["Provider Extension Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "api_key_auth"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ) -> None:
        """
        Initialize ProviderExtensionManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            db: Database session (optional)
            db_manager: Database manager instance (required)
            model_registry: Model registry for dynamic model handling (optional)
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._ability = None

        """Get the provider extension ability manager."""
        if self._ability is None:
            # Import locally to avoid circular imports
            self._ability = ProviderExtensionAbilityManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._ability  # type: ignore[return-value]

        """Validate provider extension creation - check that provider and extension exist."""
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        if entity.provider_id:
            provider = ProviderModel.DB(self.model_registry.DB.manager.Base).get(
                model_registry=self.model_registry,
                id=entity.provider_id,
            )
            if not provider:
                raise HTTPException(status_code=404, detail="Provider not found")

        if entity.extension_id:
            extension = ExtensionModel.DB(self.model_registry.DB.manager.Base).get(
                model_registry=self.model_registry,
                id=entity.extension_id,
            )
            if not extension:
                raise HTTPException(status_code=404, detail="Extension not found")


class ProviderExtensionAbilityModel(
    ApplicationModel,
    UpdateMixinModel,
    ProviderExtensionModel.Reference,
    AbilityModel.Reference,
    metaclass=ModelMeta,
):
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderExtensionAbility represents a ProviderExtension and Ability combination. This allows for a provider to provide partial functionality to an extension, for example SendGrid only provides sending email, but not an inbox."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

        provider_extension_id: str
        ability_id: str

        provider_extension_id: Optional[str] | None = None
        ability_id: Optional[str] | None = None

        provider_extension_id: Optional[StringSearchModel] | None = None
        ability_id: Optional[StringSearchModel] | None = None


    _model = ProviderExtensionAbilityModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/extension/ability/provider"
    tags: ClassVar[Optional[List[str]]] = ["Provider Extension Ability Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "api_key_auth"

        """Validate provider extension ability creation - check that provider extension and ability exist."""
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        if entity.provider_extension_id:
            provider_extension = ProviderExtensionModel.DB(
                self.model_registry.DB.manager.Base
            ).get(
                model_registry=self.model_registry,
                id=entity.provider_extension_id,
            )
            if not provider_extension:
                raise HTTPException(
                    status_code=404, detail="Provider extension not found"
                )

        if entity.ability_id:
            ability = AbilityModel.DB(self.model_registry.DB.manager.Base).get(
                model_registry=self.model_registry,
                id=entity.ability_id,
            )
            if not ability:
                raise HTTPException(status_code=404, detail="Ability not found")


class ProviderInstanceModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    ProviderModel.Reference,
    metaclass=ModelMeta,
):
    model_name: Optional[str] | None = None
    # Write-only: excluded from every serialization (responses, the entity
    # cache) and from GraphQL types; providers read the attribute.
    api_key: Optional[str] | None = Field(
        None, exclude=True, description="Credential for the provider (write-only)"
    )
    enabled: Optional[bool] = Field(True, description="Whether it is enabled")
    # Item 19: provider scope. Resolution semantics differ by scope:
    #   * root   -> framework-internal only; never reachable via user-context resolution.
    #   * system -> SaaS-owned; user-invokable, metered against unified Quota.
    #   * team   -> team-owned; user-invokable.
    #   * user   -> user-owned; user-invokable.
    # Defaults to "user" for backwards compatibility with rows written before
    # this column existed.
    scope: Literal["root", "system", "team", "user"] = Field(
        "user",
        description="Provider scope: root (framework-internal), system (SaaS-owned), team (team-owned), user (user-owned).",
    )
    # Item 36: physical placement of this provider instance (e.g. "eu-west-1").
    # Consumed by the unmerged residency extension to filter chains by jurisdiction.
    region: Optional[str] = Field(
        default=None,
        description="Item 36 residency region (consumed by the residency extension).",
    )
    # Item 10 — per-instance AuthStrategy override. NULL inherits the
    # provider class's `auth_strategy_name` ClassVar. Bonding consults this
    # field via `AbstractStaticProvider.build_auth_strategy(instance, ...)`.
    # Use case: a Stripe provider class defaulting to "api_key" lets a
    # Stripe Connect user override their own instance to "oauth2" without
    # touching the provider class.
    auth_strategy_name: Optional[str] = Field(
        default=None,
        description=(
            "Item 10 per-instance AuthStrategy name override. NULL inherits "
            "the provider class default. Common values: 'api_key', 'oauth2', "
            "'jwt_bearer', 'basic', 'mtls', 'aws_sigv4'."
        ),
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstance represents a User or Team's instance of a Provider. They can have multiple of the same Provider."
    )
    is_system_entity: ClassVar[bool] = False
    seed_creator_id: ClassVar[str] = env("ROOT_ID")

        """The operator's ``Root_<Provider>`` instance of each loaded
        provider, root-scoped: it is the operator's, so it alone (with the
        system-scoped ones) defaults to the server's environment."""
        try:
            logger.debug("Discovering provider instances for seeding...")

            seed_data = []
            found_any = False

            for ext_name, providers in _iter_extension_providers(model_registry):
                found_any = True
                logger.debug(
                    f"Processing provider instances for {len(providers)} providers for extension: {ext_name}"
                )

                for provider_class in providers:
                    provider_name = _resolve_provider_name(
                        provider_class, warn_missing=True
                    )
                    if not reads_environment_settings(provider_class):
                        logger.debug(
                            f"Provider '{provider_name}' takes nothing from the "
                            "environment: no Root_ instance seeded"
                        )
                        continue

                    provider_instance_name = root_instance_name(provider_name)

                    provider_data = {
                        "_provider_name": provider_name,  # Will be resolved to provider_id
                        "name": provider_instance_name,
                        "model_name": provider_name,
                        "api_key": None,  # Will be set by extension hooks
                        "scope": ROOT_INSTANCE_SCOPE,
                    }

                    envs = provider_class._env
                    for env_name, env_value in envs.items():
                        if "API_KEY" in env_name:
                            provider_data["api_key"] = env(env_name) or env_value
                            break

                    seed_data.append(provider_data)
                    logger.debug(
                        f"Added provider instance {provider_instance_name} to seed data for extension {ext_name}"
                    )

            if not found_any:
                logger.debug("No loaded extension ships providers; nothing to seed")

            logger.debug(f"Total provider instances in seed data: {len(seed_data)}")
            return seed_data

            logger.error(f"Error discovering provider instances for seeding: {e}")
            return []

    # ``str | None``: inside a model body ``Optional`` is the model's nested
    # ``Optional`` class, not ``typing.Optional``.
        """This instance's ``ProviderInstanceSetting`` value for ``key``, or
        ``default`` when the instance has no such setting.

        Read against the attached app's registry: providers run inside
        rotations, which have no request to take a registry from.

        Every live row applies, whatever its author's role has since become:
        ProviderInstanceChildManager checks each write, and the migration
        ``provider_instance_settings_planted`` removed the rows planted
        before it did. A deleted row does not apply (ROOT's reads include
        deleted rows, so they are filtered out).
        """
        from zephyrex.pydantic2.registry import ModelRegistry

        registry = ModelRegistry.attached()
        if registry is None:
            raise RuntimeError("No app registry attached to read provider settings")
        rows = ProviderInstanceSettingModel.DB(registry.DB.manager.Base).list(
            model_registry=registry,
            return_type="dto",
            override_dto=ProviderInstanceSettingModel,
            provider_instance_id=self.id,
            key=key,
        )
        from zephyrex.lib.SecretEncryption import decrypt_secret

        for row in rows:
            if row.value is not None:
                return (
                    decrypt_secret(str(row.value)) if row.write_only else str(row.value)
                )
        return default

        name: str
        provider_id: str
        model_name: Optional[str] | None = None
        api_key: Optional[str] | None = None
        user_id: Optional[str] | None = None
        team_id: Optional[str] | None = None
        scope: Literal["root", "system", "team", "user"] = "user"
        # Item 36: residency region (optional; consumed by residency extension).
        region: Optional[str] | None = None
        # Item 10: per-instance AuthStrategy override (optional).
        auth_strategy_name: Optional[str] | None = None

        @model_validator(mode="after")
        def validate_name_length(self):
            return _validate_name_min_length(self, "Provider instance name")

        name: Optional[str] | None = None
        provider_id: Optional[str] | None = None
        model_name: Optional[str] | None = None
        api_key: Optional[str] | None = None
        user_id: Optional[str] | None = None
        team_id: Optional[str] | None = None
        scope: Optional[Literal["root", "system", "team", "user"]] | None = None
        enabled: Optional[bool] | None = None
        # Item 36: residency region (optional; consumed by residency extension).
        region: Optional[str] | None = None
        # Item 10: per-instance AuthStrategy override (optional).
        auth_strategy_name: Optional[str] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        ProviderModel.Reference.ID.Search,
    ):
        model_name: Optional[StringSearchModel] | None = None
        scope: Optional[StringSearchModel] | None = None
        # Item 36: residency region (consumed by residency extension).
        region: Optional[StringSearchModel] | None = None
        # Item 10: per-instance AuthStrategy override (search-by-name).
        auth_strategy_name: Optional[StringSearchModel] | None = None


# Instance scopes that speak for the operator rather than a user or team.
OPERATOR_SCOPES = frozenset({"root", "system"})


    """A row that belongs to one provider instance (its settings, its
    usage, its enabled abilities). Providers read these on the server's
    behalf as ROOT, so a row on an instance shapes what that instance does
    (a planted ``api_base`` would send its credential elsewhere). The model
    inherits access from the instance (``permission_references``), and
    writing one takes what changing the instance takes:

    - the instance is visible to the requester, or it is not found (404);
    - a root- or system-scoped instance speaks for the operator, so only
      ROOT and SYSTEM write its rows (403);
    - anyone else needs edit rights on the instance (403);
    - a row never moves to another instance (400).
    """


        if not provider_instance_id:
            raise HTTPException(
                status_code=400, detail="provider_instance_id is required"
            )
        base = self.model_registry.DB.manager.Base
        instance_db = ProviderInstanceModel.DB(base)
        instance = instance_db.get(
            model_registry=self.model_registry,
            id=provider_instance_id,
            return_type="dto",
            override_dto=ProviderInstanceModel,
            allow_nonexistent=True,
        )
        if instance is None:
            raise HTTPException(status_code=404, detail="Provider instance not found")
        if server_side(requester_id):
            return
        if instance.scope in OPERATOR_SCOPES:
            raise HTTPException(
                status_code=403,
                detail=f"Only the operator configures {instance.scope}-scoped instances",
            )
        session = self.model_registry.DB.session()
        try:
                requester_id,
                instance_db,
                provider_instance_id,
                session,
                declarative_base=base,
            )
        finally:
            session.close()
        if not editable:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to change this provider instance",
            )

        """The row ``id`` (404 when invisible), once its instance is one
        the requester may change and ``moved_to`` (if given) is that same
        instance."""
        existing = self.get(id=id)
        if moved_to is not None and moved_to != existing.provider_instance_id:
            raise HTTPException(
                status_code=400,
                detail="A row stays on its provider instance",
            )
        self._require_writable_instance(existing.provider_instance_id)
        return existing

    def create_validation(self, entity: Any) -> None:
        self._require_writable_instance(entity.provider_instance_id)

    def update(self, id: str, **kwargs: Any) -> Any:
        self._require_writable_row(id, kwargs.get("provider_instance_id"))
        return super().update(id, **kwargs)

        self._require_writable_row(id)
        super().delete(id)


    _model = ProviderInstanceModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/provider/instance"
    tags: ClassVar[Optional[List[str]]] = ["Provider Instance Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ) -> None:
        """
        Initialize ProviderInstanceManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            db: Database session (optional)
            db_manager: Database manager instance (required)
            model_registry: Model registry for dynamic model handling (optional)
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._usage = None
        self._setting = None
        self._ability = None

        """Get the provider instance usage manager."""
        if self._usage is None:
            # Import locally to avoid circular imports
            self._usage = ProviderInstanceUsageManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._usage  # type: ignore[return-value]

        """Get the provider instance setting manager."""
        if self._setting is None:
            # Import locally to avoid circular imports
            self._setting = ProviderInstanceSettingManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._setting  # type: ignore[return-value]

        """Get the provider instance extension ability manager."""
        if self._ability is None:
            # Import locally to avoid circular imports
            self._ability = ProviderInstanceExtensionAbilityManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._ability  # type: ignore[return-value]

    # Only the caller's own id may be claimed as an instance's user.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)

        """An instance in the root or system scope speaks for the operator
        (an extension may trust what it vouches for), so only ROOT and
        SYSTEM may put one there."""
        if scope not in OPERATOR_SCOPES:
            return
        if not server_side(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail=f"Only the operator may create {scope}-scoped instances",
            )

        self._refuse_operator_scope(kwargs.get("scope"))
        return super().update(id, **kwargs)

        """Validate provider instance creation - check that provider exists."""
        self._refuse_operator_scope(entity.scope)
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        if entity.provider_id:
            provider = ProviderModel.DB(self.model_registry.DB.manager.Base).get(
                model_registry=self.model_registry,
                id=entity.provider_id,
            )
            if not provider:
                raise HTTPException(status_code=404, detail="Provider not found")


class ProviderInstanceUsageModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    provider_instance_id: str
    key: Optional[str] = Field(
        None,
        description="Optional key to differentiate between usage records (for example AI model input and output tokens).",
    )
    value: Optional[int] = Field(
        0,
        description="Optional key to differentiate between usage records (for example AI model input and output tokens).",
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstanceUsage represents a User's usage of a provider. If team_id is also populated, it was used on behalf of a team by a user. Lack of a record means a user has never used the ProviderInstance. Note that ProviderInstances lower on a rotation may be seldom/never used."
    )
    permission_references: ClassVar[List[str]] = ["provider_instance"]

        provider_instance_id: str
        key: Optional[str] | None = None
        value: Optional[int] | None = None
        user_id: Optional[str] | None = None
        team_id: Optional[str] | None = None

        provider_instance_id: Optional[str] | None = None
        key: Optional[str] | None = None
        value: Optional[int] | None = None
        user_id: Optional[str] | None = None
        team_id: Optional[str] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] | None = None
        key: Optional[StringSearchModel] | None = None
        value: Optional[NumericalSearchModel] | None = None


    """Usage is recorded by the server (as ROOT) against the user a call
    ran for; anyone else records it only on an instance they may change,
    and only as themselves."""

    _model = ProviderInstanceUsageModel
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/provider/instance/usage"
    tags: ClassVar[Optional[List[str]]] = ["Provider Instance Usage Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_provider_manager"


class ProviderInstanceSettingModel(
    ApplicationModel,
    UpdateMixinModel,
    ProviderInstanceModel.Reference,
    metaclass=ModelMeta,
):
    key: str
    value: Optional[str] | None = None
    # Set by the server when the instance's provider declares ``key``
    # secret: the value is stored encrypted and never returned.
    write_only: bool = Field(
        False, description="Its value is stored encrypted and never returned"
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstanceSetting represents a non-default configuration setting for a User or Team's instance of an Provider."
    )
    permission_references: ClassVar[List[str]] = ["provider_instance"]

    # ``str | None``: in a model body ``Optional`` is the nested class.
    @field_serializer("value")
    def _withhold_secret(self, value: str | None) -> str | None:
        return None if self.write_only else value

    is_system_entity: ClassVar[bool] = False

        provider_instance_id: str
        key: str
        value: Optional[str] | None = None
        # A caller may make any value write-only; the server does for every
        # key the provider declares secret.
        write_only: bool = False

        provider_instance_id: Optional[str] | None = None
        key: Optional[str] | None = None
        value: Optional[str] | None = None
        write_only: Optional[bool] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] | None = None
        key: Optional[StringSearchModel] | None = None
        value: Optional[StringSearchModel] | None = None


    _model = ProviderInstanceSettingModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/provider/instance/setting"
    tags: ClassVar[Optional[List[str]]] = ["Provider Instance Settings"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"

        """A setting has a key, on an instance the requester may change."""
        if not entity.key:
            raise HTTPException(
                status_code=400,
                detail="Setting key is required",
            )
        super().create_validation(entity)

        """Whether the instance's provider declares ``key`` a secret."""
        if not (provider_instance_id and key):
            return False
        instance = ProviderInstanceModel.DB(base).get(
            model_registry=self.model_registry,
            id=provider_instance_id,
            return_type="dto",
            override_dto=ProviderInstanceModel,
        )
        provider = (
            ProviderModel.DB(base).get(
                model_registry=self.model_registry,
                id=instance.provider_id,
                return_type="dto",
                override_dto=ProviderModel,
            )
            if instance is not None
            else None
        )
        provider_class = (
            provider_class_named(self.model_registry, provider.name)
            if provider is not None
            else None
        )
        if provider_class is None:
            return False
        try:
            return bool(provider_class.instance_setting(key).secret)
        except KeyError:
            return False

        """``fields`` with a secret's value encrypted and the row marked
        secret: when the provider declares the key secret, or the caller
        (or an earlier write) did."""
        secret = bool(fields.get("write_only")) or self._declared_secret(
            provider_instance_id, key
        )
        if not secret:
            return fields
        from zephyrex.lib.SecretEncryption import encrypt_secret

        sealed = {**fields, "write_only": True}
        if "value" in fields:
            sealed["value"] = encrypt_secret(fields["value"])
        return sealed

        if isinstance(kwargs.get("entities"), list):
            kwargs["entities"] = [
                self._sealed(
                    {**kwargs, **entity},
                    {**kwargs, **entity}.get("provider_instance_id"),
                    {**kwargs, **entity}.get("key"),
                )
                for entity in kwargs["entities"]
            ]
            return super().create(**kwargs)
        return super().create(
            **self._sealed(
                kwargs, kwargs.get("provider_instance_id"), kwargs.get("key")
            )
        )

        existing = self.get(id=id)
        fields = {
            **kwargs,
            "write_only": kwargs.get("write_only") or existing.write_only,
        }
        return super().update(
            id,
            **self._sealed(
                fields,
                kwargs.get("provider_instance_id", existing.provider_instance_id),
                kwargs.get("key", existing.key),
            ),
        )


    provider_instance_id: str
    provider_extension_ability_id: str
    state: bool = True
    forced: bool = False

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstanceExtensionAbility represents whether an ability is enabled for that ProviderInstance. Forced abilities are always enabled downstream (Companies can force all Users and their Agents within Team Scope to use them, and Users can force their own Agents to use them). Nonpresence of a record is equivalent to state=False, forced=False."
    )
    permission_references: ClassVar[List[str]] = ["provider_instance"]

        provider_instance_id: str
        provider_extension_ability_id: str
        state: bool = True
        forced: bool = False

        provider_instance_id: Optional[str] | None = None
        provider_extension_ability_id: Optional[str] | None = None
        state: Optional[bool] | None = None
        forced: Optional[bool] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] | None = None
        provider_extension_ability_id: Optional[StringSearchModel] | None = None
        state: Optional[bool] | None = None
        forced: Optional[bool] | None = None


    _model = ProviderInstanceExtensionAbilityModel

        """The ability enabled must be one the requester can see (404)."""
        if ability_id and not ProviderExtensionAbilityModel.DB(
            self.model_registry.DB.manager.Base
        ).exists(
            model_registry=self.model_registry,
            id=ability_id,
        ):
            raise HTTPException(
                status_code=404, detail="Provider extension ability not found"
            )

        super().create_validation(entity)
        self._require_visible_ability(entity.provider_extension_ability_id)

        self._require_visible_ability(kwargs.get("provider_extension_ability_id"))
        return super().update(id, **kwargs)

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/extension/ability/provider/instance"
    tags: ClassVar[Optional[List[str]]] = ["Extension Instance Ability Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_provider_manager"


class RotationModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    ExtensionModel.Reference.Optional,
    metaclass=ModelMeta,
):
    description: Optional[str] | None = None

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A Rotation represents a list of Provider Instances. When you call a provider via a rotation, it will try each instance in order until one succeeds."
    )
    is_system_entity: ClassVar[bool] = False
    seed_creator_id: ClassVar[str] = env("ROOT_ID")

        """Create root rotations for each discovered extension."""

        try:
            logger.debug("Creating root rotations for seeding...")

            seed_data = []

            extension_registry = _get_extension_registry(model_registry)
            if extension_registry is not None:
                logger.debug(f"Using ExtensionRegistry for root rotation discovery")

                # Create a root rotation for each extension that has providers
                for ext_name in extension_registry._extension_name_map:
                    try:
                        # Check if this extension has any providers
                        if (
                            ext_name not in extension_registry.extension_providers
                            or not extension_registry.extension_providers[ext_name]
                        ):
                            logger.debug(
                                f"Extension '{ext_name}' has no providers, skipping rotation creation"
                            )
                            continue

                        rotation_name = root_rotation_name(ext_name)

                        rotation_data = {
                            "name": rotation_name,
                            "description": f"Root rotation for {ext_name} extension",
                            "_extension_name": ext_name,  # Will be resolved to extension_id
                            "user_id": None,
                            "team_id": None,
                        }

                        seed_data.append(rotation_data)
                        logger.debug(
                            f"Added root rotation '{rotation_name}' for extension '{ext_name}'"
                        )

                        logger.error(
                            f"Error creating root rotation for extension {ext_name}: {e}"
                        )
            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no root rotations to seed"
                )

            logger.debug(f"Total root rotations in seed data: {len(seed_data)}")
            return seed_data

            logger.error(f"Error creating root rotations for seeding: {e}")
            return []

        name: str
        description: Optional[str] | None = None
        user_id: Optional[str] | None = None
        team_id: Optional[str] | None = None

        @model_validator(mode="after")
        def validate_name_length(self):
            return _validate_name_min_length(self, "Rotation name")

        name: Optional[str] | None = None
        description: Optional[str] | None = None
        user_id: Optional[str] | None = None
        team_id: Optional[str] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        description: Optional[StringSearchModel] | None = None


# Item 34 — telemetry helper. Wraps individual `counter`/`histogram`/
# `gauge` calls so a misbehaving metrics backend can NEVER fail a
# rotation call. Span enter/exit calls in `RotationManager.rotate`
# follow the same swallow-and-log pattern inline.


def _safe_metric(fn, *args, **kwargs):
    """Invoke ``fn(*args, **kwargs)``; log-and-swallow any exception."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"telemetry call failed (ignored): {exc}")


    _model = RotationModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/rotation"
    tags: ClassVar[Optional[List[str]]] = ["Rotation Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"

    # Item 48 — in-process counter for `provider_silent_drop_total{provider, ability}`.
    _silent_drop_counter: ClassVar[Dict[Tuple[str, str], int]] = {}
    # Item 84 — in-process counter for `provider_cost_usd_total{tenant, provider, ability}`.
    _cost_counter: ClassVar[Dict[Tuple[str, str, str], Decimal]] = {}
    # Item 48 — pluggable outbox store; tests inject an `InMemoryOutboxStore`.
    _outbox_store: ClassVar[Optional[Any]] = None
    # Item 4 — in-process idempotency cache for retry budget. Same-process
    # retries hitting the same key short-circuit; cross-process safety is
    # the outbox row's job.
    _idempotency_cache: ClassVar[Dict[str, Any]] = {}
    _idempotency_hits: ClassVar[int] = 0
    _idempotency_misses: ClassVar[int] = 0

    @classmethod
    def get_silent_drop_counter(cls) -> Dict[Tuple[str, str], int]:
        return dict(cls._silent_drop_counter)

    @classmethod
    def get_cost_counter(cls) -> Dict[Tuple[str, str, str], Decimal]:
        return dict(cls._cost_counter)

    @classmethod
    def get_idempotency_counters(cls) -> Tuple[int, int]:
        return cls._idempotency_hits, cls._idempotency_misses

    @classmethod
    def reset_observability_counters(cls) -> None:
        cls._silent_drop_counter.clear()
        cls._cost_counter.clear()
        cls._idempotency_cache.clear()
        cls._idempotency_hits = 0
        cls._idempotency_misses = 0

        """Return ``(hit, value)``. On a hit emit the cache-hit counter;
        on a miss emit the cache-miss counter. Telemetry failures are
        swallowed via :func:`_safe_metric`."""
        from zephyrex.lib.Metrics import get_metrics_backend

        metrics = get_metrics_backend()
        if key in cls._idempotency_cache:
            cls._idempotency_hits += 1
            _safe_metric(metrics.counter, "idempotency_cache.hit")
        cls._idempotency_misses += 1
        _safe_metric(metrics.counter, "idempotency_cache.miss")
        return False, None

    @classmethod
    def _cached_idempotency_set(cls, key: str, value: Any) -> None:
        """Record a fresh idempotency-key value in the cache."""
        cls._idempotency_cache[key] = value

    @classmethod
    def set_outbox_store(cls, store: Optional[Any]) -> None:
        cls._outbox_store = store

        """Item 70 — `model_registry` first per the documented contract.

        Back-compat: if the first positional argument is a string we
        assume it's the legacy `requester_id` and route it accordingly,
        emitting a `DeprecationWarning`.
        """
        if isinstance(model_registry, str):
            warnings.warn(
                "RotationManager(requester_id, ...) is deprecated; pass "
                "model_registry first per the documented BLL.Patterns contract",
                DeprecationWarning,
                stacklevel=2,
            )
            requester_id, model_registry = model_registry, None
        super().__init__(
            model_registry=model_registry,
            target_id=target_id,
            target_team_id=target_team_id,
            parent=parent,
        )
        self._provider_instances = None
        self._rotation = None

    @property
    def rotation(self):
        """Get the rotation instance."""
        if self._rotation is None:
            self._rotation = self
        return self._rotation

        if self._provider_instances is None:
            # Import locally to avoid circular imports
            self._provider_instances = RotationProviderInstanceManager(
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._provider_instances

    def _load_rotation_policy_module(self):
        """Lazy import the rotation-policy types from `extensions.ExternalErrors`.

        Returns the module if available, else None — back-compat path
        keeps working when Item 2's file isn't on the import path yet.
        """
        try:
            from zephyrex.extensions import ExternalErrors

            return ExternalErrors
        except ImportError:
            return None

        """Look up the per-provider `RotationPolicy` from the rotation
        policy module, falling back to defaults."""
        mod = self._load_rotation_policy_module()
        if mod is None:
            return None
        # Prefer policy declared on the bonded provider class, when accessible.
        provider_cls = getattr(provider_instance, "provider_class", None)
        if provider_cls is not None:
            policy = getattr(provider_cls, "rotation_policy", None)
            if policy is not None:
                return policy
        if hasattr(mod, "default_rotation_policy"):
            return mod.default_rotation_policy()
        return None

        """Item 48 — walk attempted provider instances and pick the
        `DegradationPolicy` from the first one whose provider class
        declares one. Falls back to `default_degradation_policy()` (which
        is `FAIL_FAST`)."""
        mod = self._load_rotation_policy_module()
        for provider_instance in provider_instances:
            if provider_instance is None:
                continue
            provider_cls = getattr(provider_instance, "provider_class", None)
            if provider_cls is None or not inspect.isclass(provider_cls):
                continue
            policy = getattr(provider_cls, "degradation_policy", None)
            if policy is not None:
                return policy
        if mod is not None and hasattr(mod, "default_degradation_policy"):
            return mod.default_degradation_policy()
        return None

        """Item 84 — read the per-provider `CostModel` from the bonded
        provider class. Returns None when the provider does not declare
        one (the framework treats absence as "not metered" rather than
        emitting zero-cost noise)."""
        provider_cls = getattr(provider_instance, "provider_class", None)
        if provider_cls is None or not inspect.isclass(provider_cls):
            return None
        return getattr(provider_cls, "cost_model", None)

        """Item 84 — best-effort pre-call cost estimate. Returns ``None``
        when the cost model declines (raises) — TokenBasedCostModel-style
        post-only models cannot pre-estimate, in which case the framework
        skips the pre-check and relies on post-call true-up.
        """
        if cost_model is None:
            return None
        try:
            cost = cost_model((args, kwargs), None)
            return None
        try:
            return Decimal(str(cost))
            return None

        """Item 84 — per-tenant USD-cap pre-check. Refuse the call when
        the estimated cost would push the quota's `consumed_usd` past
        `limit_usd`. Skipped entirely when `usd_quota` is None or the row
        has no USD cap. Every provider call goes through here, the pinned
        attempt included, so a sticky pin cannot bypass the cap."""
        if usd_quota is None or getattr(usd_quota, "limit_usd", None) is None:
            return
        estimated = self._estimate_pre_call_cost(
            self._resolve_cost_model(provider_instance), args, kwargs
        )
        if estimated is None or not usd_quota.is_usd_exhausted(additional=estimated):
            return
        from zephyrex.extensions.quota.BLL_Quota import QuotaExhaustedError

        raise QuotaExhaustedError(
            scope="usd",
            ability=str(ability or "unknown"),
            period=str(getattr(usd_quota, "period", "unknown")),
        )

        """Item 84 — account a successful provider call: the per-tenant
        cost counter, the cost audit log and the USD-cap true-up. Every
        successful call goes through here, the pinned attempt included.
        Never raises: accounting failure must not fail the call."""
        cost_model = self._resolve_cost_model(provider_instance)
        if cost_model is None:
            return
        try:
            cost = cost_model((args, kwargs), result)
            cost_dec = Decimal(str(cost))
            key = (
                self._tenant_id(),
                str(getattr(provider_instance, "name", "unknown")),
                str(ability or "unknown"),
            )
            current = RotationManager._cost_counter.get(key, Decimal("0"))
            RotationManager._cost_counter[key] = current + cost_dec
            # Item 84 — emit cost into the audit log when an
            # emitter is registered. Source-of-truth for
            # finance reconstruction lives in the audit log;
            # the in-process counter is the same-process
            # roll-up optimization.
            try:
                from zephyrex.extensions.billing.BLL_CostAuditEmitter import (
                    get_cost_audit_emitter,
                )

                _emitter = get_cost_audit_emitter()
                _emitter = None
            if _emitter is not None:
                _emitter(
                    {
                        "tenant_id": key[0],
                        "provider": key[1],
                        "ability": key[2],
                        "cost_usd": cost_dec,
                    }
                )
            # Item 84 — true-up the per-tenant USD cap with
            # the actual cost. Atomic via the Quota module's
            # fallback lock so concurrent rotations agree.
            if (
                usd_quota is not None
                and getattr(usd_quota, "limit_usd", None) is not None
            ):
                from zephyrex.extensions.quota.BLL_Quota import (
                    _FALLBACK_LOCK,
                )

                with _FALLBACK_LOCK:
                    usd_quota.consumed_usd = usd_quota.consumed_usd + cost_dec
            logger.warning(
                "cost_model raised for provider %s: %s",
                getattr(provider_instance, "name", "unknown"),
                cost_exc,
            )

        """Item 34 — best-effort: read the provider's health and emit
        ``provider_health_status{provider, status}`` as a gauge. Never
        raises; telemetry must not break a rotation."""
        from zephyrex.lib.Metrics import emit_provider_health_gauge

        try:
            provider_cls = getattr(provider_instance, "provider_class", None)
            if provider_cls is None:
                return
            check = getattr(provider_cls, "cached_health_check", None) or getattr(
                provider_cls, "health_check", None
            )
            if check is None:
                return
            report = check()
            status = getattr(report, "status", report)
            name = str(getattr(provider_instance, "name", "unknown"))
            _safe_metric(emit_provider_health_gauge, name, status)
            logger.debug(f"health-gauge emit failed (ignored): {exc}")

        """Item 84 — best-effort tenant identifier for cost metric labels."""
        requester = self.optional_requester
        team_id = getattr(requester, "team_id", None)
        if team_id:
            return str(team_id)
        user_id = requester.id if requester is not None else None
        if user_id:
            return str(user_id)
        return "unknown"

    def _backoff_delay(
        self, base_ms: int, max_ms: int, jitter: float, attempt: int
    ) -> _Wait:
        """Exponential backoff with jitter; honors `max_ms` ceiling."""
        delay_ms = min(max_ms, base_ms * (2 ** max(0, attempt - 1)))
        if jitter > 0:
            factor = 1.0 + random.uniform(-jitter, jitter)
            delay_ms = max(0.0, delay_ms * factor)
        return _Wait(delay_ms / 1000.0)

        """Item 51: a pinned RPI is usable iff it is not under an active
        auth cooldown. Other health signals (DB unreachability, instance
        deletion) are surfaced when the pinned attempt actually runs."""
        cooldown_expires = _AUTH_COOLDOWNS.get(str(rpi.provider_instance_id))
        if cooldown_expires is None:
            return True
        return time.monotonic() >= cooldown_expires

        """Item 51: run the rotated callable against the pinned RPI exactly once.

        Returns ``(result, succeeded)`` so the caller can refresh the
        sticky pin TTL on success or fall through to the surviving-chain
        rotation on failure. Errors that linear rotation would surface
        immediately (`InvalidInputExternalError`, `PermanentExternalError`)
        are re-raised through the pin path as well — sticky pinning never
        masks application-level invalid input. The USD-cap pre-check and
        the cost accounting run here exactly as on the chain path, so a
        pinned call is refused at the cap (`QuotaExhaustedError`) and is
        charged when it succeeds.
        """
        try:
            with self.model_registry.DB.get_session():
                provider_instance = ProviderInstanceModel.DB(
                    self.model_registry.DB.Base
                ).get(
                    model_registry=self.model_registry,
                    return_type="dto",
                    override_dto=ProviderInstanceModel,
                    id=rpi.provider_instance_id,
                )
            return None, False
        if not provider_instance:
            return None, False
        self._check_usd_quota(
            provider_instance, args, kwargs, ability=ability, usd_quota=usd_quota
        )
        try:
            result = yield _CallProvider(provider_instance, args, kwargs)
            policy_mod = self._load_rotation_policy_module()
            if policy_mod is not None:
                InvalidInput = getattr(policy_mod, "InvalidInputExternalError", None)
                Permanent = getattr(policy_mod, "PermanentExternalError", None)
                if (
                    InvalidInput
                    and Permanent
                    and isinstance(exc, (InvalidInput, Permanent))
                ):
                    raise
            return None, False
        self._charge_call(
            provider_instance,
            args,
            kwargs,
            result,
            ability=ability,
            usd_quota=usd_quota,
        )
        return result, True

    def rotate(self, callable_func, *args, **kwargs):
        """
        Execute a callable with each provider instance in the rotation sequence.

        Per Item 2 (rotation policy hookup), when the callable raises a
        `BaseExternalError` subclass the rotation system applies the
        per-provider `RotationPolicy`:

        - `TransientExternalError` -> retry per `transient_*`, then advance
        - `RateLimitExternalError` -> backoff per `rate_limit_*`, do NOT advance
        - `AuthExternalError` -> mark unhealthy, advance immediately
        - `InvalidInputExternalError` / `PermanentExternalError` -> re-raise
        - bare `Exception` -> advance (back-compat path)

        Per Item 51 (sticky-session routing), an optional
        `routing_hint=RoutingHint(stickiness_key=...)` keyword argument
        pins repeated calls within that key to the first provider that
        succeeded for it (scoped by `(stickiness_key, ability)`), until
        the pin TTL expires or the pinned provider becomes unhealthy.
        On pinned-provider failure the pin is invalidated and the call
        falls through to the default linear rotation. On default-rotation
        success with a hint present, the winning provider becomes the new
        pin. Default behavior is unchanged when no hint is supplied;
        canary remains L7's job (Item 3).

        Args:
            callable_func: Function to call with each ProviderInstanceModel
            *args: Positional arguments to pass to the callable function
            **kwargs: Keyword arguments to pass to the callable function.
                Reserved keys (consumed by the rotation system, not the callable):
                  - `routing_hint`: Optional[RoutingHint]
                  - `ability`: Optional[str] — sticky-cache discriminator

        Returns:
            Result from the first successful provider call

        Raises:
            HTTPException: If all providers fail, includes list of attempted providers

        For a callable that returns an awaitable, use :meth:`arotate`: this
        synchronous driver would return the un-run coroutine as the
        "successful" result, so its failures would escape the policy.
        """
        return _drive_rotation(self._rotation_steps(args, kwargs), callable_func)

    async def arotate(self, callable_func, *args, **kwargs):
        """:meth:`rotate` for async callables.

        Each attempt's awaitable is awaited inside the attempt, so failures
        raised while it runs get the same retry/advance/re-raise policy as
        synchronous ones, and backoff waits yield to the event loop.
        """
        return await _adrive_rotation(self._rotation_steps(args, kwargs), callable_func)

        """The rotation itself, as steps for a driver to execute (see
        :class:`_CallProvider` / :class:`_Wait`), so the sync and async entry
        points share one implementation of the policy."""
        from fastapi import HTTPException

        # Item 51 — extract sticky-session controls before passing kwargs through.
        routing_hint: Optional[RoutingHint] = kwargs.pop("routing_hint", None)
        ability: Optional[str] = kwargs.pop("ability", None)
        # Item 84 — optional per-tenant USD-cap quota. When supplied, the
        # rotation pre-checks the estimated USD cost against the quota's
        # remaining headroom and updates `consumed_usd` post-call.
        usd_quota: Optional[Any] = kwargs.pop("usd_quota", None)
        # Only these provider instances may serve the call (an operation on
        # something one provider owns, such as its message or media id).
        only_instances: Optional[Collection[str]] = kwargs.pop(
            "provider_instance_ids", None
        )

        # Get all rotation provider instances for this rotation
        if not self.target_id:
            raise HTTPException(
                status_code=400, detail="No target rotation ID set for rotation manager"
            )

        # Get rotation provider instances ordered by hierarchy
        rotation_provider_instances = self._get_ordered_rotation_provider_instances()

        if not rotation_provider_instances:
            # Nothing configured to serve the call: unavailable, not missing.
            raise HTTPException(
                status_code=503,
                detail=f"No provider instances found for rotation {self.target_id}",
            )

        # Item 36 — apply data-residency jurisdiction filter when a tenant
        # resolver is registered. No-op when no resolver is registered, no
        # jurisdiction applies, or the chain already satisfies the policy.
        # NoInJurisdictionProviderError surfaces as a typed 400 to callers.
        try:
            from zephyrex.extensions.Residency import (
                filter_chain_by_jurisdiction,
            )

            rotation_provider_instances = filter_chain_by_jurisdiction(
                rotation_provider_instances,
                self.optional_requester,
                ability=str(ability or "unknown"),
                    self.optional_requester.id
                    if self.optional_requester is not None
                    else None
                ),
            )
        except HTTPException:
            raise
            # Residency filter must never crash rotation; missing module or
            # resolver bugs degrade to "no filtering" rather than failure.
            pass

        if only_instances is not None:
            allowed = {str(instance_id) for instance_id in only_instances}
            rotation_provider_instances = [
                r
                for r in rotation_provider_instances
                if str(r.provider_instance_id) in allowed
            ]
            if not rotation_provider_instances:
                raise HTTPException(
                    status_code=503,
                    detail="No instance of the required provider is in the rotation",
                )

        # Item 51 — if a stickiness key is present, try the pinned provider
        # first. On success → return. On failure → invalidate the pin and
        # fall through to the default linear rotation. Skip pin if the
        # pinned provider is currently unhealthy or under auth cooldown.
        sticky_key: Optional[str] = (
            routing_hint.stickiness_key if routing_hint else None
        )
        ordered_chain = list(rotation_provider_instances)
        if sticky_key:
            pinned_pi_id = _sticky_get(sticky_key, ability)
            if pinned_pi_id is not None:
                pinned_rpi = next(
                    (
                        r
                        for r in ordered_chain
                        if str(r.provider_instance_id) == str(pinned_pi_id)
                    ),
                    None,
                )
                if pinned_rpi is not None and self._sticky_pin_usable(pinned_rpi):
                    pin_result, pin_succeeded = yield from self._attempt_pinned(
                        pinned_rpi,
                        args,
                        kwargs,
                        ability=ability,
                        usd_quota=usd_quota,
                    )
                    if pin_succeeded:
                        # Refresh TTL on success.
                        _sticky_set(sticky_key, ability, pinned_pi_id)
                        return pin_result
                    # Pin failed → invalidate and fall through. Move the
                    # pinned RPI to the head so the surviving-chain
                    # rotation begins from there (per Item 51 — recompute
                    # against the surviving chain post-attempt).
                    _sticky_invalidate(sticky_key, ability)
                    ordered_chain = [
                        r
                        for r in ordered_chain
                        if str(r.provider_instance_id) != str(pinned_pi_id)
                    ]
                else:
                    # Unhealthy or vanished pin — drop and fall through.
                    _sticky_invalidate(sticky_key, ability)

        attempted_providers: List[Dict[str, Any]] = []
        attempted_provider_instances: List[Any] = []
        policy_mod = self._load_rotation_policy_module()

        # Item 34 — parent rotation span. Telemetry must NEVER fail the
        # rotation, so the span is driven through the explicit cm
        # protocol with try/except around every interaction.
        metrics = get_metrics_backend()
        rotate_span_cm = None
        try:
            rotate_span_cm = metrics.span(
                "rotation.rotate",
                target_id=str(self.target_id),
                ability=str(ability or "unknown"),
            )
            rotate_span_cm.__enter__()
            logger.debug(f"telemetry span open failed (ignored): {_exc}")
            rotate_span_cm = None

        try:
            return (
                yield from self._rotate_chain(
                    args,
                    kwargs,
                    ordered_chain=ordered_chain,
                    ability=ability,
                    sticky_key=sticky_key,
                    policy_mod=policy_mod,
                    attempted_providers=attempted_providers,
                    attempted_provider_instances=attempted_provider_instances,
                    metrics=metrics,
                    usd_quota=usd_quota,
                )
            )
        finally:
            if rotate_span_cm is not None:
                try:
                    rotate_span_cm.__exit__(None, None, None)
                    logger.debug(f"telemetry span close failed (ignored): {_exc}")

        """Item 34 — extracted body of `rotate()` so the parent
        rotation span (and the attempt-span / metrics emissions) can
        wrap the chain-walk without re-indenting the whole method.
        """
        from fastapi import HTTPException

        attempt_index = -1

        for rpi in ordered_chain:
            # Item 2: skip providers still inside their auth cooldown window.
            cooldown_expires = _AUTH_COOLDOWNS.get(str(rpi.provider_instance_id))
            if cooldown_expires is not None and time.monotonic() < cooldown_expires:
                logger.debug(
                    "Skipping provider_instance %s — auth cooldown active "
                    "(%.1fs remaining)",
                    rpi.provider_instance_id,
                    cooldown_expires - time.monotonic(),
                )
                attempted_providers.append(
                    {
                        "provider_instance_id": rpi.provider_instance_id,
                        "error": "auth cooldown active",
                    }
                )
                continue
            # Get the actual provider instance once per rpi.
            try:
                with self.model_registry.DB.get_session():
                    provider_instance = ProviderInstanceModel.DB(
                        self.model_registry.DB.Base
                    ).get(
                        model_registry=self.model_registry,
                        return_type="dto",
                        override_dto=ProviderInstanceModel,
                        id=rpi.provider_instance_id,
                    )
                logger.warning(f"Provider lookup failed: {exc}")
                attempted_providers.append(
                    {
                        "provider_instance_id": rpi.provider_instance_id,
                        "error": f"lookup failed: {exc}",
                    }
                )
                continue

            if not provider_instance:
                logger.warning(
                    f"Provider instance {rpi.provider_instance_id} not found"
                )
                attempted_providers.append(
                    {
                        "provider_instance_id": rpi.provider_instance_id,
                        "error": "Provider instance not found",
                    }
                )
                continue

            policy = self._resolve_rotation_policy(provider_instance)
            attempted_provider_instances.append(provider_instance)
            transient_attempt = 0
            rate_limit_attempt = 0
            advance_after_loop = False
            # Item 34 — emit current health gauge for this provider before
            # the attempt so dashboards reflect the per-provider state in
            # the same scrape window as the attempt latency.
            self._emit_health_for(provider_instance)
            # Item 34 — attempt-span: one span per outer-loop attempt.
            # `attempt_index` increments per outer-loop iteration so the
            # multi-provider chain shows up as N children of the parent.
            attempt_index += 1

            while True:
                # Item 34 — open a child span per inner-loop attempt and
                # measure latency. Telemetry MUST never derail the
                # rotation, so the span open and the metric emissions
                # are individually wrapped in try/except.
                attempt_span_cm = None
                try:
                    attempt_span_cm = metrics.span(
                        "rotation.attempt",
                        provider=getattr(provider_instance, "name", "unknown"),
                        attempt_index=attempt_index,
                        transient_attempt=transient_attempt,
                    )
                    attempt_span_cm.__enter__()
                    logger.debug(
                        f"telemetry attempt-span enter failed (ignored): {_exc}"
                    )
                    attempt_span_cm = None
                attempt_started_at = time.monotonic()
                try:
                    logger.debug(
                        f"Attempting to use provider instance: {provider_instance.name}"
                    )
                    self._check_usd_quota(
                        provider_instance,
                        args,
                        kwargs,
                        ability=ability,
                        usd_quota=usd_quota,
                    )
                    result = yield _CallProvider(provider_instance, args, kwargs)
                    # Success metrics — Item 34. Emission is best-effort.
                    elapsed_ms = (time.monotonic() - attempt_started_at) * 1000.0
                    _safe_metric(
                        metrics.histogram,
                        "rotation.attempt.latency_ms",
                        elapsed_ms,
                        labels={
                            "provider": str(
                                getattr(provider_instance, "name", "unknown")
                            ),
                            "ability": str(ability or "unknown"),
                        },
                    )
                    _safe_metric(
                        metrics.counter,
                        "rotation.attempt.success",
                        labels={
                            "provider": str(
                                getattr(provider_instance, "name", "unknown")
                            ),
                            "ability": str(ability or "unknown"),
                        },
                    )
                    _safe_metric(
                        metrics.counter,
                        "rotation.success_total",
                        labels={"ability": str(ability or "unknown")},
                    )
                    _safe_metric(
                        metrics.histogram,
                        "rotation.attempts_until_success",
                        float(attempt_index),
                        labels={"ability": str(ability or "unknown")},
                    )
                    logger.debug(
                        f"Successfully used provider instance: {provider_instance.name}"
                    )
                    self._charge_call(
                        provider_instance,
                        args,
                        kwargs,
                        result,
                        ability=ability,
                        usd_quota=usd_quota,
                    )
                    # Item 51 — on a successful default-rotation attempt with a
                    # stickiness key, the winning provider becomes the new pin.
                    if sticky_key:
                        _sticky_set(sticky_key, ability, str(rpi.provider_instance_id))
                    return result
                    # Item 84 — USD-cap pre-check refusal must surface
                    # immediately (operator-visible quota error), not get
                    # absorbed by the back-compat advance path.
                    from zephyrex.extensions.quota.BLL_Quota import (
                        QuotaExhaustedError,
                    )

                    if isinstance(exc, QuotaExhaustedError):
                        raise
                    # Item 34 — emit per-attempt failure counter. Skip
                    # rate-limit (we stay on the same provider, the
                    # attempt continues on the next iteration) but emit
                    # for auth/transient-exhausted/bare so dashboards
                    # see real failures rather than back-pressure.
                    _failure_already_counted = False
                    # Item 2 dispatch — only when the typed error module is loaded.
                    if policy_mod is not None and isinstance(
                        exc, getattr(policy_mod, "BaseExternalError", ())
                    ):
                        InvalidInput = getattr(policy_mod, "InvalidInputExternalError")
                        Permanent = getattr(policy_mod, "PermanentExternalError")
                        Transient = getattr(policy_mod, "TransientExternalError")
                        RateLimit = getattr(policy_mod, "RateLimitExternalError")
                        Auth = getattr(policy_mod, "AuthExternalError")

                        if isinstance(exc, (InvalidInput, Permanent)):
                            # Surface immediately; do not rotate.
                            raise

                        if isinstance(exc, RateLimit):
                            rate_limit_attempt += 1
                            base = (
                                getattr(policy, "rate_limit_base_ms", 1000)
                                if policy
                                else 1000
                            )
                            mx = (
                                getattr(policy, "rate_limit_max_ms", 60000)
                                if policy
                                else 60000
                            )
                            wait = getattr(exc, "retry_after_seconds", None)
                            if wait is not None:
                                yield _Wait(float(wait))
                            else:
                                yield self._backoff_delay(
                                    base, mx, 0.1, rate_limit_attempt
                                )
                            # Stay on the same provider; do not advance.
                            continue

                        if isinstance(exc, Auth):
                            # Mark unhealthy via cooldown; advance.
                            cooldown = (
                                getattr(policy, "auth_cooldown_seconds", 300)
                                if policy
                                else 300
                            )
                            _AUTH_COOLDOWNS[str(rpi.provider_instance_id)] = (
                                time.monotonic() + float(cooldown)
                            )
                            logger.warning(
                                "Auth failure on %s; advancing (cooldown=%ss)",
                                provider_instance.name,
                                cooldown,
                            )
                            attempted_providers.append(
                                {
                                    "provider_instance_id": rpi.provider_instance_id,
                                    "provider_name": provider_instance.name,
                                    "error": f"auth: {exc}",
                                }
                            )
                            _safe_metric(
                                metrics.counter,
                                "rotation.attempt.failure",
                                labels={
                                    "provider": str(
                                        getattr(provider_instance, "name", "unknown")
                                    ),
                                    "ability": str(ability or "unknown"),
                                    "error_class": type(exc).__name__,
                                },
                            )
                            _failure_already_counted = True
                            advance_after_loop = True
                            break

                        if isinstance(exc, Transient):
                            transient_attempt += 1
                            mr = (
                                getattr(policy, "transient_max_retries", 3)
                                if policy
                                else 3
                            )
                            if transient_attempt <= mr:
                                base = (
                                    getattr(policy, "transient_base_ms", 100)
                                    if policy
                                    else 100
                                )
                                mx = (
                                    getattr(policy, "transient_max_ms", 5000)
                                    if policy
                                    else 5000
                                )
                                jit = (
                                    getattr(policy, "transient_jitter", 0.1)
                                    if policy
                                    else 0.1
                                )
                                yield self._backoff_delay(
                                    base, mx, jit, transient_attempt
                                )
                                continue
                            attempted_providers.append(
                                {
                                    "provider_instance_id": rpi.provider_instance_id,
                                    "provider_name": provider_instance.name,
                                    "error": f"transient (exhausted): {exc}",
                                }
                            )
                            _safe_metric(
                                metrics.counter,
                                "rotation.attempt.failure",
                                labels={
                                    "provider": str(
                                        getattr(provider_instance, "name", "unknown")
                                    ),
                                    "ability": str(ability or "unknown"),
                                    "error_class": type(exc).__name__,
                                },
                            )
                            _failure_already_counted = True
                            advance_after_loop = True
                            break

                    # Bare-exception or unknown typed error -> back-compat advance.
                    logger.warning(
                        f"Provider instance {provider_instance.name} failed: {exc}"
                    )
                    attempted_providers.append(
                        {
                            "provider_instance_id": rpi.provider_instance_id,
                            "provider_name": provider_instance.name,
                            "error": str(exc),
                        }
                    )
                    if not _failure_already_counted:
                        _safe_metric(
                            metrics.counter,
                            "rotation.attempt.failure",
                            labels={
                                "provider": str(
                                    getattr(provider_instance, "name", "unknown")
                                ),
                                "ability": str(ability or "unknown"),
                                "error_class": type(exc).__name__,
                            },
                        )
                    advance_after_loop = True
                    break
                finally:
                    # Item 34 — close the attempt span on every exit
                    # path (success-return, retry-continue, advance-break,
                    # surface-raise). Telemetry must NEVER fail rotation,
                    # so the close is wrapped.
                    if attempt_span_cm is not None:
                        try:
                            attempt_span_cm.__exit__(None, None, None)
                            logger.debug(
                                f"telemetry attempt-span close failed (ignored): {_exc}"
                            )

            if advance_after_loop:
                continue

        # If we get here, all providers failed
        error_detail = {
            "message": f"All provider instances failed for rotation {self.target_id}",
            "attempted_providers": attempted_providers,
            "total_attempts": len(attempted_providers),
        }

        # Item 34 — chain-exhaustion counter. Always emit on full
        # exhaustion regardless of degradation policy so on-call sees
        # the rotation give up before the policy decides what to do.
        _safe_metric(
            metrics.counter,
            "rotation.exhausted_total",
            labels={"ability": str(ability or "unknown")},
        )

        # Item 48 — graceful degradation dispatch on chain exhaustion.
        degradation = self._resolve_degradation_policy(attempted_provider_instances)
        mode = getattr(degradation, "mode", None) if degradation else None
        mode_value = getattr(mode, "value", mode)

        if mode_value == "queue_and_retry":
            store = RotationManager._outbox_store
            if store is None:
                raise HTTPException(status_code=500, detail=error_detail)
            from zephyrex.logic.Outbox import OutboxEntry

            first_pi = next(
                (pi for pi in attempted_provider_instances if pi is not None),
                None,
            )
            target_provider = (
                str(getattr(first_pi, "name", "unknown")) if first_pi else "unknown"
            )
            entry = OutboxEntry(
                operation_type="rotation_call",
                target_provider=target_provider,
                target_ability=str(ability or "unknown"),
                payload={
                    "rotation_id": str(self.target_id),
                    "attempted_providers": [
                        str(p.get("provider_instance_id")) for p in attempted_providers
                    ],
                },
                idempotency_key=f"rotation:{self.target_id}:{uuid.uuid4()}",
            )
            tracking_id = store.enqueue(entry)
            from zephyrex.extensions.ExternalErrors import QueuedForRetry

            return QueuedForRetry(tracking_id=tracking_id)

        if mode_value == "silent_drop":
            first_pi = next(
                (pi for pi in attempted_provider_instances if pi is not None),
                None,
            )
            provider_name = (
                str(getattr(first_pi, "name", "unknown")) if first_pi else "unknown"
            )
            ability_name = str(ability or "unknown")
            drop_key = (provider_name, ability_name)
            RotationManager._silent_drop_counter[drop_key] = (
                RotationManager._silent_drop_counter.get(drop_key, 0) + 1
            )
            logger.warning(
                "provider_silent_drop provider=%s ability=%s rotation=%s "
                "attempted=%d",
                provider_name,
                ability_name,
                self.target_id,
                len(attempted_providers),
            )
            from zephyrex.extensions.ExternalErrors import SilentDropped

            return SilentDropped(provider=provider_name, ability=ability_name)

        raise HTTPException(status_code=500, detail=error_detail)

        """
        Get rotation provider instances ordered by parent hierarchy.

        Returns instances in order: parent_id=None first, then their children, etc.
        """
        # Get all rotation provider instances for this rotation
        with self.model_registry.DB.get_session():
            all_instances = RotationProviderInstanceModel.DB(
                self.model_registry.DB.Base
            ).list(
                model_registry=self.model_registry,
                # db=session,
                # db_manager=self.model_registry,
                return_type="dto",
                override_dto=RotationProviderInstanceModel,
                rotation_id=self.target_id,
            )

        if not all_instances:
            return []

        # Build ordered list by hierarchy
        ordered_instances = []
        remaining_instances = all_instances.copy()

        # Start with instances that have no parent (parent_id=None)
        current_level = [inst for inst in remaining_instances if inst.parent_id is None]

        while current_level:
            # Add current level to ordered list
            ordered_instances.extend(current_level)

            # Remove current level from remaining
            for inst in current_level:
                remaining_instances.remove(inst)

            # Find children of current level
            current_level_ids = [inst.id for inst in current_level]
            next_level = [
                inst
                for inst in remaining_instances
                if inst.parent_id in current_level_ids
            ]

            current_level = next_level

        # Add any remaining instances that might have broken hierarchy
        if remaining_instances:
            logger.warning(
                f"Found rotation provider instances with broken hierarchy: {[inst.id for inst in remaining_instances]}"
            )
            ordered_instances.extend(remaining_instances)

        return ordered_instances


class RotationProviderInstanceModel(
    ApplicationModel,
    UpdateMixinModel,
    ParentMixinModel,
    RotationModel.Reference,
    ProviderInstanceModel.Reference,
    metaclass=ModelMeta,
):
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A RotationProviderInstance represents a link between a Rotation and a ProviderInstance. Order is determined by record parentage (NULL parent is first)."
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["rotation"]

        """Create links between root rotations and provider instances."""

        try:
            logger.debug(
                "Creating rotation/provider-instance instance links for seeding..."
            )

            seed_data = []

            extension_registry = _get_extension_registry(model_registry)
            if extension_registry is not None:
                logger.debug(
                    f"Using ExtensionRegistry for rotation/provider-instance link discovery"
                )

                # Process all providers from all extensions
                for (
                    ext_name,
                    providers,
                ) in extension_registry.extension_providers.items():
                    for prv_class in providers:
                        try:
                            provider_name = _resolve_provider_name(
                                prv_class, warn_missing=True
                            )

                            # Check if provider has required environment variables set
                            required_env_vars = getattr(prv_class, "_env", {})
                            has_required_vars = False

                            for (
                                env_var_name,
                                default_value,
                            ) in required_env_vars.items():
                                current_value = env(env_var_name)
                                if current_value and current_value.strip():
                                    has_required_vars = True
                                    break

                            if not has_required_vars and not getattr(
                                prv_class, "usable_with_defaults", False
                            ):
                                # No instance created for this provider, skip linking
                                logger.debug(
                                    f"Provider '{provider_name}' has no environment variables set, skipping rotation links"
                                )
                                continue

                            # Must match the rotation seeder's name exactly, or
                            # the link never resolves for multi-word extensions.
                            rotation_name = root_rotation_name(ext_name)

                            instance_name = root_instance_name(provider_name)

                            link_data = {
                                "_rotation_name": rotation_name,  # Will be resolved to rotation_id
                                "_provider_instance_name": instance_name,  # Instance name
                                "parent_id": None,  # Root level in hierarchy
                            }

                            seed_data.append(link_data)
                            logger.debug(
                                f"Added rotation/provider-instance instance link for rotation '{rotation_name}' and instance '{instance_name}'"
                            )

                            logger.error(
                                f"Error creating rotation/provider-instance instance link for {prv_class.__name__}: {e}"
                            )
            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no rotation/provider-instance links to seed"
                )

            logger.debug(
                f"Total rotation/provider-instance instance links in seed data: {len(seed_data)}"
            )
            return seed_data

            logger.error(
                f"Error creating rotation/provider-instance instance links for seeding: {e}"
            )
            return []

        rotation_id: str
        provider_instance_id: str
        parent_id: Optional[str] | None = None

        rotation_id: Optional[str] | None = None
        provider_instance_id: Optional[str] | None = None
        parent_id: Optional[str] | None = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ParentMixinModel.Search,
    ):
        rotation_id: Optional[StringSearchModel] | None = None
        provider_instance_id: Optional[StringSearchModel] | None = None


    _model = RotationProviderInstanceModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/rotation/provider/instance"
    tags: ClassVar[Optional[List[str]]] = ["Rotation Provider Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_rotation_provider_instance_manager"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ) -> None:
        """
        Initialize RotationProviderInstanceManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            db: Database session (optional)
            db_manager: Database manager instance (required)
            model_registry: Model registry for dynamic model handling (optional)
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._rotation = None

        """Get the rotation manager instance."""
        if self._rotation is None:
            # Import locally to avoid circular imports
            self._rotation = RotationManager(  # type: ignore[assignment]
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._rotation  # type: ignore[return-value]

        """The rotation and provider instance a link names must be ones the
        requester can see, or they are not found (404). Checked as ROOT, any
        user could put any instance, theirs or not, into their rotation.
        ROOT and SYSTEM see every record, so nothing changes for them."""
        base = self.model_registry.DB.manager.Base
        references = (
            (RotationModel, rotation_id, "Rotation not found"),
            (
                ProviderInstanceModel,
                provider_instance_id,
                "Provider instance not found",
            ),
        )
        for model, reference_id, not_found in references:
            if reference_id and not model.DB(base).exists(
                model_registry=self.model_registry,
                id=reference_id,
            ):
                raise HTTPException(status_code=404, detail=not_found)

        self._require_visible_references(
            kwargs.get("rotation_id"), kwargs.get("provider_instance_id")
        )
        return super().update(id, **kwargs)

        """Validate rotation provider instance creation: the rotation and
        provider instance must be visible to the requester."""
        self._require_visible_references(
            entity.rotation_id, entity.provider_instance_id
        )

        # Prevent circular parent relationships
        if entity.parent_id == entity.rotation_id:
            raise HTTPException(
                status_code=400,
                detail="A rotation provider instance cannot be its own parent",
            )


ProviderModel.Manager = ProviderManager
ProviderExtensionModel.Manager = ProviderExtensionManager
ProviderExtensionAbilityModel.Manager = ProviderExtensionAbilityManager
ProviderInstanceModel.Manager = ProviderInstanceManager
ProviderInstanceUsageModel.Manager = ProviderInstanceUsageManager
ProviderInstanceSettingModel.Manager = ProviderInstanceSettingManager
ProviderInstanceExtensionAbilityModel.Manager = ProviderInstanceExtensionAbilityManager
RotationModel.Manager = RotationManager
RotationProviderInstanceModel.Manager = RotationProviderInstanceManager
