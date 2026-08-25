from datetime import datetime
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, Field
from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from zephyrex.lib.Logging import logger


# Search model for string fields
class StringSearchModel(BaseModel):
    contains: Optional[str] | None = None
    equals: Optional[str] | None = None
    starts_with: Optional[str] | None = None
    ends_with: Optional[str] | None = None
    in_list: Optional[List[str]] | None = None


# Common Pydantic model mixins that match the SQLAlchemy mixins
class ApplicationModel(BaseModel):
    id: Optional[str] = Field(None, description="Unique identifier")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    created_by_user_id: Optional[str] = Field(
        None, description="ID of the user who created this record"
    )

    class Optional(BaseModel):
        id: Optional[str] | None = None
        created_at: Optional[datetime] | None = None
        created_by_user_id: Optional[str] | None = None

    class Search(BaseModel):
        id: Optional[StringSearchModel] | None = None
        created_at: Optional[datetime] | None = None
        created_by_user_id: Optional[StringSearchModel] | None = None


class UpdateMixinModel(BaseModel):
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by_user_id: Optional[str] = Field(
        None, description="ID of the user who last updated this record"
    )

    class Optional(BaseModel):
        updated_at: Optional[datetime] | None = None
        updated_by_user_id: Optional[str] | None = None

    class Search(BaseModel):
        updated_at: Optional[datetime] | None = None
        updated_by_user_id: Optional[StringSearchModel] | None = None


class ImageMixinModel(BaseModel):
    image_url: Optional[str] = Field(
        None, description="URL to the image for this record"
    )

    class Optional(BaseModel):
        image_url: Optional[str] = Field(None, description="Optional image URL")

    class Search(BaseModel):
        image_url: Optional[StringSearchModel] | None = None


class ParentMixinModel(BaseModel):
    parent_id: Optional[str] = Field(None, description="ID of the parent record")

    class Optional(BaseModel):
        parent_id: Optional[str] | None = None

    class Search(BaseModel):
        parent_id: Optional[StringSearchModel] | None = None


# Fix circular imports and handle parent relationship properly
class ParentRelationshipMixin:
    """Modified version of ParentMixin that sets up the self-reference correctly"""

    @declared_attr
    def parent_id(cls):
        return Column(
            String,
            ForeignKey(f"{cls.__tablename__}.id"),
            nullable=True,
            comment="ID of the parent record",
        )

    @declared_attr
    def parent(cls):
        return relationship(
            cls,
            remote_side=[cls.id],
            backref="children",
            primaryjoin=lambda: cls.id == cls.parent_id,
        )


# Legacy compatibility class
class ModelConverter:
    """
    Legacy utility class for backward compatibility.
    """

    @staticmethod
    def create_sqlalchemy_model(model, **kwargs):
        """Legacy method - use create_sqlalchemy_model function instead."""
        from zephyrex.pydantic2.registry import ModelRegistry
        from zephyrex.pydantic2.sqlalchemy.builder import create_sqlalchemy_model

        registry = ModelRegistry()
        return create_sqlalchemy_model(model, registry, **kwargs)

    @staticmethod
    def pydantic_to_dict(pydantic_obj: BaseModel) -> Dict[str, Any]:
        """
        Convert a Pydantic model instance to a dictionary suitable for SQLAlchemy.
        Removes any fields that don't belong in the SQLAlchemy model.

        Args:
            pydantic_obj: Pydantic model instance

        Returns:
            Dictionary with only the valid SQLAlchemy fields
        """
        # Handle both Pydantic v1 and v2
        try:
            if hasattr(pydantic_obj, "model_dump"):
                # Pydantic v2
                data = pydantic_obj.model_dump(exclude_unset=True)
            elif hasattr(pydantic_obj, "dict"):
                # Pydantic v1
                data = pydantic_obj.dict(exclude_unset=True)
            else:
                # Fallback
                data = {
                    k: v
                    for k, v in pydantic_obj.__dict__.items()
                    if not k.startswith("_")
                }
        except Exception as e:
            # Fallback if the methods fail
            data = {
                k: v for k, v in pydantic_obj.__dict__.items() if not k.startswith("_")
            }

        # Remove any fields that shouldn't be passed to SQLAlchemy
        # (like nested models or computed fields)
        keys_to_remove = []
        for key, value in data.items():
            if isinstance(value, BaseModel):
                keys_to_remove.append(key)
            elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del data[key]

        # Filter out PydanticUndefined values
        try:
            from pydantic_core import PydanticUndefined
        except ImportError:
            try:
                from pydantic.fields import PydanticUndefined
            except ImportError:
                PydanticUndefined = None  # type: ignore[assignment]

        if PydanticUndefined is not None:
            keys_to_remove = []
            for key, value in data.items():
                if value is PydanticUndefined:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del data[key]

        return data

    @staticmethod
    def sqlalchemy_to_pydantic(
        sa_obj: Any, pydantic_class: Type[BaseModel]
    ) -> BaseModel:
        """
        Convert a SQLAlchemy model instance to a Pydantic model instance.

        Args:
            sa_obj: SQLAlchemy model instance
            pydantic_class: Target Pydantic model class

        Returns:
            Pydantic model instance
        """
        # Convert SQLAlchemy model to dict
        if hasattr(sa_obj, "__dict__"):
            # Extract data from SQLAlchemy model
            data = {}
            for key, value in sa_obj.__dict__.items():
                if not key.startswith("_"):
                    data[key] = value

            # Get model fields and provide default values
            try:
                # Initialize missing optional fields with None
                for field_name, field_type in get_type_hints(pydantic_class).items():
                    if (
                        field_name not in data
                        and get_origin(field_type) is Union
                        and type(None) in get_args(field_type)
                    ):
                        data[field_name] = None

                # Create Pydantic model instance based on version
                try:
                    if hasattr(pydantic_class, "model_validate"):
                        # Pydantic v2
                        return pydantic_class.model_validate(data)
                    elif hasattr(pydantic_class, "parse_obj"):
                        # Pydantic v1
                        return pydantic_class.parse_obj(data)
                    else:
                        # Direct instantiation
                        return pydantic_class(**data)
                except Exception as e:
                    # If the above fails, try direct instantiation
                    return pydantic_class(**data)
            except Exception as e:
                raise ValueError(
                    f"Failed to convert SQLAlchemy object to Pydantic: {str(e)}"
                )
        else:
            # Handle the case where sa_obj is already a dict
            try:
                if hasattr(pydantic_class, "model_validate"):
                    # Pydantic v2
                    return pydantic_class.model_validate(sa_obj)
                elif hasattr(pydantic_class, "parse_obj"):
                    # Pydantic v1
                    return pydantic_class.parse_obj(sa_obj)
                else:
                    # Direct instantiation
                    return pydantic_class(**sa_obj)
            except Exception as e:
                # If the above fails, try direct instantiation
                try:
                    return pydantic_class(**sa_obj)
                except Exception as nested_e:
                    raise ValueError(
                        f"Failed to convert dict to Pydantic: {str(e)}, nested error: {str(nested_e)}"
                    )


class DatabaseMixin:
    """
    Mixin for Pydantic models that provides access to corresponding SQLAlchemy models.

    This mixin adds a `.DB(declarative_base)` method that returns the SQLAlchemy model class
    that corresponds to the Pydantic model for the given declarative base. The SQLAlchemy
    model is generated automatically and cached per declarative base.

    Example:
        class UserModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="User's name")
            email: str = Field(..., description="User's email")

        # Access the SQLAlchemy model for a specific declarative base
        # Note: In practice, get the db_manager from app.state.model_registry.database_manager or dependency injection
        db_manager = _get_db_manager_from_context()
        User = UserModel.DB(db_manager.Base)

        # Use it with SQLAlchemy
        with db_manager.get_session() as db:
            users = db.query(User).all()
    """

    @classmethod
    def DB(cls, declarative_base):
        """
        Get or create the SQLAlchemy model for this Pydantic model within the given declarative base.
        Automatically creates dependent models to ensure proper relationship resolution.

        Args:
            declarative_base: The SQLAlchemy declarative base to use

        Returns:
            The SQLAlchemy model class corresponding to this Pydantic model
        """
        from zephyrex.pydantic2.sqlalchemy.builder import (
            _get_db_manager_from_context,
            create_sqlalchemy_model,
        )

        if declarative_base is None:
            raise ValueError("declarative_base cannot be None")

        # Add debugging to help identify the issue
        logger.debug(
            f"DB method called for {cls.__name__} with declarative_base type: {type(declarative_base)}"
        )

        # Validate that declarative_base is a proper class/type, not a mock or proxy
        if not hasattr(declarative_base, "__name__") and not hasattr(
            declarative_base, "__class__"
        ):
            logger.error(
                f"Invalid declarative_base object: {declarative_base}, type: {type(declarative_base)}"
            )
            raise ValueError(
                f"declarative_base must be a valid SQLAlchemy declarative base class, got {type(declarative_base)}"
            )

        # Create a registry key based on the model and declarative base
        registry_key = f"{cls.__module__}.{cls.__name__}"

        # Check if we already have this model in the declarative base registry
        if hasattr(declarative_base, "_pydantic_models"):
            pydantic_models = getattr(declarative_base, "_pydantic_models", None)
            if isinstance(pydantic_models, dict) and registry_key in pydantic_models:
                return pydantic_models[registry_key]
            elif not isinstance(pydantic_models, dict):
                # Reset if it's not a proper dictionary
                declarative_base._pydantic_models = {}
        else:
            declarative_base._pydantic_models = {}

        # Get the model registry from the declarative base or database manager
        model_registry = None

        # First, try to get it from the declarative base if it has one attached
        if hasattr(declarative_base, "_model_registry"):
            model_registry = declarative_base._model_registry
        else:
            # Try to get it from the database manager
            try:
                # WARNING: This is deprecated singleton usage - use dependency injection in practice
                db_manager = _get_db_manager_from_context()
                if (
                    db_manager
                    and hasattr(db_manager, "Base")
                    and db_manager.Base == declarative_base
                ):
                    # Check if there's an app state with model registry
                    try:
                        # This is a fallback - in practice we should have the registry attached to the base
                        pass
                    except ImportError as e:
                        logger.debug(
                            "starlette unavailable while resolving "
                            "model_registry: %s",
                            e,
                        )
            except Exception as e:
                logger.debug(
                    "model_registry resolution from db_manager context failed: %s",
                    e,
                )

        # If we still don't have a model registry, we need to create one for this declarative base
        if model_registry is None:
            from zephyrex.pydantic2.registry import ModelRegistry

            model_registry = ModelRegistry()
            model_registry.declarative_base = declarative_base
            # Attach it to the declarative base for future use
            try:
                setattr(declarative_base, "_model_registry", model_registry)
            except (TypeError, AttributeError) as e:
                logger.warning(
                    f"Could not attach model registry to declarative_base: {e}"
                )
                # Continue without attaching - we still have the model_registry locally

        # Before creating this model, ensure all its dependencies are created first
        cls._ensure_dependencies_created(declarative_base)

        # Create the SQLAlchemy model using the proper model registry
        sqlalchemy_model = create_sqlalchemy_model(
            cls, model_registry, base_model=declarative_base
        )

        # Store it in the declarative base registry
        # Ensure _pydantic_models is a proper dictionary before storing
        try:
            if not hasattr(declarative_base, "_pydantic_models") or not isinstance(
                getattr(declarative_base, "_pydantic_models", None), dict
            ):
                setattr(declarative_base, "_pydantic_models", {})
            getattr(declarative_base, "_pydantic_models")[
                registry_key
            ] = sqlalchemy_model
        except (TypeError, AttributeError) as e:
            logger.warning(
                f"Could not store model in declarative_base registry due to {e}. Proceeding without caching."
            )
            # If we can't store in the registry, that's okay - we'll just return the model without caching

        return sqlalchemy_model

    @classmethod
    def _ensure_dependencies_created(cls, declarative_base):
        """
        Ensure all dependent models are created in the same declarative base.
        This prevents SQLAlchemy relationship resolution errors.
        """
        from typing import get_type_hints

        # Get all reference fields from the model's inheritance chain
        dependency_models = set()

        for base in cls.__bases__:
            # Skip basic types and our own mixins
            if (
                base.__name__ in ["BaseModel", "DatabaseMixin"]
                or "Mixin" in base.__name__
            ):
                continue

            # Check for ReferenceModel classes (like UserReferenceModel, TeamReferenceModel)
            if hasattr(base, "__qualname__") and base.__qualname__.endswith(
                "ReferenceModel"
            ):
                try:
                    ref_fields = get_type_hints(base)
                    for field_name, field_type in ref_fields.items():
                        if field_name.endswith("_id"):
                            entity_name = field_name.removesuffix("_id")
                            entity_class_name = f"{entity_name.title()}Model"

                            # Try to import and get the dependency model
                            try:
                                # Import from the same module as this model
                                module = __import__(
                                    cls.__module__, fromlist=[entity_class_name]
                                )
                                if hasattr(module, entity_class_name):
                                    dependency_model = getattr(
                                        module, entity_class_name
                                    )
                                    if hasattr(dependency_model, "DB") and hasattr(
                                        dependency_model, "model_fields"
                                    ):
                                        dependency_models.add(dependency_model)
                            except (ImportError, AttributeError):
                                # If we can't import the dependency, skip it
                                pass
                except Exception:
                    # If we can't get type hints, skip this base
                    pass

        # Create all dependency models first
        for dependency_model in dependency_models:
            try:
                dependency_model.DB(declarative_base)
            except Exception:
                # If creating a dependency fails, continue with others
                pass

    @classmethod
    def clear_db_cache(cls):
        """
        Clear any cached database models for this Pydantic model.
        Note: With the new approach, caching is per declarative base,
        so this method is mainly for compatibility.
        """
        # This method is now mainly for compatibility
        # The actual cache is stored in each declarative base
        pass

    @classmethod
    def get_db_model(cls, declarative_base) -> Type:
        """
        Alias for DB() method for backward compatibility.

        Args:
            declarative_base: The SQLAlchemy declarative base to use

        Returns:
            The SQLAlchemy model class
        """
        return cls.DB(declarative_base)  # type: ignore[no-any-return]
