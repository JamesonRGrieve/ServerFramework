"""
SDK handler generator for ``RouterMixin``-tagged BLL managers.

This module walks a registry of ``RouterMixin``-tagged manager classes and
emits one ``<Resource>SDK_generated.py`` file per resource. Generated files
follow the same ``ResourceConfig``-driven shape as the hand-authored handlers
in ``src/sdk/`` (e.g. ``UserSDK``, ``TeamSDK``) and expose the standard CRUD,
batch, and search surface via :class:`AbstractSDKHandler`.

Determinism contract:
    * Field order is alphabetical.
    * Imports are sorted.
    * No timestamps, random ids, or other non-deterministic content.
    * Two calls against the same registry produce byte-identical output.

The :func:`generate_sdk_handlers` function supports a ``check_only`` mode
that returns a list of ``(path, drift_kind)`` tuples for files whose on-disk
contents differ from the regenerated output, without writing to disk. This
is suitable for CI drift checks.

Versioning (Item 39)
--------------------
Each ``RouterMixin``-tagged manager carries a ``version`` ClassVar
(default ``"v1"``). When ``version != "v1"`` the generated SDK class name
gains a Pascal-cased version suffix (``"v2"`` -> ``UserV2SDK_generated``,
``"v2beta"`` -> ``UserV2BetaSDK_generated``) and every emitted method name
gains a lower-case ``_<version>`` suffix (``list_v2``, ``get_v2``, ...).
The default version (``v1``) is left untouched so existing generated files
remain byte-identical.

Generated files include the following metadata comments near the top:

    * ``# version: <version>`` — always emitted
    * ``# deprecated_in: <iso-8601>`` — when ``RouterMixin.deprecated_in`` is set
    * ``# sunset_in: <iso-8601>`` — when ``RouterMixin.sunset_in`` is set

Future cleanup item: ``Pydantic2SDKHandler`` is currently fully commented
out; once it ships a public API for resource-name / endpoint inference,
this generator should consume that API rather than re-implementing the
inference here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple, Type

import inflect
import stringcase

_inflect_engine = inflect.engine()


def _is_router_mixin_manager(cls: Any) -> bool:
    """Return True iff ``cls`` is a class tagged with ``RouterMixin``."""
    if not isinstance(cls, type):
        return False
    for base in cls.__mro__:
        if base.__name__ == "RouterMixin":
            return True
    return False


def _iter_manager_classes(registry: Any) -> List[Type]:
    """Extract the list of ``RouterMixin``-tagged manager classes from ``registry``.

    ``registry`` may be:
      * an iterable / sequence of classes,
      * an object exposing ``managers`` (attribute or method),
      * an object exposing ``router_managers`` (attribute or method),
      * an object exposing ``bound_models`` — in that case we look up
        ``model._manager`` if present (best-effort).

    Anything that is not a ``RouterMixin``-tagged class is filtered out.
    Order is preserved by ``__module__`` then ``__qualname__`` so output
    is deterministic regardless of input ordering.
    """
    candidates: List[Any] = []

    if registry is None:
        return []

    if isinstance(registry, (list, tuple, set, frozenset)):
        candidates = list(registry)
    elif hasattr(registry, "managers"):
        managers = registry.managers
        if callable(managers):
            managers = managers()
        candidates = list(managers)
    elif hasattr(registry, "router_managers"):
        rm = registry.router_managers
        if callable(rm):
            rm = rm()
        candidates = list(rm)
    else:
        try:
            candidates = list(registry)
        except TypeError:
            candidates = []

    managers = [c for c in candidates if _is_router_mixin_manager(c)]
    managers.sort(key=lambda c: (c.__module__, c.__qualname__))
    return managers


def _resource_name_for(manager_cls: Type) -> str:
    """Derive the snake_case singular resource name for ``manager_cls``.

    Mirrors the convention in ``Pydantic2FastAPI.register_route``:
    strip the ``Manager`` suffix and snake_case the remainder.
    Leading underscores (used for fake/test classes) are removed so the
    resource name remains a valid identifier-ish snake_case string.
    """
    name = manager_cls.__name__
    name = name.lstrip("_")
    if name.endswith("Manager"):
        name = name[: -len("Manager")]
    return stringcase.snakecase(name)


def _resource_name_plural_for(resource_name: str) -> str:
    """Plural form of ``resource_name`` using inflect with classical plurals."""
    plural = _inflect_engine.plural(resource_name)
    return plural if isinstance(plural, str) and plural else f"{resource_name}s"


def _endpoint_for(manager_cls: Type, resource_name: str) -> str:
    """Determine the endpoint prefix for a manager class.

    Honors the ``prefix`` ClassVar when set; otherwise derives
    ``/<version>/<resource_name>`` from the ``version`` ClassVar
    (defaulting to ``v1``).
    """
    prefix = getattr(manager_cls, "prefix", None)
    if prefix:
        return prefix.rstrip("/")
    version = getattr(manager_cls, "version", "v1") or "v1"
    return f"/{version}/{resource_name}"


def _version_suffix_for(version: str) -> str:
    """Pascal-cased version suffix used in generated class / file names.

    Returns the empty string for the default ``v1`` so existing output is
    preserved byte-for-byte. For other versions a deterministic
    Pascal-cased token is produced (``"v2"`` -> ``"V2"``,
    ``"v2beta"`` -> ``"V2Beta"``, ``"v2_beta"`` -> ``"V2Beta"``).
    """
    if not version or version == "v1":
        return ""
    cleaned = version.replace("-", "_")
    if "_" in cleaned:
        parts = [p for p in cleaned.split("_") if p]
    else:
        # Split on the leading vN... numeric chunk so trailing alpha
        # ("beta", "alpha", ...) becomes its own pascalcase token.
        parts = []
        idx = 0
        if cleaned and cleaned[0].lower() == "v":
            num_end = 1
            while num_end < len(cleaned) and cleaned[num_end].isdigit():
                num_end += 1
            if num_end > 1:
                parts.append(cleaned[:num_end])
                idx = num_end
        if idx < len(cleaned):
            parts.append(cleaned[idx:])
        if not parts:
            parts = [cleaned]
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


def _method_version_suffix_for(version: str) -> str:
    """Lower-case method-name suffix (``""`` for default ``v1``)."""
    if not version or version == "v1":
        return ""
    return f"_{version}"


def _sdk_class_name_for(resource_name: str, version: str = "v1") -> str:
    """Generated SDK class name (PascalCase + version suffix + ``SDK``)."""
    return (
        f"{stringcase.pascalcase(resource_name)}"
        f"{_version_suffix_for(version)}SDK"
    )


def _sdk_filename_for(resource_name: str, version: str = "v1") -> str:
    """Generated file name (PascalCase + version suffix + ``SDK_generated.py``)."""
    return (
        f"{stringcase.pascalcase(resource_name)}"
        f"{_version_suffix_for(version)}SDK_generated.py"
    )


def generate_sdk_handler_for(manager_cls: Type) -> str:
    """Generate the source text of an SDK handler for one manager class.

    The output is deterministic: imports are sorted, field configuration
    uses alphabetically ordered keyword arguments, and no timestamps or
    random identifiers appear. The generated module declares a single
    ``<Resource>SDK`` class extending ``AbstractSDKHandler`` and exposes
    ``list``, ``get``, ``create``, ``update``, ``delete``, ``batch_create``,
    ``batch_update``, ``batch_delete``, and ``search`` operations through
    a ``ResourceConfig`` block.
    """
    if not _is_router_mixin_manager(manager_cls):
        raise TypeError(
            f"{manager_cls!r} is not a RouterMixin-tagged manager class"
        )

    resource_name = _resource_name_for(manager_cls)
    resource_name_plural = _resource_name_plural_for(resource_name)
    endpoint = _endpoint_for(manager_cls, resource_name)
    version = getattr(manager_cls, "version", "v1") or "v1"
    deprecated_in = getattr(manager_cls, "deprecated_in", None)
    sunset_in = getattr(manager_cls, "sunset_in", None)
    method_suffix = _method_version_suffix_for(version)
    class_name = _sdk_class_name_for(resource_name, version)
    manager_qualname = f"{manager_cls.__module__}.{manager_cls.__qualname__}"

    imports = sorted(
        [
            "from typing import Any, Dict, List, Optional",
            "from sdk.AbstractSDKHandler import AbstractSDKHandler, ResourceConfig",
        ]
    )

    config_kwargs = sorted(
        [
            f'name="{resource_name}"',
            f'name_plural="{resource_name_plural}"',
            f'endpoint="{endpoint}"',
            "supports_batch=True",
            "supports_search=True",
        ]
    )
    config_kwargs_block = ",\n                ".join(config_kwargs)

    manager_attr = resource_name_plural

    lines: List[str] = []
    lines.append('"""')
    lines.append(f"Auto-generated SDK handler for {manager_cls.__name__}.")
    lines.append("")
    lines.append("This file is generated by ``sdk.SDKGenerator``. Do not edit by hand;")
    lines.append("regenerate via ``generate_sdk_handlers`` (see Item 25).")
    lines.append("")
    lines.append(f"Source manager: ``{manager_qualname}``")
    lines.append('"""')
    lines.append("")
    # Item 39 — emit version + deprecation metadata as comments.
    # To preserve byte-identical output for the default ``v1`` case
    # (existing behavior), the version comment is only emitted when
    # ``version != "v1"`` *or* when deprecation / sunset metadata is
    # present. Deprecation / sunset comments are emitted whenever the
    # corresponding ClassVars are set, regardless of version.
    metadata_lines: List[str] = []
    if version != "v1":
        metadata_lines.append(f"# version: {version}")
    if deprecated_in:
        metadata_lines.append(f"# deprecated_in: {deprecated_in}")
    if sunset_in:
        metadata_lines.append(f"# sunset_in: {sunset_in}")
    if metadata_lines:
        # When deprecation/sunset are present without a version override,
        # still emit the version line so the comment block is self-describing.
        if version == "v1" and metadata_lines and not metadata_lines[0].startswith("# version:"):
            metadata_lines.insert(0, f"# version: {version}")
        for line in metadata_lines:
            lines.append(line)
        lines.append("")
    for imp in imports:
        lines.append(imp)
    lines.append("")
    lines.append("")
    lines.append(f"class {class_name}(AbstractSDKHandler):")
    lines.append(f'    """Generated SDK for ``{resource_name}`` resources."""')
    lines.append("")
    lines.append("    def _configure_resources(self) -> Dict[str, ResourceConfig]:")
    lines.append("        return {")
    lines.append(f'            "{manager_attr}": ResourceConfig(')
    lines.append(f"                {config_kwargs_block},")
    lines.append("            ),")
    lines.append("        }")
    lines.append("")
    lines.append(
        f"    def list{method_suffix}("
        "\n        self,"
        "\n        offset: int = 0,"
        "\n        limit: int = 100,"
        "\n        next_token: Optional[str] = None,"
        "\n        fields: Optional[List[str]] = None,"
        "\n        **filters: Any,"
        "\n    ) -> Dict[str, Any]:"
    )
    lines.append(f'        """List ``{resource_name_plural}`` with pagination."""')
    lines.append("        params: Dict[str, Any] = dict(filters)")
    lines.append('        params["offset"] = offset')
    lines.append('        params["limit"] = limit')
    lines.append("        if next_token is not None:")
    lines.append('            params["next_token"] = next_token')
    lines.append("        if fields is not None:")
    lines.append('            params["fields"] = ",".join(fields)')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").list(**params)"
    )
    lines.append("")
    lines.append(
        f"    def get{method_suffix}("
        "\n        self,"
        "\n        resource_id: str,"
        "\n        fields: Optional[List[str]] = None,"
        "\n    ) -> Dict[str, Any]:"
    )
    lines.append(f'        """Get a single ``{resource_name}`` by id."""')
    lines.append(f"        manager = getattr(self, \"{manager_attr}\")")
    lines.append("        if fields is None:")
    lines.append("            return manager.get(resource_id)")
    lines.append(
        "        return self._request("
        "\n            \"GET\","
        f"\n            f\"{endpoint}/{{resource_id}}\","
        '\n            query_params={"fields": ",".join(fields)},'
        f"\n            resource_name=\"{resource_name}\","
        "\n        )"
    )
    lines.append("")
    lines.append(
        f"    def create{method_suffix}(self, data: Dict[str, Any]) -> Dict[str, Any]:"
    )
    lines.append(f'        """Create a new ``{resource_name}``."""')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").create(data)"
    )
    lines.append("")
    lines.append(
        f"    def update{method_suffix}("
        "\n        self,"
        "\n        resource_id: str,"
        "\n        updates: Dict[str, Any],"
        "\n    ) -> Dict[str, Any]:"
    )
    lines.append(f'        """Update an existing ``{resource_name}``."""')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").update("
        "resource_id, updates)"
    )
    lines.append("")
    lines.append(f"    def delete{method_suffix}(self, resource_id: str) -> None:")
    lines.append(f'        """Delete a ``{resource_name}`` by id."""')
    lines.append(
        f"        getattr(self, \"{manager_attr}\").delete(resource_id)"
    )
    lines.append("")
    lines.append(
        f"    def search{method_suffix}("
        "\n        self,"
        "\n        criteria: Dict[str, Any],"
        "\n        offset: int = 0,"
        "\n        limit: int = 100,"
        "\n        next_token: Optional[str] = None,"
        "\n        fields: Optional[List[str]] = None,"
        "\n    ) -> Dict[str, Any]:"
    )
    lines.append(f'        """Search ``{resource_name_plural}``."""')
    lines.append("        params: Dict[str, Any] = dict(criteria)")
    lines.append('        params["offset"] = offset')
    lines.append('        params["limit"] = limit')
    lines.append("        if next_token is not None:")
    lines.append('            params["next_token"] = next_token')
    lines.append("        if fields is not None:")
    lines.append('            params["fields"] = ",".join(fields)')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").search(params)"
    )
    lines.append("")
    lines.append(
        f"    def batch_create{method_suffix}(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:"
    )
    lines.append(f'        """Create multiple ``{resource_name_plural}`` in one call."""')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").batch_create(items)"
    )
    lines.append("")
    lines.append(
        f"    def batch_update{method_suffix}("
        "\n        self,"
        "\n        updates: Dict[str, Any],"
        "\n        resource_ids: List[str],"
        "\n    ) -> Dict[str, Any]:"
    )
    lines.append(f'        """Update multiple ``{resource_name_plural}`` in one call."""')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").batch_update("
        "updates, resource_ids)"
    )
    lines.append("")
    lines.append(
        f"    def batch_delete{method_suffix}(self, resource_ids: List[str]) -> Dict[str, Any]:"
    )
    lines.append(f'        """Delete multiple ``{resource_name_plural}`` in one call."""')
    lines.append(
        f"        return getattr(self, \"{manager_attr}\").batch_delete(resource_ids)"
    )
    lines.append("")

    return "\n".join(lines)


def _path_for(manager_cls: Type, output_dir: Path) -> Path:
    resource_name = _resource_name_for(manager_cls)
    version = getattr(manager_cls, "version", "v1") or "v1"
    return output_dir / _sdk_filename_for(resource_name, version)


def generate_sdk_handlers(
    registry: Any,
    output_dir: Path,
    check_only: bool = False,
) -> List[Any]:
    """Generate ``<Resource>SDK_generated.py`` for each manager in ``registry``.

    Args:
        registry: A registry (or any iterable) yielding ``RouterMixin``-tagged
            manager classes. See :func:`_iter_manager_classes` for the
            accepted shapes.
        output_dir: Directory to emit generated files into. Created if it
            does not exist (when not in ``check_only`` mode).
        check_only: If ``True``, no files are written. Returns a list of
            ``(path, drift_kind)`` tuples — ``drift_kind`` is one of
            ``"missing"`` (file is absent) or ``"modified"`` (file exists
            but differs byte-for-byte from the freshly generated output).

    Returns:
        When ``check_only`` is ``False``: a sorted list of :class:`Path`
        objects pointing at the files that were written.
        When ``check_only`` is ``True``: a sorted list of
        ``(Path, drift_kind)`` tuples for files that drift; an empty list
        means the on-disk files match the registry.
    """
    output_dir = Path(output_dir)
    managers = _iter_manager_classes(registry)

    if not check_only:
        output_dir.mkdir(parents=True, exist_ok=True)

    results: List[Any] = []
    drifts: List[Tuple[Path, str]] = []

    for manager_cls in managers:
        path = _path_for(manager_cls, output_dir)
        content = generate_sdk_handler_for(manager_cls)

        if check_only:
            if not path.exists():
                drifts.append((path, "missing"))
                continue
            existing = path.read_text(encoding="utf-8")
            if existing != content:
                drifts.append((path, "modified"))
            continue

        path.write_text(content, encoding="utf-8")
        results.append(path)

    if check_only:
        drifts.sort(key=lambda item: str(item[0]))
        return drifts

    results.sort(key=str)
    return results
