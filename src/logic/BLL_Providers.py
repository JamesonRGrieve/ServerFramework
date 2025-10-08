from typing import Any, ClassVar, Dict, List, Optional

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
        # Database constraints ensure referenced provider instance exists.


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

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
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

    def rotate(self, callable_func, *args, **kwargs):
        """
        Execute a callable with each provider instance in the rotation sequence.

        Tries each provider in hierarchy order (parent_id=None first, then children).
        Catches exceptions, logs failures, and continues to the next provider.
        Only raises HTTPException if all providers fail.

        Args:
            callable_func: Function to call with each ProviderInstanceModel
            *args: Positional arguments to pass to the callable function
            **kwargs: Keyword arguments to pass to the callable function

        Returns:
            Result from the first successful provider call

        Raises:
            HTTPException: If all providers fail, includes list of attempted providers
        """
        from fastapi import HTTPException

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

        attempted_providers = []
        last_exception = None

        for rpi in rotation_provider_instances:
            try:
                # Get the actual provider instance
                with self.model_registry.DB.get_session() as session:
                    provider_instance = ProviderInstanceModel.DB(
                        self.model_registry.DB.Base
                    ).get(
                        requester_id=self.requester.id,
                        # db=session,
                        model_registry=self.model_registry,
                        return_type="dto",
                        override_dto=ProviderInstanceModel,
                        id=rpi.provider_instance_id,
                    )

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

                # Attempt to call the function with the provider instance and additional args/kwargs
                logger.debug(
                    f"Attempting to use provider instance: {provider_instance.name}"
                )
                result = callable_func(provider_instance, *args, **kwargs)

                # TODO: result may come with error details, need to handle that
                # If we get here, the call was successful
                logger.debug(
                    f"Successfully used provider instance: {provider_instance.name}"
                )
                return result

            except Exception as e:
                # Log the failure and add to attempted list
                provider_name = (
                    getattr(provider_instance, "name", "Unknown")
                    if "provider_instance" in locals()
                    else "Unknown"
                )
                logger.warning(f"Provider instance {provider_name} failed: {str(e)}")

                attempted_providers.append(
                    {
                        "provider_instance_id": rpi.provider_instance_id,
                        "provider_name": provider_name,
                        "error": str(e),
                    }
                )
                last_exception = e
                continue

        # If we get here, all providers failed
        error_detail = {
            "message": f"All provider instances failed for rotation {self.target_id}",
            "attempted_providers": attempted_providers,
            "total_attempts": len(attempted_providers),
        }

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
        # Database constraints ensure referenced rotation exists.

        # Prevent circular parent relationships
        if entity.parent_id == entity.rotation_id:
            raise HTTPException(
                status_code=400,
                detail="A rotation provider instance cannot be its own parent",
            )
        # Database constraints ensure referenced provider instance exists.


ProviderModel.Manager = ProviderManager
ProviderExtensionModel.Manager = ProviderExtensionManager
ProviderExtensionAbilityModel.Manager = ProviderExtensionAbilityManager
ProviderInstanceModel.Manager = ProviderInstanceManager
ProviderInstanceUsageModel.Manager = ProviderInstanceUsageManager
ProviderInstanceSettingModel.Manager = ProviderInstanceSettingManager
ProviderInstanceExtensionAbilityModel.Manager = ProviderInstanceExtensionAbilityManager
RotationModel.Manager = RotationManager
RotationProviderInstanceModel.Manager = RotationProviderInstanceManager
