from __future__ import annotations

import inspect
import random
import time
import uuid
import warnings
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple

import stringcase
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from database.DatabaseManager import DatabaseManager
from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import BaseModel
from lib.Pydantic2FastAPI import AuthType, RouterMixin
from logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from logic.BLL_Auth import TeamModel, UserModel
from logic.BLL_Extensions import AbilityModel, ExtensionModel


class ManagerContractError(TypeError):
    """Raised by `validate_manager_constructors` when a manager violates the
    documented `model_registry`-first contract (Item 70)."""


# Item 2 — per-process auth cooldown tracker. Module-level so the
# multi-instance state survives RotationManager construction across
# requests. Keyed by `provider_instance_id`; value is the monotonic time
# at which the cooldown expires. Multi-PROCESS deployments accept
# independent per-process state until Item 69's DistributedCounter
# is wired through.
_AUTH_COOLDOWNS: Dict[str, float] = {}


def reset_auth_cooldowns() -> None:
    """Test helper: clear the in-process auth cooldown tracker."""
    _AUTH_COOLDOWNS.clear()


# Item 51 — sticky-session routing primitives.
@dataclass(frozen=True)
class RoutingHint:
    """Per-call routing hint passed to `RotationManager.rotate`.

    Item 51: when `stickiness_key` is non-None, the rotation system pins
    repeated calls within that key to the first provider that succeeded
    for it (scoped by `(stickiness_key, ability)`), until the pin expires
    or the pinned provider becomes unhealthy. Default behavior (linear
    failover) is unchanged when no hint is supplied. Canary routing is
    intentionally NOT a field here — per Item 3 it remains L7's job.
    """

    stickiness_key: Optional[str] = None


# Item 51 — in-memory sticky-session cache. Keyed by
# `(stickiness_key, ability)`; value is `(provider_instance_id, expires_at)`
# where `expires_at` is a monotonic-time deadline. Multi-PROCESS
# deployments accept session-affinity loss across processes (acceptable
# for many use cases) or back this with Redis (out of scope here).
_STICKY_SESSIONS: Dict[Tuple[str, Optional[str]], Tuple[str, float]] = {}


def _sticky_ttl_seconds() -> float:
    """Configurable TTL via env, default 600s."""
    raw = env("STICKY_SESSION_TTL_SECONDS")
    if not raw:
        return 600.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 600.0


def reset_sticky_sessions() -> None:
    """Test helper: clear the in-process sticky-session cache."""
    _STICKY_SESSIONS.clear()


def _sticky_get(
    stickiness_key: str, ability: Optional[str]
) -> Optional[str]:
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


def _sticky_invalidate(stickiness_key: str, ability: Optional[str]) -> None:
    """Drop the pin for `(stickiness_key, ability)` (e.g. on health failure
    or pinned-provider error)."""
    _STICKY_SESSIONS.pop((stickiness_key, ability), None)


def validate_manager_constructors(*managers: type) -> None:
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
        sig = inspect.signature(cls.__init__)
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
    friendly_name: Optional[str] = None
    agent_settings_json: Optional[str] = None

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A Provider represents an external provider of data or functionality. It represents an extension provider. Settings should exclude model name and api key, as those are stored in provider instance as fields."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")
    seed_list: ClassVar[List[Dict[str, Any]]] = []

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Dynamically discover and return provider seed data."""
        try:
            logger.debug("Discovering providers for seeding...")

            seed_data = []

            # Get providers from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                extension_registry = model_registry.extension_registry
                logger.debug(f"Using ExtensionRegistry for provider discovery")

                # Iterate through registered extensions and their providers
                for (
                    ext_name,
                    providers,
                ) in extension_registry.extension_providers.items():
                    logger.debug(
                        f"Processing {len(providers)} providers for extension: {ext_name}"
                    )

                    for provider_class in providers:
                        # Get provider name
                        if hasattr(provider_class, "name") and provider_class.name:
                            provider_name = provider_class.name
                        else:
                            provider_name = provider_class.__name__.replace(
                                "Provider", ""
                            ).replace("PRV_", "")
                            logger.warning(
                                f"Provider class {provider_class.__name__} missing 'name' attribute, using {provider_name}"
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

            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no providers to seed"
                )

            logger.debug(f"Total providers in seed data: {len(seed_data)}")
            return seed_data

        except Exception as e:
            logger.error(f"Error discovering providers for seeding: {e}")
            return []

    class Create(BaseModel):
        name: str
        friendly_name: Optional[str] = None
        agent_settings_json: Optional[str] = None
        system: bool = False

        @model_validator(mode="after")
        def validate_name_length(self):
            if self.name and len(self.name) < 2:
                raise ValueError("Provider name must be at least 2 characters long")
            return self

    class Update(BaseModel):
        name: Optional[str] = None
        friendly_name: Optional[str] = None
        agent_settings_json: Optional[str] = None
        system: Optional[bool] = None

    class Search(
        ApplicationModel.Search, NameMixinModel.Search, UpdateMixinModel.Search
    ):
        friendly_name: Optional[StringSearchModel] = None
        agent_settings_json: Optional[StringSearchModel] = None
        system: Optional[bool] = None


class ProviderManager(AbstractBLLManager, RouterMixin):
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
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
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

    @property
    def extensions(self) -> "ProviderExtensionManager":
        """Get the provider extension manager."""
        if self._extensions is None:
            # Import locally to avoid circular imports
            self._extensions = ProviderExtensionManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._extensions

    @property
    def instances(self) -> "ProviderInstanceManager":
        """Get the provider instance manager."""
        if self._instances is None:
            # Import locally to avoid circular imports
            self._instances = ProviderInstanceManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._instances

    @property
    def rotations(self) -> "RotationManager":
        """Get the rotation manager."""
        if self._rotations is None:
            # Import locally to avoid circular imports
            self._rotations = RotationManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._rotations

    @staticmethod
    def list_runtime_providers():
        return ["OpenAI", "AGInYourPC"]

    @staticmethod
    def get_runtime_provider_options(provider_name):
        if provider_name == "OpenAI":
            return {"OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-4"}
        elif provider_name == "AGInYourPC":
            return {"AGINYOURPC_API_KEY": "", "AGINYOURPC_AI_MODEL": "local"}
        return {}

    @classmethod
    def static_list(cls, db_manager: DatabaseManager, **kwargs):
        """
        Static method to list rotations without requiring a manager instance.
        Used for extension root rotation lookup.

        Args:
            db_manager: DatabaseManager instance for database operations
        """
        session = db_manager.get_session()

        try:
            return cls.Model.DB(db_manager.Base).list(
                requester_id=kwargs.get("created_by_user_id"),
                db=session,
                return_type="dto",
                override_dto=cls.Model,
                **{k: v for k, v in kwargs.items() if k != "created_by_user_id"},
            )
        finally:
            session.close()


class ProviderExtensionModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    provider_id: str
    extension_id: str

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderExtension represents Provider support for an Extension."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Dynamically create provider-extension links based on discovered providers and extensions."""
        try:
            logger.debug("Creating provider-extension links for seeding...")

            seed_data = []

            # Get providers and extensions from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                extension_registry = model_registry.extension_registry
                logger.debug(
                    f"Using ExtensionRegistry for provider-extension link discovery"
                )

                # Create provider-extension links
                for (
                    ext_name,
                    providers,
                ) in extension_registry.extension_providers.items():
                    for provider_class in providers:
                        # Get provider name
                        if hasattr(provider_class, "name") and provider_class.name:
                            provider_name = provider_class.name
                        else:
                            provider_name = provider_class.__name__.replace(
                                "Provider", ""
                            ).replace("PRV_", "")

                        link_data = {
                            "_provider_name": provider_name,  # Will be resolved to provider_id
                            "_extension_name": ext_name,  # Will be resolved to extension_id
                        }
                        seed_data.append(link_data)
                        logger.debug(
                            f"Added ProviderExtension link for provider '{provider_name}' and extension '{ext_name}'"
                        )
            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no provider-extension links to seed"
                )

            logger.debug(
                f"Total provider-extension links in seed data: {len(seed_data)}"
            )
            return seed_data

        except Exception as e:
            logger.error(f"Error creating provider-extension links: {e}")
            return []

    class Create(BaseModel):
        provider_id: str
        extension_id: str

    class Update(BaseModel):
        provider_id: Optional[str] = None
        extension_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
    ):
        provider_id: Optional[StringSearchModel] = None
        extension_id: Optional[StringSearchModel] = None


class ProviderExtensionManager(AbstractBLLManager, RouterMixin):
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
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
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

    @property
    def ability(self) -> "ProviderExtensionAbilityManager":
        """Get the provider extension ability manager."""
        if self._ability is None:
            # Import locally to avoid circular imports
            self._ability = ProviderExtensionAbilityManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._ability

    def create_validation(self, entity):
        """Validate provider extension creation - check that provider and extension exist."""
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        if entity.provider_id:
            provider = ProviderModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.provider_id,
            )
            if not provider:
                raise HTTPException(status_code=404, detail="Provider not found")

        if entity.extension_id:
            extension = ExtensionModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.extension_id,
            )
            if not extension:
                raise HTTPException(status_code=404, detail="Extension not found")


class ProviderExtensionAbilityModel(
    ApplicationModel, UpdateMixinModel, metaclass=ModelMeta
):
    provider_extension_id: str
    ability_id: str

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderExtensionAbility represents a ProviderExtension and Ability combination. This allows for a provider to provide partial functionality to an extension, for example SendGrid only provides sending email, but not an inbox."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

    class Create(BaseModel):
        provider_extension_id: str
        ability_id: str

    class Update(BaseModel):
        provider_extension_id: Optional[str] = None
        ability_id: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        provider_extension_id: Optional[StringSearchModel] = None
        ability_id: Optional[StringSearchModel] = None


class ProviderExtensionAbilityManager(AbstractBLLManager, RouterMixin):
    _model = ProviderExtensionAbilityModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/extension/ability/provider"
    tags: ClassVar[Optional[List[str]]] = ["Provider Extension Ability Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "api_key_auth"

    def create_validation(self, entity):
        """Validate provider extension ability creation - check that provider extension and ability exist."""
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        if entity.provider_extension_id:
            provider_extension = ProviderExtensionModel.DB(
                self.model_registry.DB.manager.Base
            ).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.provider_extension_id,
            )
            if not provider_extension:
                raise HTTPException(
                    status_code=404, detail="Provider extension not found"
                )

        if entity.ability_id:
            ability = AbilityModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
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
    model_name: Optional[str] = None
    api_key: Optional[str] = None
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

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstance represents a User or Team's instance of a Provider. They can have multiple of the same Provider."
    )
    is_system_entity: ClassVar[bool] = False
    seed_creator_id: ClassVar[str] = env("ROOT_ID")

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Return empty seed data - provider instances should be created by extension hooks."""
        # Provider instances are created by extension-specific hooks
        # Each extension knows best which environment variables to check
        # and how to configure their provider instances
        # logger.debug(
        #     "ProviderInstance seed_data called - delegating to extension hooks"
        # )
        # return []

        try:
            logger.debug("Discovering provider instances for seeding...")

            seed_data = []

            # Get providers from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                extension_registry = model_registry.extension_registry
                logger.debug(f"Using ExtensionRegistry for provider discovery")

                # Iterate through registered extensions and their providers
                for (
                    ext_name,
                    providers,
                ) in extension_registry.extension_providers.items():
                    logger.debug(
                        f"Processing provider instances for {len(providers)} providers for extension: {ext_name}"
                    )

                    for provider_class in providers:
                        # Get provider name
                        if hasattr(provider_class, "name") and provider_class.name:
                            provider_name = provider_class.name
                        else:
                            provider_name = provider_class.__name__.replace(
                                "Provider", ""
                            ).replace("PRV_", "")
                            logger.warning(
                                f"Provider class {provider_class.__name__} missing 'name' attribute, using {provider_name}"
                            )

                        # Create provider instance name using PascalCase of provider name
                        provider_instance_name = (
                            f"Root_{stringcase.pascalcase(provider_name)}"
                        )

                        provider_data = {
                            "_provider_name": provider_name,  # Will be resolved to provider_id
                            "name": provider_instance_name,
                            "model_name": provider_name,
                            "api_key": None,  # Will be set by extension hooks
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

            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no providers to seed"
                )

            logger.debug(f"Total provider instances in seed data: {len(seed_data)}")
            return seed_data

        except Exception as e:
            logger.error(f"Error discovering provider instances for seeding: {e}")
            return []

    class Create(BaseModel):
        name: str
        provider_id: str
        model_name: Optional[str] = None
        api_key: Optional[str] = None
        user_id: Optional[str] = None
        team_id: Optional[str] = None
        scope: Literal["root", "system", "team", "user"] = "user"

        @model_validator(mode="after")
        def validate_name_length(self):
            if self.name and len(self.name) < 2:
                raise ValueError(
                    "Provider instance name must be at least 2 characters long"
                )
            return self

    class Update(BaseModel):
        name: Optional[str] = None
        provider_id: Optional[str] = None
        model_name: Optional[str] = None
        api_key: Optional[str] = None
        user_id: Optional[str] = None
        team_id: Optional[str] = None
        scope: Optional[Literal["root", "system", "team", "user"]] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        ProviderModel.Reference.ID.Search,
    ):
        model_name: Optional[StringSearchModel] = None
        api_key: Optional[StringSearchModel] = None
        scope: Optional[StringSearchModel] = None


class ProviderInstanceManager(AbstractBLLManager, RouterMixin):
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
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
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

    @property
    def usage(self) -> "ProviderInstanceUsageManager":
        """Get the provider instance usage manager."""
        if self._usage is None:
            # Import locally to avoid circular imports
            self._usage = ProviderInstanceUsageManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._usage

    @property
    def setting(self) -> "ProviderInstanceSettingManager":
        """Get the provider instance setting manager."""
        if self._setting is None:
            # Import locally to avoid circular imports
            self._setting = ProviderInstanceSettingManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._setting

    @property
    def ability(self) -> "ProviderInstanceExtensionAbilityManager":
        """Get the provider instance extension ability manager."""
        if self._ability is None:
            # Import locally to avoid circular imports
            self._ability = ProviderInstanceExtensionAbilityManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._ability

    def create_validation(self, entity):
        """Validate provider instance creation - check that provider exists."""
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        if entity.provider_id:
            provider = ProviderModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
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

    class Create(BaseModel):
        provider_instance_id: str
        key: Optional[str] = None
        value: Optional[int] = None
        user_id: Optional[str] = None
        team_id: Optional[str] = None

    class Update(BaseModel):
        provider_instance_id: Optional[str] = None
        key: Optional[str] = None
        value: Optional[int] = None
        user_id: Optional[str] = None
        team_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        key: Optional[StringSearchModel] = None
        value: Optional[NumericalSearchModel] = None


class ProviderInstanceUsageManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceUsageModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/provider/instance/usage"
    tags: ClassVar[Optional[List[str]]] = ["Provider Instance Usage Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_provider_manager"


class ProviderInstanceSettingModel(
    ApplicationModel, UpdateMixinModel, metaclass=ModelMeta
):
    provider_instance_id: str
    key: str
    value: Optional[str] = None

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstanceSetting represents a non-default configuration setting for a User or Team's instance of an Provider."
    )
    is_system_entity: ClassVar[bool] = False

    class Create(BaseModel):
        provider_instance_id: str
        key: str
        value: Optional[str] = None

    class Update(BaseModel):
        provider_instance_id: Optional[str] = None
        key: Optional[str] = None
        value: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        key: Optional[StringSearchModel] = None
        value: Optional[StringSearchModel] = None


class ProviderInstanceSettingManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceSettingModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/provider/instance/setting"
    tags: ClassVar[Optional[List[str]]] = ["Provider Instance Settings"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"

    def create_validation(self, entity):
        """Validate provider instance setting creation"""
        if not entity.key:
            raise HTTPException(
                status_code=400,
                detail="Setting key is required",
            )
        # Check that provider instance exists (use ROOT_ID to bypass permission filtering)
        if entity.provider_instance_id:
            provider_instance = ProviderInstanceModel.DB(
                self.model_registry.DB.manager.Base
            ).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.provider_instance_id,
            )
            if not provider_instance:
                raise HTTPException(
                    status_code=404, detail="Provider instance not found"
                )


class ProviderInstanceExtensionAbilityModel(
    ApplicationModel, UpdateMixinModel, metaclass=ModelMeta
):
    provider_instance_id: str
    provider_extension_ability_id: str
    state: bool = True
    forced: bool = False

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A ProviderInstanceExtensionAbility represents whether an ability is enabled for that ProviderInstance. Forced abilities are always enabled downstream (Companies can force all Users and their Agents within Team Scope to use them, and Users can force their own Agents to use them). Nonpresence of a record is equivalent to state=False, forced=False."
    )

    class Create(BaseModel):
        provider_instance_id: str
        provider_extension_ability_id: str
        state: bool = True
        forced: bool = False

    class Update(BaseModel):
        provider_instance_id: Optional[str] = None
        provider_extension_ability_id: Optional[str] = None
        state: Optional[bool] = None
        forced: Optional[bool] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        provider_extension_ability_id: Optional[StringSearchModel] = None
        state: Optional[bool] = None
        forced: Optional[bool] = None


class ProviderInstanceExtensionAbilityManager(AbstractBLLManager, RouterMixin):
    _model = ProviderInstanceExtensionAbilityModel

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
    description: Optional[str] = None

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A Rotation represents a list of Provider Instances. When you call a provider via a rotation, it will try each instance in order until one succeeds."
    )
    is_system_entity: ClassVar[bool] = False
    seed_creator_id: ClassVar[str] = env("ROOT_ID")

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Create root rotations for each discovered extension."""
        from lib.Environment import inflection

        try:
            logger.debug("Creating root rotations for seeding...")

            seed_data = []

            # Get extensions from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                extension_registry = model_registry.extension_registry
                logger.debug(f"Using ExtensionRegistry for root rotation discovery")

                # Create a root rotation for each extension that has providers
                for (
                    ext_name,
                    ext_class,
                ) in extension_registry._extension_name_map.items():
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

                        # Get the extension's friendly name if available
                        if (
                            hasattr(ext_class, "friendly_name")
                            and ext_class.friendly_name
                        ):
                            friendly_name = ext_class.friendly_name
                        else:
                            # Generate friendly name from extension name
                            friendly_name = stringcase.titlecase(ext_name)

                        # Create rotation name preserving underscores and proper capitalization
                        # Convert extension name parts to PascalCase individually
                        parts = ext_name.split("_")
                        capitalized_parts = [
                            stringcase.pascalcase(part) for part in parts
                        ]
                        rotation_name = f"Root_{'_'.join(capitalized_parts)}"

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

                    except Exception as e:
                        logger.error(
                            f"Error creating root rotation for extension {ext_name}: {e}"
                        )
            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no root rotations to seed"
                )

            logger.debug(f"Total root rotations in seed data: {len(seed_data)}")
            return seed_data

        except Exception as e:
            logger.error(f"Error creating root rotations for seeding: {e}")
            return []

    class Create(BaseModel):
        name: str
        description: Optional[str] = None
        user_id: Optional[str] = None
        team_id: Optional[str] = None

        @model_validator(mode="after")
        def validate_name_length(self):
            if self.name and len(self.name) < 2:
                raise ValueError("Rotation name must be at least 2 characters long")
            return self

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        user_id: Optional[str] = None
        team_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        description: Optional[StringSearchModel] = None


class RotationManager(AbstractBLLManager, RouterMixin):
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

    @classmethod
    def get_silent_drop_counter(cls) -> Dict[Tuple[str, str], int]:
        return dict(cls._silent_drop_counter)

    @classmethod
    def get_cost_counter(cls) -> Dict[Tuple[str, str, str], Decimal]:
        return dict(cls._cost_counter)

    @classmethod
    def reset_observability_counters(cls) -> None:
        cls._silent_drop_counter.clear()
        cls._cost_counter.clear()

    @classmethod
    def set_outbox_store(cls, store: Optional[Any]) -> None:
        cls._outbox_store = store

    def __init__(
        self,
        model_registry: Optional[Any] = None,
        requester_id: Optional[str] = None,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        parent: Optional[Any] = None,
    ) -> None:
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
            requester_id=requester_id,
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

    @property
    def provider_instances(self):
        if self._provider_instances is None:
            # Import locally to avoid circular imports
            self._provider_instances = RotationProviderInstanceManager(
                requester_id=self.requester.id,
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
            from extensions import ExternalErrors  # type: ignore
            return ExternalErrors
        except ImportError:
            return None

    def _resolve_rotation_policy(self, provider_instance):
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

    def _resolve_degradation_policy(self, provider_instances):
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

    def _resolve_cost_model(self, provider_instance):
        """Item 84 — read the per-provider `CostModel` from the bonded
        provider class. Returns None when the provider does not declare
        one (the framework treats absence as "not metered" rather than
        emitting zero-cost noise)."""
        provider_cls = getattr(provider_instance, "provider_class", None)
        if provider_cls is None or not inspect.isclass(provider_cls):
            return None
        return getattr(provider_cls, "cost_model", None)

    def _tenant_id(self) -> str:
        """Item 84 — best-effort tenant identifier for cost metric labels."""
        team_id = getattr(self.requester, "team_id", None)
        if team_id:
            return str(team_id)
        user_id = getattr(self.requester, "id", None)
        if user_id:
            return str(user_id)
        return "unknown"

    def _backoff_sleep(self, base_ms: int, max_ms: int, jitter: float, attempt: int) -> None:
        """Exponential backoff with jitter; honors `max_ms` ceiling."""
        delay_ms = min(max_ms, base_ms * (2 ** max(0, attempt - 1)))
        if jitter > 0:
            factor = 1.0 + random.uniform(-jitter, jitter)
            delay_ms = max(0.0, delay_ms * factor)
        time.sleep(delay_ms / 1000.0)

    def _sticky_pin_usable(self, rpi) -> bool:
        """Item 51: a pinned RPI is usable iff it is not under an active
        auth cooldown. Other health signals (DB unreachability, instance
        deletion) are surfaced when the pinned attempt actually runs."""
        cooldown_expires = _AUTH_COOLDOWNS.get(str(rpi.provider_instance_id))
        if cooldown_expires is None:
            return True
        return time.monotonic() >= cooldown_expires

    def _attempt_pinned(self, rpi, callable_func, args, kwargs):
        """Item 51: run `callable_func` against the pinned RPI exactly once.

        Returns ``(result, succeeded)`` so the caller can refresh the
        sticky pin TTL on success or fall through to the surviving-chain
        rotation on failure. Errors that linear rotation would surface
        immediately (`InvalidInputExternalError`, `PermanentExternalError`)
        are re-raised through the pin path as well — sticky pinning never
        masks application-level invalid input.
        """
        try:
            with self.model_registry.DB.get_session() as session:
                provider_instance = ProviderInstanceModel.DB(
                    self.model_registry.DB.Base
                ).get(
                    requester_id=self.requester.id,
                    model_registry=self.model_registry,
                    return_type="dto",
                    override_dto=ProviderInstanceModel,
                    id=rpi.provider_instance_id,
                )
        except Exception:
            return None, False
        if not provider_instance:
            return None, False
        try:
            result = callable_func(provider_instance, *args, **kwargs)
            return result, True
        except Exception as exc:
            policy_mod = self._load_rotation_policy_module()
            if policy_mod is not None:
                InvalidInput = getattr(policy_mod, "InvalidInputExternalError", None)
                Permanent = getattr(policy_mod, "PermanentExternalError", None)
                if InvalidInput and Permanent and isinstance(exc, (InvalidInput, Permanent)):
                    raise
            return None, False

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
        """
        from fastapi import HTTPException

        # Item 51 — extract sticky-session controls before passing kwargs through.
        routing_hint: Optional[RoutingHint] = kwargs.pop("routing_hint", None)
        ability: Optional[str] = kwargs.pop("ability", None)

        # Get all rotation provider instances for this rotation
        if not self.target_id:
            raise HTTPException(
                status_code=400, detail="No target rotation ID set for rotation manager"
            )

        # Get rotation provider instances ordered by hierarchy
        rotation_provider_instances = self._get_ordered_rotation_provider_instances()

        if not rotation_provider_instances:
            raise HTTPException(
                status_code=404,
                detail=f"No provider instances found for rotation {self.target_id}",
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
                    pin_result, pin_succeeded = self._attempt_pinned(
                        pinned_rpi, callable_func, args, kwargs
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

        attempted_providers = []
        attempted_provider_instances: List[Any] = []
        last_exception = None
        policy_mod = self._load_rotation_policy_module()

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
                with self.model_registry.DB.get_session() as session:
                    provider_instance = ProviderInstanceModel.DB(
                        self.model_registry.DB.Base
                    ).get(
                        requester_id=self.requester.id,
                        model_registry=self.model_registry,
                        return_type="dto",
                        override_dto=ProviderInstanceModel,
                        id=rpi.provider_instance_id,
                    )
            except Exception as exc:  # back-compat: bare exception advances
                logger.warning(f"Provider lookup failed: {exc}")
                attempted_providers.append(
                    {
                        "provider_instance_id": rpi.provider_instance_id,
                        "error": f"lookup failed: {exc}",
                    }
                )
                last_exception = exc
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

            while True:
                try:
                    logger.debug(
                        f"Attempting to use provider instance: {provider_instance.name}"
                    )
                    result = callable_func(provider_instance, *args, **kwargs)
                    logger.debug(
                        f"Successfully used provider instance: {provider_instance.name}"
                    )
                    # Item 84 — emit per-tenant cost metric. Failure here
                    # must NEVER fail the rotation call.
                    cost_model = self._resolve_cost_model(provider_instance)
                    if cost_model is not None:
                        try:
                            cost = cost_model((args, kwargs), result)
                            key = (
                                self._tenant_id(),
                                str(getattr(provider_instance, "name", "unknown")),
                                str(ability or "unknown"),
                            )
                            current = RotationManager._cost_counter.get(key, Decimal("0"))
                            RotationManager._cost_counter[key] = current + Decimal(str(cost))
                        except Exception as cost_exc:
                            logger.warning(
                                "cost_model raised for provider %s: %s",
                                getattr(provider_instance, "name", "unknown"),
                                cost_exc,
                            )
                    # Item 51 — on a successful default-rotation attempt with a
                    # stickiness key, the winning provider becomes the new pin.
                    if sticky_key:
                        _sticky_set(
                            sticky_key, ability, str(rpi.provider_instance_id)
                        )
                    return result
                except Exception as exc:
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
                            base = getattr(policy, "rate_limit_base_ms", 1000) if policy else 1000
                            mx = getattr(policy, "rate_limit_max_ms", 60000) if policy else 60000
                            wait = getattr(exc, "retry_after_seconds", None)
                            if wait is not None:
                                time.sleep(float(wait))
                            else:
                                self._backoff_sleep(base, mx, 0.1, rate_limit_attempt)
                            # Stay on the same provider; do not advance.
                            last_exception = exc
                            continue

                        if isinstance(exc, Auth):
                            # Mark unhealthy via cooldown; advance.
                            cooldown = getattr(policy, "auth_cooldown_seconds", 300) if policy else 300
                            _AUTH_COOLDOWNS[str(rpi.provider_instance_id)] = (
                                time.monotonic() + float(cooldown)
                            )
                            logger.warning(
                                "Auth failure on %s; advancing (cooldown=%ss)",
                                provider_instance.name, cooldown,
                            )
                            attempted_providers.append(
                                {
                                    "provider_instance_id": rpi.provider_instance_id,
                                    "provider_name": provider_instance.name,
                                    "error": f"auth: {exc}",
                                }
                            )
                            last_exception = exc
                            advance_after_loop = True
                            break

                        if isinstance(exc, Transient):
                            transient_attempt += 1
                            mr = getattr(policy, "transient_max_retries", 3) if policy else 3
                            if transient_attempt <= mr:
                                base = getattr(policy, "transient_base_ms", 100) if policy else 100
                                mx = getattr(policy, "transient_max_ms", 5000) if policy else 5000
                                jit = getattr(policy, "transient_jitter", 0.1) if policy else 0.1
                                self._backoff_sleep(base, mx, jit, transient_attempt)
                                last_exception = exc
                                continue
                            attempted_providers.append(
                                {
                                    "provider_instance_id": rpi.provider_instance_id,
                                    "provider_name": provider_instance.name,
                                    "error": f"transient (exhausted): {exc}",
                                }
                            )
                            last_exception = exc
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
                    last_exception = exc
                    advance_after_loop = True
                    break

            if advance_after_loop:
                continue

        # If we get here, all providers failed
        error_detail = {
            "message": f"All provider instances failed for rotation {self.target_id}",
            "attempted_providers": attempted_providers,
            "total_attempts": len(attempted_providers),
        }

        # Item 48 — graceful degradation dispatch on chain exhaustion.
        degradation = self._resolve_degradation_policy(attempted_provider_instances)
        mode = getattr(degradation, "mode", None) if degradation else None
        mode_value = getattr(mode, "value", mode)

        if mode_value == "queue_and_retry":
            store = RotationManager._outbox_store
            if store is None:
                raise HTTPException(status_code=500, detail=error_detail)
            from logic.Outbox import OutboxEntry

            first_pi = next(
                (pi for pi in attempted_provider_instances if pi is not None),
                None,
            )
            target_provider = str(getattr(first_pi, "name", "unknown")) if first_pi else "unknown"
            entry = OutboxEntry(
                operation_type="rotation_call",
                target_provider=target_provider,
                target_ability=str(ability or "unknown"),
                payload={
                    "rotation_id": str(self.target_id),
                    "attempted_providers": [
                        str(p.get("provider_instance_id"))
                        for p in attempted_providers
                    ],
                },
                idempotency_key=f"rotation:{self.target_id}:{uuid.uuid4()}",
            )
            tracking_id = store.enqueue(entry)
            from extensions.ExternalErrors import QueuedForRetry

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
            key = (provider_name, ability_name)
            RotationManager._silent_drop_counter[key] = (
                RotationManager._silent_drop_counter.get(key, 0) + 1
            )
            logger.warning(
                "provider_silent_drop provider=%s ability=%s rotation=%s "
                "attempted=%d",
                provider_name,
                ability_name,
                self.target_id,
                len(attempted_providers),
            )
            from extensions.ExternalErrors import SilentDropped

            return SilentDropped(provider=provider_name, ability=ability_name)

        raise HTTPException(status_code=500, detail=error_detail)

    def _get_ordered_rotation_provider_instances(self):
        """
        Get rotation provider instances ordered by parent hierarchy.

        Returns instances in order: parent_id=None first, then their children, etc.
        """
        # Get all rotation provider instances for this rotation
        with self.model_registry.DB.get_session() as session:
            all_instances = RotationProviderInstanceModel.DB(
                self.model_registry.DB.Base
            ).list(
                requester_id=self.requester.id,
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
    metaclass=ModelMeta,
):
    rotation_id: str
    provider_instance_id: str
    permission_references: List[str] = ["rotation"]

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "A RotationProviderInstance represents a link between a Rotation and a ProviderInstance. Order is determined by record parentage (NULL parent is first)."
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["rotation"]

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Create links between root rotations and provider instances."""
        from lib.Environment import inflection

        try:
            logger.debug(
                "Creating rotation/provider-instance instance links for seeding..."
            )

            seed_data = []

            # Get providers from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                extension_registry = model_registry.extension_registry
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
                            # Get consistent provider name
                            if hasattr(prv_class, "name") and prv_class.name:
                                provider_name = prv_class.name
                            else:
                                provider_name = prv_class.__name__.replace(
                                    "Provider", ""
                                ).replace("PRV_", "")
                                logger.warning(
                                    f"Provider class {prv_class.__name__} missing 'name' attribute, using {provider_name}"
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

                            if not has_required_vars:
                                # No instance created for this provider, skip linking
                                logger.debug(
                                    f"Provider '{provider_name}' has no environment variables set, skipping rotation links"
                                )
                                continue

                            # Get or generate friendly names for consistent naming
                            # For provider
                            if (
                                hasattr(prv_class, "friendly_name")
                                and prv_class.friendly_name
                            ):
                                provider_friendly_name = prv_class.friendly_name
                            else:
                                provider_friendly_name = stringcase.titlecase(
                                    provider_name
                                )

                            # For extension (need to get the extension class)
                            ext_class = extension_registry._extension_name_map.get(
                                ext_name
                            )
                            if (
                                ext_class
                                and hasattr(ext_class, "friendly_name")
                                and ext_class.friendly_name
                            ):
                                ext_friendly_name = ext_class.friendly_name
                            else:
                                ext_friendly_name = stringcase.titlecase(ext_name)

                            # Create rotation name using PascalCase of extension name
                            rotation_name = f"Root_{stringcase.pascalcase(ext_name)}"

                            # Create instance name using PascalCase of provider name
                            instance_name = (
                                f"Root_{stringcase.pascalcase(provider_name)}"
                            )

                            link_data = {
                                "_rotation_name": rotation_name,  # Will be resolved to rotation_id
                                "_provider_instance_name": instance_name,  # Instance name
                                "parent_id": None,  # Root level in hierarchy
                            }

                            seed_data.append(link_data)
                            logger.debug(
                                f"Added rotation/provider-instance instance link for rotation '{rotation_name}' and instance 'Root_{provider_name}'"
                            )

                        except Exception as e:
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

        except Exception as e:
            logger.error(
                f"Error creating rotation/provider-instance instance links for seeding: {e}"
            )
            return []

    class Create(BaseModel):
        rotation_id: str
        provider_instance_id: str
        parent_id: Optional[str] = None

    class Update(BaseModel):
        rotation_id: Optional[str] = None
        provider_instance_id: Optional[str] = None
        parent_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ParentMixinModel.Search,
    ):
        rotation_id: Optional[StringSearchModel] = None
        provider_instance_id: Optional[StringSearchModel] = None


class RotationProviderInstanceManager(AbstractBLLManager, RouterMixin):
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
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
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

    @property
    def rotation(self) -> "RotationManager":
        """Get the rotation manager instance."""
        if self._rotation is None:
            # Import locally to avoid circular imports
            self._rotation = RotationManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._rotation

    def create_validation(self, entity):
        """Validate rotation provider instance creation - check that rotation and provider instance exist."""
        # Use ROOT_ID to bypass permission filtering for pure existence checks
        # Check that rotation exists
        if entity.rotation_id:
            rotation = RotationModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.rotation_id,
            )
            if not rotation:
                raise HTTPException(status_code=404, detail="Rotation not found")

        # Check that provider instance exists
        if entity.provider_instance_id:
            provider_instance = ProviderInstanceModel.DB(
                self.model_registry.DB.manager.Base
            ).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.provider_instance_id,
            )
            if not provider_instance:
                raise HTTPException(
                    status_code=404, detail="Provider instance not found"
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
