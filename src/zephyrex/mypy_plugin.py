# SPDX-License-Identifier: AGPL-3.0-or-later
"""mypy plugin that teaches the type checker what ``ModelMeta`` builds at runtime.

Enable it next to pydantic's plugin in the consumer's mypy config, listed
first::

    [tool.mypy]
    plugins = ["zephyrex.mypy_plugin", "pydantic.mypy"]

Order matters: mypy gives a class's base-class hook to the first plugin that
claims it, and pydantic's claims every ``BaseModel`` subclass. This plugin
therefore runs pydantic's class transform itself before its own work on the
classes it handles, and leaves every other class to ``pydantic.mypy``.

``ModelMeta`` does two things mypy cannot see statically:

1. For every database-backed model it generates a ``Reference`` family
   (``Reference``, ``Reference.Optional``, ``Reference.ID``,
   ``Reference.ID.Optional``, ``Reference.ID.Search``) that other models use as
   mixins to declare a foreign key. This plugin synthesizes the same classes
   with the same fields (see ``ModelMeta._create_reference_class``).

2. Mixins such as ``UpdateMixinModel`` each carry their own nested
   ``Optional``/``Search`` namespace classes, and the framework reads them per
   declaring class. On a model that inherits several mixins, ``Model.Optional``
   resolves through the MRO like any attribute; mypy instead treats the
   sibling nested classes as conflicting definitions. The plugin binds each
   such name on the model to the MRO-first definition, which is exactly what
   Python resolves.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import stringcase
from mypy.mro import MroError, calculate_mro
from mypy.nodes import (
    MDEF,
    Block,
    ClassDef,
    MypyFile,
    SymbolTable,
    SymbolTableNode,
    TypeInfo,
    Var,
)
from mypy.options import Options
from mypy.plugin import ClassDefContext, Plugin, SemanticAnalyzerPluginInterface
from mypy.types import Instance, NoneType, Type, UnionType

MODEL_META_FULLNAME = "zephyrex.logic.AbstractLogicManager.models.ModelMeta"
DATABASE_MIXIN_FULLNAME = "zephyrex.pydantic2.sqlalchemy.mixins.DatabaseMixin"
STRING_SEARCH_FULLNAME = "zephyrex.logic.AbstractLogicManager.models.StringSearchModel"
METADATA_KEY = "zephyrex_model_meta"
REFERENCE_MARKER = "zephyrex_reference"
FRAMEWORK_MODEL_MODULES = frozenset(
    {
        "zephyrex.logic.AbstractLogicManager.models",
        "zephyrex.pydantic2.sqlalchemy.mixins",
    }
)

# ModelMeta skips Reference generation for these class-name prefixes.
REFERENCE_EXEMPT_PREFIXES = ("Base", "Application")


def _uses_model_meta(info: TypeInfo) -> bool:
    return any(
        base.declared_metaclass is not None
        and base.declared_metaclass.type.fullname == MODEL_META_FULLNAME
        for base in info.mro
    )


def _reference_field_name(model_name: str) -> str:
    """Mirror ``ModelMeta._create_reference_class``'s field naming."""
    stem = model_name[: -len("Model")] if model_name.endswith("Model") else model_name
    return str(stringcase.snakecase(stem))


def _optional(typ: Type) -> Type:
    return UnionType([typ, NoneType()])


def _add_class(
    api: SemanticAnalyzerPluginInterface,
    owner: TypeInfo,
    name: str,
    bases: List[Instance],
    fields: Dict[str, Type],
) -> TypeInfo:
    """Create a class nested in ``owner`` with the given bases and fields."""
    fullname = f"{owner.fullname}.{name}"
    classdef = ClassDef(name, Block([]))
    classdef.fullname = fullname
    info = TypeInfo(SymbolTable(), classdef, owner.module_name)
    classdef.info = info
    info.metadata[REFERENCE_MARKER] = {}
    info.bases = bases or [api.named_type("builtins.object")]
    try:
        calculate_mro(info)
    except MroError:
        api.fail(f"Cannot build a consistent MRO for {fullname}", classdef)
    for field_name, field_type in fields.items():
        var = Var(field_name, field_type)
        var.info = info
        var._fullname = f"{fullname}.{field_name}"
        info.names[field_name] = SymbolTableNode(MDEF, var, plugin_generated=True)
    owner.names[name] = SymbolTableNode(MDEF, info, plugin_generated=True)
    return info


def _add_reference_family(
    api: SemanticAnalyzerPluginInterface, model: TypeInfo
) -> None:
    field_name = _reference_field_name(model.name)
    id_field_name = f"{field_name}_id"
    str_type = api.named_type("builtins.str")
    model_type = _optional(Instance(model, []))
    string_search = api.named_type_or_none(STRING_SEARCH_FULLNAME)
    search_type: Type = (
        _optional(string_search) if string_search else _optional(str_type)
    )

    # Created before its ID base so ``Reference.ID`` lives in Reference's
    # namespace, matching the runtime ``Reference.ID = ID`` attachment.
    reference = _add_class(api, model, "Reference", [], {})
    id_info = _add_class(api, reference, "ID", [], {id_field_name: str_type})
    id_optional = _add_class(
        api, id_info, "Optional", [], {id_field_name: _optional(str_type)}
    )
    _add_class(api, id_info, "Search", [], {id_field_name: search_type})

    reference.bases = [Instance(id_info, [])]
    reference.names[field_name] = _field_node(reference, field_name, model_type)
    calculate_mro(reference)
    _add_class(
        api,
        reference,
        "Optional",
        [Instance(id_optional, [])],
        {field_name: model_type},
    )


def _field_node(owner: TypeInfo, name: str, typ: Type) -> SymbolTableNode:
    var = Var(name, typ)
    var.info = owner
    var._fullname = f"{owner.fullname}.{name}"
    return SymbolTableNode(MDEF, var, plugin_generated=True)


def _bind_nested_namespaces(info: TypeInfo) -> None:
    """Bind nested classes defined by more than one base to the MRO-first one.

    Runs on every semantic-analysis pass: names the plugin bound earlier are
    recomputed (a base may have been incomplete on the first pass), names the
    class defines itself are never touched.
    """
    metadata = info.metadata.setdefault(METADATA_KEY, {})
    previously_bound: List[str] = metadata.get("bound", [])
    for name in previously_bound:
        info.names.pop(name, None)
    definitions: Dict[str, List[TypeInfo]] = {}
    for base in info.mro[1:]:
        for name, node in base.names.items():
            if isinstance(node.node, TypeInfo) and name not in info.names:
                definitions.setdefault(name, []).append(node.node)
    bound = []
    for name, nested in definitions.items():
        if len({n.fullname for n in nested}) > 1:
            info.names[name] = SymbolTableNode(MDEF, nested[0], plugin_generated=True)
            bound.append(name)
    metadata["bound"] = bound


def _bind_hook(ctx: ClassDefContext) -> None:
    _bind_nested_namespaces(ctx.cls.info)


def _transform_model(ctx: ClassDefContext) -> None:
    info = ctx.cls.info
    metadata = info.metadata.setdefault(METADATA_KEY, {})
    # Generate Reference before binding so the generated class owns the name
    # and is never mistaken for a plugin binding on a later pass.
    if (
        not metadata.get("reference")
        and info.has_base(DATABASE_MIXIN_FULLNAME)
        and not info.name.startswith(REFERENCE_EXEMPT_PREFIXES)
    ):
        _add_reference_family(ctx.api, info)
        metadata["reference"] = True
    _bind_nested_namespaces(info)


def _is_framework_model_base(info: TypeInfo) -> bool:
    """True for the framework's model bases/mixins and generated Reference
    classes: the classes that carry nested ``Optional``/``Search``/``ID``
    namespaces meant to be read per declaring class."""
    return any(
        base.module_name in FRAMEWORK_MODEL_MODULES or REFERENCE_MARKER in base.metadata
        for base in info.mro
    )


ClassHook = Callable[[ClassDefContext], None]


def _chain(first: ClassHook, then: ClassHook) -> ClassHook:
    def hook(ctx: ClassDefContext) -> None:
        first(ctx)
        then(ctx)

    return hook


class ZephyrexPlugin(Plugin):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        # Imported here: pydantic.mypy only imports cleanly inside a mypy run.
        from pydantic.mypy import PydanticPlugin

        self._pydantic = PydanticPlugin(options)

    def set_modules(self, modules: Dict[str, MypyFile]) -> None:
        super().set_modules(modules)
        self._pydantic.set_modules(modules)

    def get_metaclass_hook(self, fullname: str) -> Optional[ClassHook]:
        return _transform_model if fullname == MODEL_META_FULLNAME else None

    def get_base_class_hook(self, fullname: str) -> Optional[ClassHook]:
        sym = self.lookup_fully_qualified(fullname)
        if sym is None or not isinstance(sym.node, TypeInfo):
            return None
        if _uses_model_meta(sym.node):
            ours: ClassHook = _transform_model
        elif _is_framework_model_base(sym.node):
            ours = _bind_hook
        else:
            return None
        pydantic_hook = self._pydantic.get_base_class_hook(fullname)
        return _chain(pydantic_hook, ours) if pydantic_hook else ours


def plugin(version: str) -> type[Plugin]:
    return ZephyrexPlugin
