from zephyrex.database.AbstractDatabaseEntity import (
    BaseMixin as BaseMixin,
    ImageMixin as ImageMixin,
    UpdateMixin as UpdateMixin,
)
from zephyrex.lib.AbstractPydantic2 import (
    default_name_processor as default_name_processor,
)
from zephyrex.lib.Environment import inflection as inflection
from zephyrex.lib.Logging import logger as logger
from zephyrex.pydantic2.registry import ModelRegistry as ModelRegistry
from zephyrex.pydantic2.sqlalchemy._const import (
    DatabaseManagerType as DatabaseManagerType,
    RESERVED_SQLALCHEMY_NAMES as RESERVED_SQLALCHEMY_NAMES,
    SQLAlchemyModelType as SQLAlchemyModelType,
    T as T,
    TABLENAME_REGEX as TABLENAME_REGEX,
    TYPE_MAPPING as TYPE_MAPPING,
    inflect_engine as inflect_engine,
)
from zephyrex.pydantic2.sqlalchemy.mixins import (
    ApplicationModel as ApplicationModel,
    DatabaseMixin as DatabaseMixin,
    ImageMixinModel as ImageMixinModel,
    ModelConverter as ModelConverter,
    ParentMixinModel as ParentMixinModel,
    ParentRelationshipMixin as ParentRelationshipMixin,
    StringSearchModel as StringSearchModel,
    UpdateMixinModel as UpdateMixinModel,
)
from zephyrex.pydantic2.sqlalchemy.builder import (
    clear_registry_cache as clear_registry_cache,
    create_sqlalchemy_model as create_sqlalchemy_model,
    get_entity_module_class as get_entity_module_class,
    get_relationship_target as get_relationship_target,
    get_scaffolded_model as get_scaffolded_model,
    list_scaffolded_models as list_scaffolded_models,
    _analyze_model_dependencies as _analyze_model_dependencies,
    _apply_pending_relationships as _apply_pending_relationships,
    _create_column_from_field as _create_column_from_field,
    _ensure_pending_relationship_targets as _ensure_pending_relationship_targets,
    _ensure_reference_foreign_keys as _ensure_reference_foreign_keys,
    _extract_mixin_classes as _extract_mixin_classes,
    _find_pydantic_model_by_name as _find_pydantic_model_by_name,
    _fix_null_type_columns as _fix_null_type_columns,
    _get_db_manager_from_context as _get_db_manager_from_context,
    _get_existing_columns as _get_existing_columns,
    _is_database_model as _is_database_model,
    _process_reference_fields as _process_reference_fields,
    _queue_pending_relationship as _queue_pending_relationship,
    _resolve_sqlalchemy_model as _resolve_sqlalchemy_model,
    _sanitize_field_name as _sanitize_field_name,
)
from zephyrex.pydantic2.sqlalchemy.extensions import (
    RemoveField as RemoveField,
    extension_model as extension_model,
    get_applied_extensions as get_applied_extensions,
    prepare_test_registry as prepare_test_registry,
    reset_extension_system as reset_extension_system,
    _EXTENSION_REGISTRY_COMPAT as _EXTENSION_REGISTRY_COMPAT,
    _MODEL_SNAPSHOTS as _MODEL_SNAPSHOTS,
    _apply_model_extension as _apply_model_extension,
    _apply_nested_model_extensions as _apply_nested_model_extensions,
    _clear_model_cache as _clear_model_cache,
    _undo_model_extension as _undo_model_extension,
)
