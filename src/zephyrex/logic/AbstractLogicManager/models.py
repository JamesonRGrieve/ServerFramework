import stringcase
import sys
from datetime import date, datetime
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
)

from fastapi.encoders import ENCODERS_BY_TYPE
from pydantic import BaseModel, ConfigDict, Field

from zephyrex.pydantic2.registry import BaseNetworkModel
from zephyrex.pydantic2.sqlalchemy import DatabaseMixin


class NumericalSearchModel(BaseModel):
    lt: Optional[Any] | None = None
    gt: Optional[Any] | None = None
    lteq: Optional[Any] | None = None
    gteq: Optional[Any] | None = None
    neq: Optional[Any] | None = None
    eq: Optional[Any] | None = None


class StringSearchModel(BaseModel):
    inc: Optional[str] | None = None
    sw: Optional[str] | None = None
    ew: Optional[str] | None = None
    eq: Optional[str] | None = None


class DateSearchModel(BaseModel):
    before: Optional[datetime] | None = None
    after: Optional[datetime] | None = None
    on: Optional[date] | None = None
    eq: Optional[datetime] | None = None


class BooleanSearchModel(BaseModel):
    eq: Optional[bool] | None = None


from pydantic._internal._model_construction import ModelMetaclass


class ModelMeta(ModelMetaclass):
    """Metaclass that generates .Reference and .Network nested classes for models."""

    def __getattr__(cls, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        try:
            model_fields = type.__getattribute__(cls, "model_fields")
        except AttributeError:
            model_fields = None
        if model_fields and item in model_fields:
            if cls._is_schema_generation_context():
                raise AttributeError(item)
            return ModelFieldAccessor(cls, item)  # type: ignore[arg-type]
        raise AttributeError(item)

    @staticmethod
    def _is_schema_generation_context() -> bool:
        frame = sys._getframe(1)
        depth = 0
        while frame and depth < 20:
            module_name = frame.f_globals.get("__name__", "")
            if module_name.startswith("pydantic") or module_name.startswith(
                "fastapi.openapi"
            ):
                return True
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1
        return False

    def __new__(mcs, name, bases, namespace, **kwargs):
        # Collect annotations from non-BaseModel mixins so Pydantic sees inherited fields
        namespace_annotations = dict(namespace.get("__annotations__", {}))
        merged_annotations = {}
        visited_mixins: set[type] = set()

        def _should_skip(cls: type) -> bool:
            try:
                return issubclass(cls, BaseModel)
            except TypeError:
                return False

        ignored_types: set[type] = set()

        for base in reversed(bases):
            if not isinstance(base, type) or _should_skip(base):
                continue

            ignored_types.add(base)

            for ancestor in reversed(base.__mro__[:-1]):  # Exclude `object`
                if not isinstance(ancestor, type) or _should_skip(ancestor):
                    continue
                if ancestor in visited_mixins:
                    continue

                visited_mixins.add(ancestor)
                ignored_types.add(ancestor)
                base_annotations = getattr(ancestor, "__annotations__", {})
                for field_name, field_type in base_annotations.items():
                    if field_name in namespace_annotations or field_name in namespace:
                        continue
                    merged_annotations[field_name] = field_type
                    namespace_annotations[field_name] = field_type

        if "__annotations__" not in namespace:
            namespace["__annotations__"] = {}

        if merged_annotations:
            namespace["__annotations__"].update(merged_annotations)

        if ignored_types:
            config_data: Dict[str, Any] = {}
            existing_config = namespace.get("model_config")
            if existing_config:
                if isinstance(existing_config, dict):
                    config_data.update(existing_config)
                else:
                    items = getattr(existing_config, "items", None)
                    if callable(items):
                        config_data.update(dict(items()))
                    else:
                        try:
                            config_data.update(dict(existing_config))
                        except TypeError:
                            pass

            existing_ignored = config_data.get("ignored_types", ())
            if isinstance(existing_ignored, (list, set, tuple)):
                ignored_types.update(existing_ignored)
            elif existing_ignored:
                ignored_types.add(existing_ignored)

            config_data["ignored_types"] = tuple(dict.fromkeys(ignored_types))
            namespace["model_config"] = ConfigDict(**config_data)

        # Add a custom model_serializer to ensure mixin fields are included in serialization
        def model_serializer(self, serializer, info):
            """Custom serializer to ensure all fields including mixin fields are serialized"""
            # Use the default serializer first
            result = serializer(self)

            # Check for any missing mixin fields that should be included
            model_fields = getattr(self.__class__, "model_fields", {})
            for field_name in model_fields:
                if field_name not in result and hasattr(self, field_name):
                    value = getattr(self, field_name)
                    result[field_name] = value

            return result

        # Only add the serializer if this is a BaseModel subclass
        if any(issubclass(base, BaseModel) for base in bases if isinstance(base, type)):
            # Add the necessary import for the decorator
            from pydantic import model_serializer as pydantic_model_serializer

            namespace["model_serializer"] = pydantic_model_serializer(mode="wrap")(
                model_serializer
            )

        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # Only generate for actual model classes (not base classes)
        # Check if this has DatabaseMixin anywhere in its inheritance chain
        has_database_mixin = False

        # Import here to avoid circular imports
        try:
            from zephyrex.pydantic2.sqlalchemy import DatabaseMixin

            # Check the full MRO (Method Resolution Order) for DatabaseMixin
            for base_class in cls.__mro__:
                if base_class is DatabaseMixin:
                    has_database_mixin = True
                    break
        except ImportError:
            # Fallback to string-based detection if import fails
            for base in bases:
                if hasattr(base, "__name__") and "DatabaseMixin" in base.__name__:
                    has_database_mixin = True
                    break
            if not has_database_mixin and "DatabaseMixin" in str(bases):
                has_database_mixin = True

        if (
            has_database_mixin
            and not name.startswith("Base")
            and not name.startswith("Application")
        ):
            # Generate Reference class with ID
            cls.Reference = mcs._create_reference_class(cls, name)

        return cls

    @staticmethod
    def _create_reference_class(model_cls, model_name):
        """Creates Reference class with dynamically generated ID class"""
        # Strip the 'Model' suffix while preserving CamelCase boundaries so
        # `snakecase` can insert the underscores. The previous form
        # lowercased first, which destroyed the boundaries and produced
        # field names like `iteminstance_id` for `ItemInstanceModel` while
        # the table name generator emitted `item_instances` — leaving the
        # FK column name out of sync with the parent table. Single-word
        # models are unaffected (`UserModel` → `User` → `user`).
        if model_name.endswith("Model"):
            field_name = model_name[:-5]
        else:
            field_name = model_name
        field_name = stringcase.snakecase(field_name)
        id_field_name = f"{field_name}_id"

        # Create ID class with the appropriate field
        class ID:
            pass

        ID.__annotations__ = {
            id_field_name: Annotated[
                str, Field(..., description=f"The ID of the related {field_name}")
            ]
        }

        # Create Optional subclass for ID
        class IDOptional:
            pass

        IDOptional.__annotations__ = {
            id_field_name: Annotated[
                Optional[str],
                Field(default=None, description=f"The ID of the related {field_name}"),
            ]
        }

        # Create Search subclass for ID
        class IDSearch:
            pass

        IDSearch.__annotations__ = {
            id_field_name: Annotated[
                Optional[StringSearchModel],
                Field(default=None, description=f"Search filter for {id_field_name}"),
            ]
        }

        # Attach Optional and Search to ID
        ID.Optional = IDOptional
        ID.Search = IDSearch

        # Create Reference class that includes the model field
        class Reference(ID):
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)

        # Add the model field dynamically to the Reference class
        Reference.__annotations__ = getattr(Reference, "__annotations__", {})
        Reference.__annotations__[field_name] = Annotated[
            Optional[model_cls],
            Field(default=None, description=f"The related {field_name}"),
        ]

        # Create Optional subclass for Reference
        class ReferenceOptional(IDOptional):
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)

        # Add the model field to Optional as well
        ReferenceOptional.__annotations__ = getattr(
            ReferenceOptional, "__annotations__", {}
        )
        ReferenceOptional.__annotations__[field_name] = Annotated[
            Optional[model_cls],
            Field(default=None, description=f"The related {field_name}"),
        ]

        # Attach ID and Optional to Reference
        Reference.ID = ID
        Reference.Optional = ReferenceOptional

        return Reference


class FieldComparison:
    """Represents a deferred comparison for later SQLAlchemy translation."""

    def __init__(
        self, model_cls: Type[BaseModel], field_name: str, operator: str, value: Any
    ):
        self.model_cls = model_cls
        self.field_name = field_name
        self.operator = operator
        self.value = value


class ModelFieldAccessor:
    """Provides comparison operators for class-level field access."""

    def __init__(self, model_cls: Type[BaseModel], field_name: str):
        self.model_cls = model_cls
        self.field_name = field_name

    def _comparison(self, operator: str, value: Any) -> FieldComparison:
        return FieldComparison(self.model_cls, self.field_name, operator, value)

    def __eq__(self, other: Any) -> FieldComparison:  # type: ignore[override]
        return self._comparison("eq", other)

    def __ne__(self, other: Any) -> FieldComparison:  # type: ignore[override]
        return self._comparison("ne", other)

    def __lt__(self, other: Any) -> FieldComparison:
        return self._comparison("lt", other)

    def __le__(self, other: Any) -> FieldComparison:
        return self._comparison("le", other)

    def __gt__(self, other: Any) -> FieldComparison:
        return self._comparison("gt", other)

    def __ge__(self, other: Any) -> FieldComparison:
        return self._comparison("ge", other)

    def in_(self, values: Any) -> FieldComparison:
        return self._comparison("in", values)

    def not_in(self, values: Any) -> FieldComparison:
        return self._comparison("not_in", values)

    def like(self, pattern: str) -> FieldComparison:
        return self._comparison("like", pattern)

    def ilike(self, pattern: str) -> FieldComparison:
        return self._comparison("ilike", pattern)

    def contains(self, value: Any) -> FieldComparison:
        return self._comparison("contains", value)

    def startswith(self, value: str) -> FieldComparison:
        return self._comparison("startswith", value)

    def endswith(self, value: str) -> FieldComparison:
        return self._comparison("endswith", value)

    def is_(self, other: Any) -> FieldComparison:
        return self._comparison("is", other)

    def isnot(self, other: Any) -> FieldComparison:
        return self._comparison("isnot", other)

    def __repr__(self) -> str:
        return f"{self.model_cls.__name__}.{self.field_name}"


ENCODERS_BY_TYPE.setdefault(
    ModelFieldAccessor,
    lambda value: {
        "model": value.model_cls.__name__,
        "field": value.field_name,
    },
)


class ApplicationModel(BaseModel, DatabaseMixin, metaclass=ModelMeta):
    """Base mixin for all models with common audit fields."""

    id: str = Field(..., description="The unique identifier")
    created_at: datetime = Field(
        ..., description="The time and date at which this was created"
    )
    created_by_user_id: str = Field(
        ..., description="The ID of the user who performed the creation"
    )

    class Optional(BaseModel, DatabaseMixin, metaclass=ModelMeta):
        id: Optional[str] | None = None
        created_at: Optional[datetime] | None = None
        created_by_user_id: Optional[str] | None = None

    class Search(BaseModel):
        id: Optional[StringSearchModel] | None = None
        created_at: Optional[DateSearchModel] | None = None
        created_by_user_id: Optional[StringSearchModel] | None = None

    # ReferenceID classes to enable automatic Reference and Network generation
    class ReferenceID(BaseModel):
        """Base class for reference models with just the ID field."""

        id: str = Field(..., description="The unique identifier")

        class Optional(BaseModel):
            id: Optional[str] | None = None


class UpdateMixinModel:
    updated_at: Annotated[
        Optional[datetime],
        Field(description="The time and date at which this was last updated"),
    ]
    updated_by_user_id: Annotated[
        Optional[str], Field(description="The ID of the user who made the last update")
    ]

    class Optional:
        updated_at: Annotated[Optional[datetime], Field(default=None)]
        updated_by_user_id: Annotated[Optional[str], Field(default=None)]

    class Search:
        updated_at: Optional[DateSearchModel] | None = None
        updated_by_user_id: Optional[StringSearchModel] | None = None


class ParentMixinModel:
    parent_id: Annotated[
        Optional[str], Field(description="The ID of the relevant parent")
    ]

    class Optional:
        parent_id: Annotated[Optional[str], Field(default=None)]
        parent: Annotated[Optional[Any], Field(default=None)]
        children: Annotated[
            Optional[List[Any]],
            Field(default_factory=list),
        ]

    class Search:
        parent_id: Optional[StringSearchModel] | None = None


class NameMixinModel:
    name: Annotated[str, Field(description="The name")]

    class Optional:
        name: Annotated[Optional[str], Field(default=None)]

    class Search:
        name: Optional[StringSearchModel] | None = None


class DescriptionMixinModel:
    description: Annotated[str, Field(description="The description")]

    class Optional:
        description: Annotated[Optional[str], Field(default=None)]

    class Search:
        description: Optional[StringSearchModel] | None = None


class ImageMixinModel:
    image_url: Annotated[str, Field(description="The path to the image")]

    class Optional:
        image_url: Annotated[Optional[str], Field(default=None)]

    class Search:
        image_url: Optional[StringSearchModel] | None = None


class TemplateModel(ApplicationModel, NameMixinModel):
    class Create(BaseModel):
        pass

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        pass


class TemplateReferenceModel(ApplicationModel):
    template_id: Optional[str] | None = None
    template: Optional[TemplateModel] | None = None


class TemplateNetworkModel(BaseModel):
    class GET(BaseNetworkModel):
        pass

    class LIST(BaseNetworkModel):
        offset: int = Field(0, ge=0)
        limit: int = Field(1000, ge=1, le=1000)
        sort_by: Optional[str] | None = None
        sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$")

    class POST(ApplicationModel):
        template: TemplateModel.Create

    class PUT(ApplicationModel):
        template: TemplateModel.Update

    class SEARCH(ApplicationModel):
        template: TemplateModel.Search

    class ResponseSingle(ApplicationModel):
        template: TemplateModel

    class ResponsePlural(ApplicationModel):
        templates: List[TemplateModel]


DtoT = TypeVar("DtoT")


class BatchUpdateItem(BaseModel):
    """Model for a single item in a batch update operation.

    This should be kept in sync with BatchUpdateItemModel in AbstractEPRouter.py
    """

    id: str
    data: Dict[str, Any]


class IDModel(ApplicationModel):
    """Model for ID-based operations."""


def gen_not_found_msg(classname):
    return f"Request searched {classname} and could not find the required record."
