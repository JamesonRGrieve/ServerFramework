from zephyrex.logic.AbstractLogicManager.hooks import (
    HookContext as HookContext,
    HookOrderingError as HookOrderingError,
    HookRegistry as HookRegistry,
    HookTiming as HookTiming,
    NON_BLOCKING_HOOK_FAILURES as NON_BLOCKING_HOOK_FAILURES,
    _P as _P,
    _R as _R,
    _emit_non_blocking_failure_metric as _emit_non_blocking_failure_metric,
    _hook_blocking as _hook_blocking,
    _hook_extension_name as _hook_extension_name,
    _register_hook_on_class as _register_hook_on_class,
    _should_execute_hook as _should_execute_hook,
    _sort_hooks_topologically as _sort_hooks_topologically,
    auto_register_hooks as auto_register_hooks,
    discover_hookable_methods as discover_hookable_methods,
    hook_bll as hook_bll,
    non_critical_hook as non_critical_hook,
    wrap_method_with_hooks as wrap_method_with_hooks,
)
from zephyrex.logic.AbstractLogicManager.models import (
    ApplicationModel as ApplicationModel,
    BatchUpdateItem as BatchUpdateItem,
    BooleanSearchModel as BooleanSearchModel,
    DateSearchModel as DateSearchModel,
    DescriptionMixinModel as DescriptionMixinModel,
    DtoT as DtoT,
    FieldComparison as FieldComparison,
    IDModel as IDModel,
    ImageMixinModel as ImageMixinModel,
    ModelFieldAccessor as ModelFieldAccessor,
    ModelMeta as ModelMeta,
    NameMixinModel as NameMixinModel,
    NumericalSearchModel as NumericalSearchModel,
    ParentMixinModel as ParentMixinModel,
    StringSearchModel as StringSearchModel,
    TemplateModel as TemplateModel,
    TemplateNetworkModel as TemplateNetworkModel,
    TemplateReferenceModel as TemplateReferenceModel,
    UpdateMixinModel as UpdateMixinModel,
    gen_not_found_msg as gen_not_found_msg,
)
from zephyrex.logic.AbstractLogicManager import manager as _manager
from zephyrex.logic.AbstractLogicManager.manager import (
    AbstractBLLManager as AbstractBLLManager,
    ModelT as ModelT,
    T as T,
    _BoundModelDescriptor as _BoundModelDescriptor,
    _cache_sync_run as _cache_sync_run,
    _escape_like as _escape_like,
    get_entity_cache as get_entity_cache,
    set_entity_cache as set_entity_cache,
)

import sys as _sys
from types import ModuleType as _ModuleType


class _AbstractLogicManagerPackage(_ModuleType):
    """Package module type exposing ``_entity_cache`` as a live view of the
    single source of truth defined in ``manager``.

    ``manager`` owns the reassignable ``_entity_cache`` global that every BLL
    read/write path uses. Consumers that poke the cache through this package's
    raw ``_entity_cache`` attribute (e.g. the ``EXT_DatabaseMemory`` wiring test
    that snapshots and restores it) must see — and be able to reset — that same
    storage. A plain ``from .manager import _entity_cache`` would snapshot the
    initial value and silently diverge once the global is reassigned, so the
    attribute is delegated to ``manager`` for both get and set here.
    """

    @property
    def _entity_cache(self):
        return _manager._entity_cache

    @_entity_cache.setter
    def _entity_cache(self, value):
        _manager._entity_cache = value


_sys.modules[__name__].__class__ = _AbstractLogicManagerPackage
