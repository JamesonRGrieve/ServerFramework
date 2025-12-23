from typing import Any, ClassVar, Dict, List, Optional

import stringcase
from fastapi import HTTPException
from pydantic import BaseModel, Field

from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import BaseModel
from lib.Pydantic2FastAPI import AuthType, RouterMixin
from logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)


class ExtensionModel(
    ApplicationModel,
    NameMixinModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    description: Optional[str] = Field(None, description="Description of the extension")
    friendly_name: Optional[str] = Field(
        None, description="Human-readable name for the extension"
    )

    model_config = {"extra": "ignore"}

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "An Extension represents a third-party integration. This is SEPARATE from an oauth link."
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Dynamically discover and return extension seed data."""
        try:
            logger.debug("Discovering extensions for seeding...")

            seed_data = []

            # Get extension classes from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                logger.debug(
                    f"Using ExtensionRegistry from ModelRegistry with {len(model_registry.extension_registry.extensions)} extensions"
                )

                # Get only the extensions that are actually loaded in the registry
                for (
                    ext_name,
                    ext_class,
                ) in model_registry.extension_registry._extension_name_map.items():
                    if hasattr(ext_class, "name") and ext_class.name:
                        # Generate friendly name from extension name
                        friendly_name = stringcase.titlecase(ext_class.name).replace(
                            " ", " "
                        )

                        ext_data = {
                            "name": ext_class.name,
                            "friendly_name": friendly_name,
                            "description": getattr(
                                ext_class,
                                "description",
                                f"Runtime extension {ext_class.name}",
                            ),
                        }
                        seed_data.append(ext_data)
                        logger.debug(f"Added extension {ext_class.name} to seed data")
                    else:
                        logger.warning(
                            f"Extension class {ext_class.__name__} missing required 'name' attribute"
                        )
            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no extensions to seed"
                )

            logger.debug(f"Total extensions in seed data: {len(seed_data)}")
            return seed_data

        except Exception as e:
            logger.error(f"Error discovering extensions for seeding: {e}")
            return []

    class Create(BaseModel, NameMixinModel):
        description: Optional[str] = Field(
            None, description="Description of the extension"
        )
        friendly_name: Optional[str] = Field(
            None, description="Human-readable name for the extension"
        )

    class Update(BaseModel, NameMixinModel.Optional):
        description: Optional[str] = Field(
            None, description="Description of the extension"
        )
        friendly_name: Optional[str] = Field(
            None, description="Human-readable name for the extension"
        )

    class Search(ApplicationModel.Search, NameMixinModel.Search):
        description: Optional[StringSearchModel] = None
        friendly_name: Optional[StringSearchModel] = None


class ExtensionManager(AbstractBLLManager, RouterMixin):
    _model = ExtensionModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/extension"
    tags: ClassVar[Optional[List[str]]] = ["Extension Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # routes_to_register defaults to None, which includes all routes
    example_overrides: ClassVar[Dict[str, Dict]] = {
        "get": {
            "extension": {
                "id": "ext-10b5fc76-7b5d-4d28-ac9c-215488921624",
                "name": "WebSearch",
                "description": "Provides web search abilities to agents",
                "created_at": "2023-09-15T14:30:00Z",
                "updated_at": "2023-09-15T14:30:00Z",
            }
        },
    }
    auth_dependency: ClassVar[Optional[str]] = "api_key_auth"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ) -> None:
        """
        Initialize ExtensionManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team (kept for backward compatibility)
            model_registry: Model registry instance (required for proper extension support)
        """
        # Initialize parent with the parameters
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        # Initialize ability manager to None
        self._abilities = None

    @property
    def abilities(self) -> "AbilityManager":
        """
        Get the ability manager for this extension manager.

        Returns:
            AbilityManager instance
        """
        if self._abilities is None:
            self._abilities = AbilityManager(
                requester_id=self.requester_id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._abilities

    @staticmethod
    def list_runtime_extensions() -> List[str]:
        """
        Get list of available runtime extensions.

        Returns:
            List of extension names available at runtime
        """
        import glob
        import logging
        import os

        from lib.Environment import env

        # Get extensions from APP_EXTENSIONS environment variable
        app_extensions = env("APP_EXTENSIONS")
        if app_extensions:
            extension_list = [
                ext.strip() for ext in app_extensions.split(",") if ext.strip()
            ]
            if extension_list:
                logger.debug(f"Using extensions from APP_EXTENSIONS: {extension_list}")
                return extension_list

        # Fallback to finding EXT_*.py files
        try:
            # Get the current directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            src_dir = os.path.dirname(current_dir)  # parent of logic dir
            extensions_dir = os.path.join(src_dir, "extensions")

            logger.debug(f"Looking for extensions in: {extensions_dir}")

            extensions = []
            # Look for EXT_*.py files in the extensions directory
            for ext_path in glob.glob(
                os.path.join(extensions_dir, "**", "EXT_*.py"), recursive=True
            ):
                ext_name = os.path.splitext(os.path.basename(ext_path))[0]
                if ext_name.startswith("EXT_"):
                    ext_name = ext_name[4:]
                logger.debug(f"Found extension: {ext_name}")
                extensions.append(ext_name)

            return extensions
        except Exception as e:
            logger.error(f"Error finding extensions: {str(e)}")
            return []


class AbilityModel(
    ApplicationModel,
    NameMixinModel,
    UpdateMixinModel,
    ExtensionModel.Reference,
    metaclass=ModelMeta,
):
    model_config = {"extra": "ignore"}
    meta: bool = Field(
        False,
        description="Whether the ability is performed by the extension itself statically rather than a specific provider.",
    )
    friendly_name: Optional[str] = Field(
        None, description="Human-readable name for the ability"
    )
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "An Ability represents something an extension can do."
    )
    is_system_entity: ClassVar[bool] = True

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Dynamically discover and return ability seed data from extensions."""
        try:
            logger.debug("Discovering abilities for seeding...")

            abilities_data = []

            # Get abilities from ModelRegistry's ExtensionRegistry
            if (
                model_registry
                and hasattr(model_registry, "extension_registry")
                and model_registry.extension_registry
            ):
                extension_registry = model_registry.extension_registry
                logger.debug(f"Using ExtensionRegistry for ability discovery")

                # Iterate through registered extensions and their abilities
                for (
                    ext_name,
                    abilities,
                ) in extension_registry.extension_abilities.items():
                    logger.debug(
                        f"Processing {len(abilities)} abilities for extension: {ext_name}"
                    )

                    for ability_info in abilities:
                        # Generate friendly name from ability name
                        friendly_name = stringcase.titlecase(
                            ability_info["name"]
                        ).replace("_", " ")

                        abilities_data.append(
                            {
                                "name": ability_info["name"],
                                "friendly_name": friendly_name,
                                "_extension_name": ext_name,  # Will be resolved to extension_id during seeding
                                "meta": ability_info.get("meta", False),
                            }
                        )
                        logger.debug(
                            f"Added ability: {ability_info['name']} (meta={ability_info.get('meta', False)}) for extension {ext_name}"
                        )

                # Also add provider abilities (non-meta)
                for (
                    provider_class,
                    provider_abilities,
                ) in extension_registry.provider_abilities.items():
                    for ability_info in provider_abilities:
                        ext_name = ability_info["extension_name"]
                        # Generate friendly name from ability name
                        friendly_name = stringcase.titlecase(
                            ability_info["name"]
                        ).replace("_", " ")

                        abilities_data.append(
                            {
                                "name": ability_info["name"],
                                "friendly_name": friendly_name,
                                "_extension_name": ext_name,  # Will be resolved to extension_id during seeding
                                "meta": False,  # Provider abilities are never meta
                            }
                        )
                        logger.debug(
                            f"Added provider ability: {ability_info['name']} for extension {ext_name}"
                        )

            else:
                logger.warning(
                    "No ExtensionRegistry available in ModelRegistry, no abilities to seed"
                )

            logger.debug(f"Generated {len(abilities_data)} ability seed records")
            return abilities_data

        except Exception as e:
            logger.error(f"Error generating ability seed data: {e}")
            return []

    class Create(BaseModel, NameMixinModel, ExtensionModel.Reference.ID):
        meta: Optional[bool] = False
        friendly_name: Optional[str] = None

    class Update(
        BaseModel, NameMixinModel.Optional, ExtensionModel.Reference.ID.Optional
    ):
        meta: Optional[bool] = None
        friendly_name: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        NameMixinModel.Search,
        ExtensionModel.Reference.ID.Search,
    ):
        meta: Optional[bool] = None
        friendly_name: Optional[StringSearchModel] = None


class AbilityManager(AbstractBLLManager, RouterMixin):
    _model = AbilityModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/ability"
    tags: ClassVar[Optional[List[str]]] = ["Ability Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # routes_to_register defaults to None, which includes all routes
    example_overrides: ClassVar[Dict[str, Dict]] = {
        "get": {
            "ability": {
                "id": "ability-550e8400-e29b-41d4-a716-446655440000",
                "name": "web_search",
                "description": "Search the web for information",
                "created_at": "2023-09-15T14:30:00Z",
                "updated_at": "2023-09-15T14:30:00Z",
            }
        },
    }
    auth_dependency: ClassVar[Optional[str]] = "api_key_auth"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ) -> None:
        """
        Initialize AbilityManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team (kept for backward compatibility)
            model_registry: Model registry instance (required for proper extension support)
        """
        # Call parent constructor
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    def create_validation(self, entity):
        """Validate ability creation - check that extension exists."""
        if entity.extension_id:
            extension = ExtensionModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.extension_id,
            )
            if not extension:
                raise HTTPException(status_code=404, detail="Extension not found")


ExtensionModel.Manager = ExtensionManager
AbilityModel.Manager = AbilityManager
