"""
Abstract Pydantic Test Framework

This module provides a reusable framework for discovering BLL models and generating
matrix tests for Pydantic to SQLAlchemy scaffolding. It includes mock models that
cover all features found in real DB entities.

Models and managers are organized by domain, where the domain name is extracted from
the BLL file name (e.g., "BLL_User.py" -> "User" domain). Mock models and managers
are stored under the "" (empty string) domain.
"""

import os
import sys
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type, Union

import pytest
import stringcase
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic import BaseNetworkModel
from serverframework.lib.Pydantic2SQLAlchemy import (
    ApplicationModel,
    ImageMixinModel,
    ParentMixinModel,
    UpdateMixinModel,
    clear_registry_cache,
)


# Mock Pydantic models that include all features found in DB entities
class MockComprehensiveModel(
    ApplicationModel, UpdateMixinModel, ImageMixinModel, ParentMixinModel
):
    """
    Mock model that includes all features found in the actual DB entities.
    This ensures our scaffolding system can handle all complex scenarios.
    """

    # Explicitly add id field to ensure it's available for testing
    id: str = Field(..., description="Unique identifier")

    # Basic fields with various types
    name: str = Field(..., description="Required name field")
    friendly_name: Optional[str] = Field(None, description="Optional friendly name")
    description: Optional[str] = Field(None, description="Optional description")

    # Boolean fields
    enabled: bool = Field(True, description="Whether this entity is enabled")
    active: bool = Field(True, description="Whether this entity is active")
    system: bool = Field(False, description="Whether this is a system entity")

    # Integer fields
    max_uses: Optional[int] = Field(None, description="Maximum number of uses")
    mfa_count: int = Field(1, description="MFA count requirement")
    trust_score: int = Field(50, description="Trust score from 0-100")

    # DateTime fields
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
    last_activity: Optional[datetime] = Field(
        None, description="Last activity timestamp"
    )

    # Text/JSON fields (using reserved SQLAlchemy name to test sanitization)
    settings_json: Optional[str] = Field(None, description="JSON settings")
    metadata: Optional[dict] = Field(
        default_factory=dict, description="Metadata dictionary"
    )
    tags: Optional[List[str]] = Field(default_factory=list, description="List of tags")

    # Complex Reference.ID structure with multiple relationships
    class Reference:
        class ID:
            # Required references
            user_id: str = Field(..., description="User who owns this")
            team_id: str = Field(..., description="Team this belongs to")
            provider_id: str = Field(..., description="Provider reference")

            # Optional references
            parent_user_id: str = Field(..., description="Parent user reference")
            extension_id: str = Field(..., description="Extension reference")
            ability_id: str = Field(..., description="Ability reference")
            role_id: str = Field(..., description="Role reference")
            invitation_id: str = Field(..., description="Invitation reference")

            class Optional:
                parent_user_id: Optional[str] | None = None
                extension_id: Optional[str] | None = None
                ability_id: Optional[str] | None = None
                role_id: Optional[str] | None = None
                invitation_id: Optional[str] | None = None

    # Add a second Reference for testing relationship functionality
    class ComprehensiveReference:
        class ID:
            comprehensive_id: str = Field(
                ..., description="The ID of the related comprehensive model"
            )

            class Optional:
                comprehensive_id: Optional[str] | None = None

    # Nested model for complex validation
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Network model support classes
    class Create(BaseModel):
        """Create model for MockComprehensiveModel."""

        name: str = Field(..., description="Required name field")
        friendly_name: Optional[str] = Field(None, description="Optional friendly name")
        description: Optional[str] = Field(None, description="Optional description")
        enabled: bool = Field(True, description="Whether this entity is enabled")
        active: bool = Field(True, description="Whether this entity is active")
        system: bool = Field(False, description="Whether this is a system entity")
        max_uses: Optional[int] = Field(None, description="Maximum number of uses")
        mfa_count: int = Field(1, description="MFA count requirement")
        trust_score: int = Field(50, description="Trust score from 0-100")
        expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
        last_activity: Optional[datetime] = Field(
            None, description="Last activity timestamp"
        )
        settings_json: Optional[str] = Field(None, description="JSON settings")
        metadata: Optional[dict] = Field(
            default_factory=dict, description="Metadata dictionary"
        )
        tags: Optional[List[str]] = Field(
            default_factory=list, description="List of tags"
        )

    class Update(BaseModel):
        """Update model for MockComprehensiveModel."""

        name: Optional[str] = Field(None, description="Required name field")
        friendly_name: Optional[str] = Field(None, description="Optional friendly name")
        description: Optional[str] = Field(None, description="Optional description")
        enabled: Optional[bool] = Field(
            None, description="Whether this entity is enabled"
        )
        active: Optional[bool] = Field(
            None, description="Whether this entity is active"
        )
        system: Optional[bool] = Field(
            None, description="Whether this is a system entity"
        )
        max_uses: Optional[int] = Field(None, description="Maximum number of uses")
        mfa_count: Optional[int] = Field(None, description="MFA count requirement")
        trust_score: Optional[int] = Field(None, description="Trust score from 0-100")
        expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
        last_activity: Optional[datetime] = Field(
            None, description="Last activity timestamp"
        )
        settings_json: Optional[str] = Field(None, description="JSON settings")
        metadata: Optional[dict] = Field(None, description="Metadata dictionary")
        tags: Optional[List[str]] = Field(None, description="List of tags")

    class Search(BaseModel):
        """Search model for MockComprehensiveModel."""

        name: Optional[str] = Field(None, description="Search by name")
        friendly_name: Optional[str] = Field(
            None, description="Search by friendly name"
        )
        enabled: Optional[bool] = Field(None, description="Filter by enabled status")
        active: Optional[bool] = Field(None, description="Filter by active status")
        system: Optional[bool] = Field(None, description="Filter by system status")
        min_trust_score: Optional[int] = Field(None, description="Minimum trust score")
        max_trust_score: Optional[int] = Field(None, description="Maximum trust score")
        tags: Optional[List[str]] = Field(None, description="Filter by tags")


class MockSimpleModel(ApplicationModel):
    """Simple mock model for basic testing."""

    title: str = Field(..., description="Simple title field")
    count: int = Field(0, description="Simple count field")

    class SimpleReference:
        class ID:
            simple_id: str = Field(
                ..., description="The ID of the related simple model"
            )

            class Optional:
                simple_id: Optional[str] | None = None


class MockRelationshipModel(
    ApplicationModel,
    UpdateMixinModel,
    MockComprehensiveModel.ComprehensiveReference.ID,
    MockSimpleModel.SimpleReference.ID.Optional,
):
    """Mock model that references other mock models."""

    name: str = Field(..., description="Relationship model name")

    class RelationshipReference:
        class ID:
            relationship_id: str = Field(
                ..., description="The ID of the related relationship model"
            )

            class Optional:
                relationship_id: Optional[str] | None = None


# Add Create, Update, and Search classes to mock models BEFORE NetworkModel classes
MockComprehensiveModel.Create = type(
    "Create",
    (BaseModel,),
    {
        "name": Field(..., description="Required name field"),
        "friendly_name": Field(None, description="Optional friendly name"),
        "enabled": Field(True, description="Whether this entity is enabled"),
        "__annotations__": {
            "name": str,
            "friendly_name": Optional[str],
            "enabled": bool,
        },
    },
)

MockComprehensiveModel.Update = type(
    "Update",
    (BaseModel,),
    {
        "name": Field(None, description="Required name field"),
        "friendly_name": Field(None, description="Optional friendly name"),
        "enabled": Field(None, description="Whether this entity is enabled"),
        "__annotations__": {
            "name": Optional[str],
            "friendly_name": Optional[str],
            "enabled": Optional[bool],
        },
    },
)

MockComprehensiveModel.Search = type(
    "Search",
    (BaseModel,),
    {
        "name": Field(None, description="Name search"),
        "enabled": Field(None, description="Enabled filter"),
        "__annotations__": {"name": Optional[str], "enabled": Optional[bool]},
    },
)

MockSimpleModel.Create = type(
    "Create",
    (BaseModel,),
    {
        "title": Field(..., description="Simple title field"),
        "count": Field(0, description="Simple count field"),
        "__annotations__": {"title": str, "count": int},
    },
)

MockSimpleModel.Update = type(
    "Update",
    (BaseModel,),
    {
        "title": Field(None, description="Simple title field"),
        "count": Field(None, description="Simple count field"),
        "__annotations__": {"title": Optional[str], "count": Optional[int]},
    },
)

MockSimpleModel.Search = type(
    "Search",
    (BaseModel,),
    {
        "title": Field(None, description="Title search"),
        "__annotations__": {"title": Optional[str]},
    },
)

MockRelationshipModel.Create = type(
    "Create",
    (BaseModel,),
    {
        "name": Field(..., description="Relationship model name"),
        "comprehensive_id": Field(..., description="Reference to comprehensive model"),
        "simple_id": Field(None, description="Reference to simple model"),
        "__annotations__": {
            "name": str,
            "comprehensive_id": str,
            "simple_id": Optional[str],
        },
    },
)

MockRelationshipModel.Update = type(
    "Update",
    (BaseModel,),
    {
        "name": Field(None, description="Relationship model name"),
        "__annotations__": {"name": Optional[str]},
    },
)

MockRelationshipModel.Search = type(
    "Search",
    (BaseModel,),
    {
        "name": Field(None, description="Name search"),
        "__annotations__": {"name": Optional[str]},
    },
)


# Mock NetworkModel classes for endpoint generation testing
class MockComprehensiveNetworkModel(BaseModel):
    """Mock NetworkModel for comprehensive model endpoint testing."""

    class GET(BaseNetworkModel):
        pass

    class LIST(BaseNetworkModel):
        offset: int = Field(0, ge=0)
        limit: int = Field(1000, ge=1, le=1000)
        sort_by: Optional[str] | None = None
        sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$")

    class POST(BaseModel):
        comprehensive: MockComprehensiveModel.Create = Field(
            ..., description="Comprehensive model data"
        )

    class PUT(BaseModel):
        comprehensive: MockComprehensiveModel.Update = Field(
            ..., description="Comprehensive model update data"
        )

    class SEARCH(BaseModel):
        comprehensive: MockComprehensiveModel.Search = Field(
            ..., description="Comprehensive model search criteria"
        )

    class ResponseSingle(BaseModel):
        comprehensive: MockComprehensiveModel = Field(
            ..., description="Single comprehensive model response"
        )

    class ResponsePlural(BaseModel):
        comprehensives: List[MockComprehensiveModel] = Field(
            ..., description="Multiple comprehensive models response"
        )


class MockSimpleNetworkModel(BaseModel):
    """Mock NetworkModel for simple model endpoint testing."""

    class GET(BaseNetworkModel):
        pass

    class LIST(BaseNetworkModel):
        offset: int = Field(0, ge=0)
        limit: int = Field(1000, ge=1, le=1000)
        sort_by: Optional[str] | None = None
        sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$")

    class POST(BaseModel):
        simple: MockSimpleModel.Create = Field(..., description="Simple model data")

    class PUT(BaseModel):
        simple: MockSimpleModel.Update = Field(
            ..., description="Simple model update data"
        )

    class SEARCH(BaseModel):
        simple: MockSimpleModel.Search = Field(
            ..., description="Simple model search criteria"
        )

    class ResponseSingle(BaseModel):
        simple: MockSimpleModel = Field(..., description="Single simple model response")

    class ResponsePlural(BaseModel):
        simples: List[MockSimpleModel] = Field(
            ..., description="Multiple simple models response"
        )


class MockRelationshipNetworkModel(BaseModel):
    """Mock NetworkModel for relationship model endpoint testing."""

    class GET(BaseNetworkModel):
        pass

    class LIST(BaseNetworkModel):
        offset: int = Field(0, ge=0)
        limit: int = Field(1000, ge=1, le=1000)
        sort_by: Optional[str] | None = None
        sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$")

    class POST(BaseModel):
        relationship: MockRelationshipModel.Create = Field(
            ..., description="Relationship model data"
        )

    class PUT(BaseModel):
        relationship: MockRelationshipModel.Update = Field(
            ..., description="Relationship model update data"
        )

    class SEARCH(BaseModel):
        relationship: MockRelationshipModel.Search = Field(
            ..., description="Relationship model search criteria"
        )

    class ResponseSingle(BaseModel):
        relationship: MockRelationshipModel = Field(
            ..., description="Single relationship model response"
        )

    class ResponsePlural(BaseModel):
        relationships: List[MockRelationshipModel] = Field(
            ..., description="Multiple relationship models response"
        )


# Mock Manager classes for endpoint generation testing
class MockComprehensiveManager:
    """Mock Manager for comprehensive model endpoint testing."""

    Model = MockComprehensiveModel
    NetworkModel = MockComprehensiveNetworkModel

    def __init__(self, requester_id: str | None = None, **kwargs):
        self.requester_id = requester_id
        self.requester = type("MockUser", (), {"id": requester_id})()

    def create(self, **kwargs):
        """Mock create method."""
        return MockComprehensiveModel(
            id="mock-comp-1", name="Mock Comprehensive", **kwargs
        )

    def get(self, id: str, **kwargs):
        """Mock get method."""
        return MockComprehensiveModel(id=id, name="Mock Comprehensive")

    def list(self, **kwargs):
        """Mock list method."""
        return [MockComprehensiveModel(id="mock-comp-1", name="Mock Comprehensive")]

    def search(self, **kwargs):
        """Mock search method."""
        return [MockComprehensiveModel(id="mock-comp-1", name="Mock Comprehensive")]

    def update(self, id: str, **kwargs):
        """Mock update method."""
        return MockComprehensiveModel(
            id=id, name="Updated Mock Comprehensive", **kwargs
        )

    def delete(self, id: str):
        """Mock delete method."""
        return True

    def batch_update(self, ids: List[str], **kwargs):
        """Mock batch update method."""
        return [
            MockComprehensiveModel(id=id, name="Batch Updated", **kwargs) for id in ids
        ]

    def batch_delete(self, ids: List[str]):
        """Mock batch delete method."""
        return len(ids)


class MockSimpleManager:
    """Mock Manager for simple model endpoint testing."""

    Model = MockSimpleModel
    NetworkModel = MockSimpleNetworkModel

    def __init__(self, requester_id: str | None = None, **kwargs):
        self.requester_id = requester_id
        self.requester = type("MockUser", (), {"id": requester_id})()

    def create(self, **kwargs):
        """Mock create method."""
        return MockSimpleModel(id="mock-simple-1", title="Mock Simple", **kwargs)

    def get(self, id: str, **kwargs):
        """Mock get method."""
        return MockSimpleModel(id=id, title="Mock Simple")

    def list(self, **kwargs):
        """Mock list method."""
        return [MockSimpleModel(id="mock-simple-1", title="Mock Simple")]

    def search(self, **kwargs):
        """Mock search method."""
        return [MockSimpleModel(id="mock-simple-1", title="Mock Simple")]

    def update(self, id: str, **kwargs):
        """Mock update method."""
        return MockSimpleModel(id=id, title="Updated Mock Simple", **kwargs)

    def delete(self, id: str):
        """Mock delete method."""
        return True

    def batch_update(self, ids: List[str], **kwargs):
        """Mock batch update method."""
        return [MockSimpleModel(id=id, title="Batch Updated", **kwargs) for id in ids]

    def batch_delete(self, ids: List[str]):
        """Mock batch delete method."""
        return len(ids)


class MockRelationshipManager:
    """Mock Manager for relationship model endpoint testing."""

    Model = MockRelationshipModel
    NetworkModel = MockRelationshipNetworkModel

    def __init__(self, requester_id: str | None = None, **kwargs):
        self.requester_id = requester_id
        self.requester = type("MockUser", (), {"id": requester_id})()

    def create(self, **kwargs):
        """Mock create method."""
        return MockRelationshipModel(
            id="mock-rel-1", name="Mock Relationship", **kwargs
        )

    def get(self, id: str, **kwargs):
        """Mock get method."""
        return MockRelationshipModel(id=id, name="Mock Relationship")

    def list(self, **kwargs):
        """Mock list method."""
        return [MockRelationshipModel(id="mock-rel-1", name="Mock Relationship")]

    def search(self, **kwargs):
        """Mock search method."""
        return [MockRelationshipModel(id="mock-rel-1", name="Mock Relationship")]

    def update(self, id: str, **kwargs):
        """Mock update method."""
        return MockRelationshipModel(id=id, name="Updated Mock Relationship", **kwargs)

    def delete(self, id: str):
        """Mock delete method."""
        return True

    def batch_update(self, ids: List[str], **kwargs):
        """Mock batch update method."""
        return [
            MockRelationshipModel(id=id, name="Batch Updated", **kwargs) for id in ids
        ]

    def batch_delete(self, ids: List[str]):
        """Mock batch delete method."""
        return len(ids)


# Global cache for discovered models - now organized by domain
_DISCOVERED_MODELS_CACHE = None

# Global cache for discovered managers - organized by domain
_DISCOVERED_MANAGERS_CACHE = None

# Flag to track if discovery has been completed
_DISCOVERY_COMPLETED = False


def discover_bll_models_for_testing(
    force_refresh: bool = False,
) -> Dict[str, List[tuple]]:
    """
    Discover BLL models for testing purposes, organized by domain.

    Args:
        force_refresh: Whether to force a refresh of the cache

    Returns:
        Dict mapping domain names to lists of (model_name, model_class) tuples.
        Mock models are stored under the "" key.
    """
    global _DISCOVERED_MODELS_CACHE, _DISCOVERY_COMPLETED

    if (
        _DISCOVERED_MODELS_CACHE is not None
        and not force_refresh
        and _DISCOVERY_COMPLETED
    ):
        logger.debug(
            f"Using cached BLL models: {sum(len(models) for models in _DISCOVERED_MODELS_CACHE.values())} total models"
        )
        return _DISCOVERED_MODELS_CACHE

    logger.debug("Discovering BLL models for testing...")

    from serverframework.lib.Pydantic import ModelRegistry

    _DISCOVERED_MODELS_CACHE = {}

    try:
        # Create a temporary registry for model discovery
        registry = ModelRegistry()

        # Import all BLL files using the new registry-based approach
        imported_modules, import_errors = registry._scoped_import(
            file_type="BLL", scopes=["logic"]
        )

        if import_errors:
            logger.debug(f"Warning: Failed to import some BLL modules: {import_errors}")

        # Discover models from imported modules
        for module_name in imported_modules:
            try:
                module = sys.modules[module_name]

                # Extract domain from module name like "serverframework.logic.BLL_User" -> "User"
                domain = ""
                if "." in module_name:
                    module_parts = module_name.split(".")
                    for part in module_parts:
                        if part.startswith("BLL_"):
                            domain = part[4:]  # Remove "BLL_" prefix
                            break

                # Initialize domain list if not exists
                if domain not in _DISCOVERED_MODELS_CACHE:
                    _DISCOVERED_MODELS_CACHE[domain] = []

                # Find all model classes in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)

                    # Check if it's a model class
                    if (
                        isinstance(attr, type)
                        and attr_name.endswith("Model")
                        and not attr_name.startswith("_")
                        and hasattr(
                            attr, "__annotations__"
                        )  # Pydantic models have annotations
                    ):
                        # Skip reference and network models
                        if (
                            attr_name.endswith("ReferenceModel")
                            or attr_name.endswith("NetworkModel")
                            or "." in attr_name
                        ):
                            continue

                        # Skip mixin models themselves
                        if (
                            attr_name.endswith("MixinModel")
                            or attr_name == "ApplicationModel"
                            or attr_name == "UpdateMixinModel"
                            or attr_name == "ImageMixinModel"
                            or attr_name == "ParentMixinModel"
                        ):
                            continue

                        # Skip utility models
                        if (
                            attr_name.endswith("SearchModel")
                            or attr_name == "StringSearchModel"
                        ):
                            continue

                        # Check if the model derives from ApplicationModel
                        try:
                            if hasattr(attr, "__mro__"):
                                # Check if ApplicationModel is in the inheritance chain
                                has_base_mixin = any(
                                    base.__name__ == "ApplicationModel"
                                    for base in attr.__mro__
                                )

                                if has_base_mixin:
                                    _DISCOVERED_MODELS_CACHE[domain].append(
                                        (attr_name, attr)
                                    )
                                    logger.debug(
                                        f"Discovered model: {attr_name} from {module_name} (domain: {domain})"
                                    )
                                else:
                                    logger.debug(
                                        f"Skipping {attr_name}: does not derive from ApplicationModel"
                                    )

                        except Exception as e:
                            logger.debug(
                                f"Error checking inheritance for {attr_name}: {e}"
                            )

            except Exception as e:
                logger.debug(f"Error discovering models from {module_name}: {e}")
                continue

    except Exception as e:
        logger.debug(f"Error during model discovery: {e}")

    # Add mock models to the "" domain
    _DISCOVERED_MODELS_CACHE[""] = [
        ("MockComprehensiveModel", MockComprehensiveModel),
        ("MockSimpleModel", MockSimpleModel),
        ("MockRelationshipModel", MockRelationshipModel),
    ]

    total_models = sum(len(models) for models in _DISCOVERED_MODELS_CACHE.values())
    real_models = total_models - len(_DISCOVERED_MODELS_CACHE.get("", []))
    logger.debug(
        f"Discovered {real_models} real BLL models and {len(_DISCOVERED_MODELS_CACHE.get('', []))} mock models for testing"
    )
    logger.debug(f"Models organized by domain: {list(_DISCOVERED_MODELS_CACHE.keys())}")

    return _DISCOVERED_MODELS_CACHE


def discover_bll_managers_for_testing(
    force_refresh: bool = False,
) -> Dict[str, List[tuple]]:
    """
    Discover BLL managers for FastAPI testing purposes, organized by domain.

    Args:
        force_refresh: Whether to force a refresh of the cache

    Returns:
        Dict mapping domain names to lists of (manager_name, manager_class) tuples.
        Mock managers are stored under the "" key.
    """
    global _DISCOVERED_MANAGERS_CACHE, _DISCOVERY_COMPLETED

    if (
        _DISCOVERED_MANAGERS_CACHE is not None
        and not force_refresh
        and _DISCOVERY_COMPLETED
    ):
        logger.debug(
            f"Using cached BLL managers: {sum(len(managers) for managers in _DISCOVERED_MANAGERS_CACHE.values())} total managers"
        )
        return _DISCOVERED_MANAGERS_CACHE

    logger.debug("Discovering BLL managers for testing...")

    from serverframework.lib.Pydantic import ModelRegistry

    _DISCOVERED_MANAGERS_CACHE = {}

    try:
        # Create a temporary registry for manager discovery
        registry = ModelRegistry()

        # Import all BLL files using the new registry-based approach
        imported_modules, import_errors = registry._scoped_import(
            file_type="BLL", scopes=["logic"]
        )

        if import_errors:
            logger.debug(f"Warning: Failed to import some BLL modules: {import_errors}")

        # Discover managers from imported modules
        for module_name in imported_modules:
            try:
                module = sys.modules[module_name]

                # Extract domain from module name like "serverframework.logic.BLL_User" -> "User"
                domain = ""
                if "." in module_name:
                    module_parts = module_name.split(".")
                    for part in module_parts:
                        if part.startswith("BLL_"):
                            domain = part[4:]  # Remove "BLL_" prefix
                            break

                # Initialize domain list if not exists
                if domain not in _DISCOVERED_MANAGERS_CACHE:
                    _DISCOVERED_MANAGERS_CACHE[domain] = []

                # Find all manager classes in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)

                    # Check if it's a manager class
                    if (
                        isinstance(attr, type)
                        and attr_name.endswith("Manager")
                        and not attr_name.startswith("_")
                        and hasattr(attr, "BaseModel")
                    ):
                        # Check if the BaseModel inherits from ApplicationModel
                        model_class = attr.BaseModel
                        if hasattr(model_class, "__mro__"):
                            # Check if ApplicationModel is in the inheritance chain
                            has_base_mixin = any(
                                base.__name__ == "ApplicationModel"
                                for base in model_class.__mro__
                            )

                            if has_base_mixin:
                                _DISCOVERED_MANAGERS_CACHE[domain].append(
                                    (attr_name, attr)
                                )
                                logger.debug(
                                    f"Discovered manager: {attr_name} from {module_name} (domain: {domain})"
                                )

            except Exception as e:
                logger.debug(f"Error discovering managers from {module_name}: {e}")
                continue

    except Exception as e:
        logger.debug(f"Error during manager discovery: {e}")

    # Add mock managers to the "" domain
    _DISCOVERED_MANAGERS_CACHE[""] = [
        ("MockComprehensiveManager", MockComprehensiveManager),
        ("MockSimpleManager", MockSimpleManager),
        ("MockRelationshipManager", MockRelationshipManager),
    ]

    total_managers = sum(
        len(managers) for managers in _DISCOVERED_MANAGERS_CACHE.values()
    )
    real_managers = total_managers - len(_DISCOVERED_MANAGERS_CACHE.get("", []))
    logger.debug(
        f"Discovered {real_managers} real BLL managers and {len(_DISCOVERED_MANAGERS_CACHE.get('', []))} mock managers for testing"
    )
    logger.debug(
        f"Managers organized by domain: {list(_DISCOVERED_MANAGERS_CACHE.keys())}"
    )

    return _DISCOVERED_MANAGERS_CACHE


def _ensure_discovery_completed():
    """
    Ensure both models and managers have been discovered and cached.
    This function should be called once at the start of test runs.
    """
    global _DISCOVERY_COMPLETED

    if not _DISCOVERY_COMPLETED:
        logger.debug("Performing initial BLL discovery for test session...")

        # Discover both models and managers
        discover_bll_models_for_testing(force_refresh=False)
        discover_bll_managers_for_testing(force_refresh=False)

        _DISCOVERY_COMPLETED = True
        logger.debug("BLL discovery completed and cached for test session.")


def clear_discovery_cache():
    """
    Clear the discovery cache. Useful for testing or when BLL modules change.
    """
    global _DISCOVERED_MODELS_CACHE, _DISCOVERED_MANAGERS_CACHE, _DISCOVERY_COMPLETED

    _DISCOVERED_MODELS_CACHE = None
    _DISCOVERED_MANAGERS_CACHE = None
    _DISCOVERY_COMPLETED = False
    logger.debug("Discovery cache cleared.")


def get_mock_models_for_testing() -> List[tuple]:
    """
    Get mock models for testing.

    Returns:
        List of (model_name, model_class) tuples
    """
    # Get models from the "" domain in the cache
    models_by_domain = discover_bll_models_for_testing()
    return models_by_domain.get("", [])


def get_all_models_for_testing(
    include_mocks: bool = True, force_refresh: bool = False
) -> List[tuple]:
    """
    Get all models for testing (real BLL models + mock models).

    Args:
        include_mocks: Whether to include mock models
        force_refresh: Whether to force a refresh of the BLL model cache

    Returns:
        List of (model_name, model_class) tuples
    """
    # Ensure discovery is completed
    if not force_refresh:
        _ensure_discovery_completed()

    models_by_domain = discover_bll_models_for_testing(force_refresh)

    # Flatten all models from all domains into a single list
    all_models = []
    mock_count = 0
    real_count = 0

    for domain, models in models_by_domain.items():
        if domain == "":  # Mock models
            if include_mocks:
                all_models.extend(models)
                mock_count = len(models)
        else:  # Real BLL models
            all_models.extend(models)
            real_count += len(models)

    if include_mocks:
        logger.debug(
            f"Total models for testing: {len(all_models)} (Real: {real_count}, Mock: {mock_count})"
        )
    else:
        logger.debug(
            f"Total models for testing: {len(all_models)} (Real BLL models only)"
        )

    return all_models


def get_models_by_domain_for_testing(
    include_mocks: bool = True, force_refresh: bool = False
) -> Dict[str, List[tuple]]:
    """
    Get all models for testing organized by domain.

    Args:
        include_mocks: Whether to include mock models (in "" domain)
        force_refresh: Whether to force a refresh of the BLL model cache

    Returns:
        Dict mapping domain names to lists of (model_name, model_class) tuples.
        Mock models are stored under the "" key.
    """
    # Ensure discovery is completed
    if not force_refresh:
        _ensure_discovery_completed()

    models_by_domain = discover_bll_models_for_testing(force_refresh)

    if not include_mocks:
        # Remove mock models (empty string domain)
        return {
            domain: models
            for domain, models in models_by_domain.items()
            if domain != ""
        }

    return models_by_domain


def create_matrix_test_class(
    test_function: Callable[[str, Type[BaseModel]], None],
    class_name: str = "GeneratedMatrixTest",
    include_mocks: bool = True,
    base_classes: tuple = (),
    setup_method: Optional[Callable] | None = None,
    teardown_method: Optional[Callable] | None = None,
) -> Type:
    """
    Create a matrix test class that runs a test function for each discovered model.

    Args:
        test_function: Function to run for each model (model_name, model_class) -> None
        class_name: Name for the generated test class
        include_mocks: Whether to include mock models in testing
        base_classes: Additional base classes for the test class
        setup_method: Optional setup method for each test
        teardown_method: Optional teardown method for each test

    Returns:
        Generated test class
    """
    # Get all models for testing
    all_models = get_all_models_for_testing(include_mocks)

    # Create the test class dictionary
    class_dict = {}

    # Add setup and teardown methods if provided
    if setup_method:
        class_dict["setup_method"] = setup_method
    if teardown_method:
        class_dict["teardown_method"] = teardown_method

    # Create the parameterized test method
    @pytest.mark.parametrize("model_name,model_class", sorted(all_models))
    def test_matrix_function(self, model_name, model_class):
        """Generated matrix test method."""
        return test_function(self, model_name, model_class)

    class_dict["test_matrix_function"] = test_matrix_function

    # Create the class
    return type(class_name, base_classes, class_dict)


class AbstractPydanticTestMixin:
    """
    Mixin class that provides common functionality for Pydantic model testing.
    """

    @classmethod
    def setup_test_database(cls, echo: bool = False):
        """Set up a test database and base model."""
        cls.engine = create_engine("sqlite:///:memory:", echo=echo)
        cls.TestBase = declarative_base()
        cls.Session = sessionmaker(bind=cls.engine)
        return cls.engine, cls.TestBase, cls.Session

    def setup_test_session(self):
        """Set up a test session and clear registries."""
        clear_registry_cache()
        if hasattr(self, "Session"):
            self.session = self.Session()

        # Clear the SQLAlchemy registry to avoid conflicts between tests
        if hasattr(self, "TestBase") and hasattr(self.TestBase, "registry"):
            self.TestBase.registry._class_registry.clear()

        return getattr(self, "session", None)

    def teardown_test_session(self):
        """Clean up test session and drop tables."""
        if hasattr(self, "session"):
            self.session.close()
        if hasattr(self, "TestBase") and hasattr(self, "engine"):
            self.TestBase.metadata.drop_all(self.engine)

    def assert_model_has_tablename(
        self, model_class, expected_pattern: Optional[str] | None = None
    ):
        """Assert that a SQLAlchemy model has a proper tablename."""
        assert hasattr(
            model_class, "__tablename__"
        ), f"{model_class} should have __tablename__"
        if expected_pattern:
            assert (
                expected_pattern in model_class.__tablename__
            ), f"Table name should match pattern {expected_pattern}"

    def assert_model_has_table(self, model_class):
        """Assert that a SQLAlchemy model has a proper table."""
        assert hasattr(model_class, "__table__"), f"{model_class} should have __table__"

    def assert_table_has_columns(self, table, expected_columns: List[str]):
        """Assert that a table has the expected columns."""
        column_names = [col.name for col in table.columns]
        for col in expected_columns:
            assert col in column_names, f"Column {col} should exist in table"

    def assert_table_has_foreign_keys(self, table, expected_fks: List[str]):
        """Assert that a table has the expected foreign key columns."""
        fk_columns = [col for col in table.columns if col.foreign_keys]
        fk_names = [col.name for col in fk_columns]
        for fk in expected_fks:
            assert fk in fk_names, f"Foreign key {fk} should exist in table"

    def get_model_analysis(self, model_class: Type[BaseModel]) -> Dict[str, Any]:
        """
        Analyze a Pydantic model and return structural information.

        Args:
            model_class: Pydantic model class to analyze

        Returns:
            Dictionary with analysis results
        """
        from typing import get_type_hints

        analysis = {
            "fields": [],
            "reference_fields": [],
            "mixins": [],
            "has_reference_id": False,
            "has_optional_refs": False,
            "field_types": {},
            "nullable_fields": [],
            "required_fields": [],
        }

        # Analyze fields
        try:
            type_hints = get_type_hints(model_class)
            analysis["fields"] = list(type_hints.keys())
            analysis["field_types"] = {
                name: str(field_type) for name, field_type in type_hints.items()
            }

            # Determine required vs optional fields
            for field_name, field_type in type_hints.items():
                if hasattr(field_type, "__origin__") and field_type.__origin__ is Union:
                    # Check if it's Optional (Union with None)
                    args = getattr(field_type, "__args__", ())
                    if type(None) in args:
                        analysis["nullable_fields"].append(field_name)
                    else:
                        analysis["required_fields"].append(field_name)
                else:
                    analysis["required_fields"].append(field_name)

        except Exception as e:
            logger.debug(
                f"Warning: Could not get type hints for {model_class.__name__}: {e}"
            )

        # Analyze mixins
        if hasattr(model_class, "__bases__"):
            for base in model_class.__bases__:
                base_name = base.__name__
                if "Mixin" in base_name:
                    analysis["mixins"].append(base_name)

        # Analyze Reference.ID structure
        if hasattr(model_class, "Reference") and hasattr(model_class.Reference, "ID"):
            analysis["has_reference_id"] = True
            ref_class = model_class.Reference.ID
            try:
                ref_fields = get_type_hints(ref_class)
                analysis["reference_fields"] = list(ref_fields.keys())
            except Exception as e:
                logger.debug(
                    f"Warning: Could not analyze ReferenceID for {model_class.__name__}: {e}"
                )

            # Check for optional references
            if hasattr(ref_class, "Optional"):
                analysis["has_optional_refs"] = True

        return analysis


def create_standard_matrix_tests(
    base_test_class: Type,
    test_functions: Dict[str, Callable],
    include_mocks: bool = True,
) -> Dict[str, Type]:
    """
    Create multiple matrix test classes for different test functions.

    Args:
        base_test_class: Base class for the generated test classes
        test_functions: Dictionary of test_name -> test_function
        include_mocks: Whether to include mock models

    Returns:
        Dictionary of test_name -> generated_test_class
    """
    generated_classes = {}

    for test_name, test_function in test_functions.items():
        class_name = f"Matrix{stringcase.pascalcase(test_name)}"

        # Create setup and teardown methods if the base class has them
        setup_method = None
        teardown_method = None

        if hasattr(base_test_class, "setup_method"):
            setup_method = base_test_class.setup_method
        elif hasattr(base_test_class, "setUp"):
            setup_method = lambda self: base_test_class.setUp(self)

        if hasattr(base_test_class, "teardown_method"):
            teardown_method = base_test_class.teardown_method
        elif hasattr(base_test_class, "tearDown"):
            teardown_method = lambda self: base_test_class.tearDown(self)

        generated_class = create_matrix_test_class(
            test_function=test_function,
            class_name=class_name,
            include_mocks=include_mocks,
            base_classes=(base_test_class,),
            setup_method=setup_method,
            teardown_method=teardown_method,
        )

        generated_classes[test_name] = generated_class

    return generated_classes


# Convenience function for quick matrix test generation
def matrix_test(include_mocks: bool = True):
    """
    Decorator to convert a test function into a matrix test that runs for all discovered models.

    Args:
        include_mocks: Whether to include mock models in testing

    Usage:
        @matrix_test()
        def test_model_scaffolding(self, model_name, model_class):
            # Test logic here
            pass
    """

    def decorator(test_function):
        all_models = get_all_models_for_testing(include_mocks)
        return pytest.mark.parametrize("model_name,model_class", all_models)(
            test_function
        )

    return decorator


def get_managers_by_domain_for_testing(
    include_mocks: bool = True, force_refresh: bool = False
) -> Dict[str, List[tuple]]:
    """
    Get all managers for testing organized by domain.

    Args:
        include_mocks: Whether to include mock managers (in "" domain)
        force_refresh: Whether to force a refresh of the manager cache

    Returns:
        Dict mapping domain names to lists of (manager_name, manager_class) tuples.
        Mock managers are stored under the "" key.
    """
    # Ensure discovery is completed
    if not force_refresh:
        _ensure_discovery_completed()

    managers_by_domain = discover_bll_managers_for_testing(force_refresh)

    if not include_mocks:
        # Remove mock managers (empty string domain)
        return {
            domain: managers
            for domain, managers in managers_by_domain.items()
            if domain != ""
        }

    return managers_by_domain


def get_all_managers_for_testing(
    include_mocks: bool = True, force_refresh: bool = False
) -> List[tuple]:
    """
    Get all managers for testing (real BLL managers + mock managers) flattened into a list.

    Args:
        include_mocks: Whether to include mock managers
        force_refresh: Whether to force a refresh of the manager cache

    Returns:
        List of (manager_name, manager_class) tuples
    """
    # Ensure discovery is completed
    if not force_refresh:
        _ensure_discovery_completed()

    managers_by_domain = discover_bll_managers_for_testing(force_refresh)

    # Flatten all managers from all domains into a single list
    all_managers = []
    mock_count = 0
    real_count = 0

    for domain, managers in managers_by_domain.items():
        if domain == "":  # Mock managers
            if include_mocks:
                all_managers.extend(managers)
                mock_count = len(managers)
        else:  # Real BLL managers
            all_managers.extend(managers)
            real_count += len(managers)

    if include_mocks:
        logger.debug(
            f"Total managers for testing: {len(all_managers)} (Real: {real_count}, Mock: {mock_count})"
        )
    else:
        logger.debug(
            f"Total managers for testing: {len(all_managers)} (Real BLL managers only)"
        )

    return sorted(all_managers)


def test_domain_organization():
    """
    Test function to demonstrate the new domain-based organization.
    This can be called to verify that models and managers are properly organized by domain.
    """
    logger.debug("\n=== Testing Domain-Based Organization ===")

    # Ensure discovery is completed
    _ensure_discovery_completed()

    # Show cache statistics
    print_discovery_cache_stats()

    # Test models by domain
    logger.debug("\n--- Models by Domain ---")
    models_by_domain = get_models_by_domain_for_testing()
    for domain, models in models_by_domain.items():
        domain_name = f'"{domain}"' if domain == "" else domain
        logger.debug(f"Domain {domain_name}: {len(models)} models")
        for model_name, model_class in models:
            logger.debug(
                f"  - {model_name}: {model_class.__module__}.{model_class.__name__}"
            )

    # Test managers by domain
    logger.debug("\n--- Managers by Domain ---")
    managers_by_domain = get_managers_by_domain_for_testing()
    for domain, managers in managers_by_domain.items():
        domain_name = f'"{domain}"' if domain == "" else domain
        logger.debug(f"Domain {domain_name}: {len(managers)} managers")
        for manager_name, manager_class in managers:
            logger.debug(
                f"  - {manager_name}: {manager_class.__module__}.{manager_class.__name__}"
            )

    # Test flattened lists for backward compatibility
    logger.debug("\n--- Flattened Lists (Backward Compatibility) ---")
    all_models = get_all_models_for_testing()
    all_managers = get_all_managers_for_testing()
    logger.debug(f"Total models (flattened): {len(all_models)}")
    logger.debug(f"Total managers (flattened): {len(all_managers)}")

    return models_by_domain, managers_by_domain


def initialize_discovery_cache():
    """
    Initialize the discovery cache. This can be called at module import time
    or at the start of test sessions to pre-populate the cache.
    """
    logger.debug("Initializing BLL discovery cache...")
    _ensure_discovery_completed()
    logger.debug("BLL discovery cache initialization complete.")


# Initialize the cache when the module is imported (optional - can be disabled if needed)
# This ensures the cache is populated once per Python session
if os.environ.get("TESTING") == "true":
    # Only auto-initialize during testing to avoid slowing down normal imports
    try:
        initialize_discovery_cache()
    except Exception as e:
        logger.debug(
            f"Warning: Failed to initialize discovery cache during import: {e}"
        )
        logger.debug("Cache will be initialized on first use instead.")


def get_discovery_cache_stats():
    """
    Get statistics about the current discovery cache.

    Returns:
        Dict with cache statistics
    """
    global _DISCOVERED_MODELS_CACHE, _DISCOVERED_MANAGERS_CACHE, _DISCOVERY_COMPLETED

    stats = {
        "discovery_completed": _DISCOVERY_COMPLETED,
        "models_cached": _DISCOVERED_MODELS_CACHE is not None,
        "managers_cached": _DISCOVERED_MANAGERS_CACHE is not None,
        "total_models": 0,
        "total_managers": 0,
        "model_domains": [],
        "manager_domains": [],
        "real_models": 0,
        "mock_models": 0,
        "real_managers": 0,
        "mock_managers": 0,
    }

    if _DISCOVERED_MODELS_CACHE:
        stats["total_models"] = sum(
            len(models) for models in _DISCOVERED_MODELS_CACHE.values()
        )
        stats["model_domains"] = list(_DISCOVERED_MODELS_CACHE.keys())
        stats["mock_models"] = len(_DISCOVERED_MODELS_CACHE.get("", []))
        stats["real_models"] = stats["total_models"] - stats["mock_models"]

    if _DISCOVERED_MANAGERS_CACHE:
        stats["total_managers"] = sum(
            len(managers) for managers in _DISCOVERED_MANAGERS_CACHE.values()
        )
        stats["manager_domains"] = list(_DISCOVERED_MANAGERS_CACHE.keys())
        stats["mock_managers"] = len(_DISCOVERED_MANAGERS_CACHE.get("", []))
        stats["real_managers"] = stats["total_managers"] - stats["mock_managers"]

    return stats


def print_discovery_cache_stats():
    """
    Print detailed statistics about the discovery cache.
    """
    stats = get_discovery_cache_stats()

    logger.debug("\n=== BLL Discovery Cache Statistics ===")
    logger.debug(f"Discovery Completed: {stats['discovery_completed']}")
    logger.debug(f"Models Cached: {stats['models_cached']}")
    logger.debug(f"Managers Cached: {stats['managers_cached']}")

    if stats["models_cached"]:
        logger.debug(f"\nModels:")
        logger.debug(f"  Total: {stats['total_models']}")
        logger.debug(f"  Real BLL Models: {stats['real_models']}")
        logger.debug(f"  Mock Models: {stats['mock_models']}")
        logger.debug(f"  Domains: {stats['model_domains']}")

    if stats["managers_cached"]:
        logger.debug(f"\nManagers:")
        logger.debug(f"  Total: {stats['total_managers']}")
        logger.debug(f"  Real BLL Managers: {stats['real_managers']}")
        logger.debug(f"  Mock Managers: {stats['mock_managers']}")
        logger.debug(f"  Domains: {stats['manager_domains']}")

    logger.debug("=" * 40)


def is_discovery_cached():
    """
    Check if discovery has been completed and cached.

    Returns:
        bool: True if discovery is cached and completed
    """
    global _DISCOVERY_COMPLETED, _DISCOVERED_MODELS_CACHE, _DISCOVERED_MANAGERS_CACHE

    return (
        _DISCOVERY_COMPLETED
        and _DISCOVERED_MODELS_CACHE is not None
        and _DISCOVERED_MANAGERS_CACHE is not None
    )


def force_refresh_discovery():
    """
    Force a complete refresh of the discovery cache.
    This will clear the cache and re-discover all models and managers.
    """
    logger.debug("Forcing complete refresh of discovery cache...")
    clear_discovery_cache()
    _ensure_discovery_completed()
    logger.debug("Discovery cache refresh completed.")


if __name__ == "__main__":
    # Run the test when the module is executed directly
    test_domain_organization()
