import asyncio
import inspect
import sys
import threading
from abc import ABC
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)


import numpy as np
import stringcase
from fastapi import HTTPException
from fastapi.encoders import ENCODERS_BY_TYPE
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_

ModelT = TypeVar("ModelT", bound=BaseModel)
from sqlalchemy.orm import Session, joinedload

from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import BaseNetworkModel, classproperty
from zephyrex.lib.Pydantic2FastAPI import AuthType
from zephyrex.lib.Pydantic2SQLAlchemy import DatabaseMixin

T = TypeVar("T")


class HookTiming(Enum):
    """Enumeration for hook execution timing."""

    BEFORE = "before"
    AFTER = "after"


# ---------------------------------------------------------------------------
# Item 21 -- Deterministic ordering / cycle detection
# ---------------------------------------------------------------------------


class HookOrderingError(Exception):
    """Raised at startup when explicit before/after constraints form a cycle.

    The error message names every extension involved in the offending cycle
    so the developer can resolve it without inspecting the registry by hand.
    """


# Generic ParamSpec / typing imports (Item 41).
try:
    from typing import ParamSpec  # Python 3.10+
except ImportError:  # pragma: no cover - fallback for older runtimes
    from typing_extensions import ParamSpec  # type: ignore[assignment]

import typing as _typing  # noqa: E402

_P = ParamSpec("_P")
_R = _typing.TypeVar("_R")


class HookContext(_typing.Generic[_P, _R]):
    """
    Type-safe context object passed to hooks (Item 41).

    ``HookContext[P, R]`` is parameterized by the target method's
    ``ParamSpec`` ``P`` and return type ``R`` so static analysis catches
    hooks that read fields not present on the target. Existing untyped
    hooks (``def my_hook(ctx: HookContext)``) continue to work because
    ``Generic`` is permissive at runtime; deprecation of the untyped path
    is a follow-up.

    Migration guide::

        # Old, still works (deprecated typing):
        def my_hook(ctx: HookContext) -> None:
            user_id = ctx.kwargs["user_id"]   # untyped Any

        # New, fully typed:
        def my_hook(ctx: HookContext[..., User]) -> None:
            user_id = ctx.kwarg("user_id")    # typed accessor

    The typed accessors (``kwarg``, ``arg``, ``set_result``) are runtime
    methods; the static type-correlation is enforced by mypy/pyright when
    callers spell out the parameterization.
    """

    def __init__(
        self,
        manager: "AbstractBLLManager",
        method_name: str,
        args: tuple,
        kwargs: dict,
        result: Any | None = None,
        timing: HookTiming = HookTiming.BEFORE,
    ):
        """
        Initialize hook context.

        Args:
            manager: The manager instance executing the method
            method_name: Name of the method being executed
            args: Positional arguments passed to the method
            kwargs: Keyword arguments passed to the method
            result: Result from method execution (for after hooks)
            timing: When this hook is executing (HookTiming.BEFORE or HookTiming.AFTER)
        """
        self.manager = manager
        self.method_name = method_name
        self.timing = timing
        self.args = list(args)  # Mutable for modification
        self.kwargs = kwargs.copy()  # Mutable for modification
        self.result = result
        self.skip_execution = False
        self.modified_result = None
        self.condition_data = {}  # type: ignore[var-annotated]

    def set_result(self, result: _R) -> None:
        """
        Set a custom result that will override the method's original return value.

        Args:
            result: The custom result to return
        """
        self.modified_result = result  # type: ignore[assignment]

    def skip_method(self) -> None:
        """Skip execution of the original method."""
        self.skip_execution = True

    # -- typed accessors (Item 41) -----------------------------------------

    def kwarg(self, name: str, default: Any | None = None) -> Any:
        """Typed-accessor sugar: return ``kwargs[name]`` or ``default``.

        Authors using ``HookContext[..., R]`` get a typed return shape via
        the param-spec; the runtime falls through to dict.get for safety.
        """
        return self.kwargs.get(name, default)

    def arg(self, index: int, default: Any | None = None) -> Any:
        """Typed-accessor sugar: return ``args[index]`` or ``default``."""
        try:
            return self.args[index]
        except IndexError:
            return default


class HookRegistry:
    """
    Registry for managing hooks at the class level with inheritance support.

    This registry maintains hooks for each method and supports inheritance,
    allowing child classes to inherit hooks from parent classes while
    adding their own.
    """

    def __init__(self, parent_registry: Optional["HookRegistry"] | None = None):
        """
        Initialize hook registry.

        Args:
            parent_registry: Parent registry for inheritance support
        """
        self.parent_registry = parent_registry
        self.hooks: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def clear(self) -> None:
        """Clear all hooks from this registry."""
        self.hooks.clear()

    def get_hooks(self, method_name: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get all hooks for a method, including inherited ones.

        Args:
            method_name: Name of the method to get hooks for

        Returns:
            Dictionary with 'before' and 'after' lists of hook info dictionaries.
            Lists are returned in the deterministic four-tier order described
            in Item 21: explicit ``before/after`` constraints, then priority,
            then extension name, then function name.
        """
        hooks = {"before": [], "after": []}  # type: ignore[var-annotated]

        # Get parent hooks first (for inheritance)
        if self.parent_registry:
            parent_hooks = self.parent_registry.get_hooks(method_name)
            hooks["before"].extend(parent_hooks["before"])
            hooks["after"].extend(parent_hooks["after"])

        # Add our own hooks
        if method_name in self.hooks:
            hooks["before"].extend(self.hooks[method_name]["before"])
            hooks["after"].extend(self.hooks[method_name]["after"])

        # Item 21: apply the deterministic four-tier sort BEFORE returning.
        hooks["before"] = _sort_hooks_topologically(hooks["before"])
        hooks["after"] = _sort_hooks_topologically(hooks["after"])
        return hooks

    def register_hook(
        self,
        target_class: Type["AbstractBLLManager"],
        method_name: str,
        timing: str,
        hook_func: Callable,
        priority: int = 50,
        condition: Optional[Callable] | None = None,
        before: Optional[List[str]] | None = None,
        after: Optional[List[str]] | None = None,
        blocking: Optional[bool] | None = None,
    ) -> None:
        """
        Register a hook for a specific method.

        Args:
            target_class: The manager class to register the hook on
            method_name: Name of the method to hook
            timing: When to execute ('before' or 'after')
            hook_func: Function to execute as hook
            priority: Execution priority (lower numbers run first)
            condition: Optional condition function for conditional execution
            before: List of extension names this hook must run BEFORE (Item 21).
            after: List of extension names this hook must run AFTER (Item 21).
            blocking: If True, exceptions from the hook propagate (Item 22).
                If False, exceptions are logged + a metric is emitted and the
                operation continues. None preserves the timing-default
                (BEFORE -> True, AFTER -> False).
        """
        if method_name not in self.hooks:
            self.hooks[method_name] = {"before": [], "after": []}

        hook_info = {
            "func": hook_func,
            "priority": priority,
            "condition": condition,
            "before": list(before or []),
            "after": list(after or []),
            "blocking": blocking,
            "extension": _hook_extension_name(hook_func),
        }

        self.hooks[method_name][timing].append(hook_info)
        # Sort by priority (lower numbers run first); the get_hooks accessor
        # then applies the full four-tier topological sort.
        self.hooks[method_name][timing].sort(key=lambda x: x["priority"])


# ---------------------------------------------------------------------------
# Item 21 helpers: extension-name derivation + topological sort
# ---------------------------------------------------------------------------


# Item 22: per-process counter exposed for observability/metric collectors.
# Tests assert against this rather than wiring a real metrics backend.
NON_BLOCKING_HOOK_FAILURES: Dict[str, int] = {}


def _hook_blocking(hook_info: Dict[str, Any], default: bool) -> bool:
    """Resolve the blocking flag for a hook, falling back to ``default``."""
    explicit = hook_info.get("blocking")
    if explicit is None:
        return default
    return bool(explicit)


def _emit_non_blocking_failure_metric(
    method_name: str,
    timing: str,
    hook_func: Callable,
    error: BaseException,
) -> None:
    """Item 22 metric emission for a swallowed hook exception.

    The framework does not bind a specific metrics backend here; this helper
    increments an in-process counter and logs a structured warning. A real
    deployment plugs into Prometheus / OTel via a logger handler or by
    monkey-patching this function at startup.
    """
    metric_key = (
        f"hook.non_blocking_failure"
        f"|method={method_name}"
        f"|timing={timing}"
        f"|hook={getattr(hook_func, '__name__', '<unknown>')}"
    )
    NON_BLOCKING_HOOK_FAILURES[metric_key] = (
        NON_BLOCKING_HOOK_FAILURES.get(metric_key, 0) + 1
    )
    logger.warning(
        f"Non-blocking hook failure: method={method_name} timing={timing}"
        f" hook={getattr(hook_func, '__name__', '<unknown>')}"
        f" error={type(error).__name__}: {error}"
    )


def _hook_extension_name(func: Callable) -> str:
    """Best-effort extension name for a hook function.

    The convention used across the framework's extensions is to put the
    hook in a module path containing the extension name (e.g.
    ``extensions.payment.hooks``). We extract the segment that comes after
    ``extensions.`` if present; otherwise we fall back to the module name.
    """
    module = getattr(func, "__module__", "") or ""
    parts = module.split(".")
    if "extensions" in parts:
        idx = parts.index("extensions")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    # Fall back to the leaf module name -- gives a stable per-file token.
    return parts[-1] if parts else ""


def _sort_hooks_topologically(hooks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply the Item 21 four-tier deterministic ordering rule.

    Tiers:
      1. Explicit ``before=[ExtName]``/``after=[ExtName]`` constraints
         (topological sort; cycles raise :class:`HookOrderingError`).
      2. ``priority`` (lower runs first).
      3. Extension name (alphabetical).
      4. Hook function name (alphabetical).
    """
    if len(hooks) <= 1:
        return list(hooks)

    # Build an extension -> [hooks] index. Constraints are extension-level.
    by_ext: Dict[str, List[Dict[str, Any]]] = {}
    for h in hooks:
        by_ext.setdefault(h.get("extension", ""), []).append(h)

    # Build dependency graph: edge u -> v means "u must come before v".
    # ``before=[v]`` on u  -> u -> v
    # ``after=[v]`` on u   -> v -> u
    graph: Dict[str, set] = {ext: set() for ext in by_ext.keys()}
    for h in hooks:
        ext = h.get("extension", "")
        for nxt in h.get("before") or []:
            if nxt in graph:
                graph.setdefault(ext, set()).add(nxt)
        for prev in h.get("after") or []:
            if prev in graph:
                graph.setdefault(prev, set()).add(ext)

    # Kahn's algorithm with deterministic tie-break (alphabetical extension).
    in_degree = {ext: 0 for ext in graph}
    for ext, outs in graph.items():
        for out in outs:
            in_degree[out] = in_degree.get(out, 0) + 1

    ordered_exts: List[str] = []
    ready = sorted([ext for ext, d in in_degree.items() if d == 0])
    while ready:
        ext = ready.pop(0)
        ordered_exts.append(ext)
        for out in sorted(graph.get(ext, set())):
            in_degree[out] -= 1
            if in_degree[out] == 0:
                ready.append(out)
        ready.sort()

    if len(ordered_exts) != len(graph):
        # Cycle: name every extension still with non-zero in-degree.
        offenders = sorted(ext for ext, d in in_degree.items() if d > 0)
        raise HookOrderingError(
            "Hook before/after constraints form a cycle among extensions: "
            f"{offenders}"
        )

    # Within each extension bucket, sort by (priority, function_name).
    result: List[Dict[str, Any]] = []
    for ext in ordered_exts:
        bucket = sorted(
            by_ext.get(ext, []),
            key=lambda h: (h.get("priority", 50), getattr(h["func"], "__name__", "")),
        )
        result.extend(bucket)
    return result


def hook_bll(
    target: Union[Type["AbstractBLLManager"], Callable],
    timing: Union[HookTiming, str] = HookTiming.BEFORE,
    priority: int = 50,
    condition: Optional[Callable[[HookContext], bool]] | None = None,
    before: Optional[List[str]] | None = None,
    after: Optional[List[str]] | None = None,
    blocking: Optional[bool] | None = None,
) -> Callable:
    """
    Enhanced hook decorator for BLL methods.

    This decorator allows registration of hooks that execute before or after
    specific methods on BLL manager classes, or all methods of a class.
    Hooks receive a HookContext object that provides access to the manager
    instance, method arguments, and the ability to modify execution.

    Args:
        target: Either a manager class (for all methods) or a specific method reference
        timing: When to execute (HookTiming.BEFORE/AFTER or "before"/"after")
        priority: Execution order (lower numbers run first)
        condition: Optional callable that returns bool for conditional execution
        before: Item 21 -- list of extension names this hook must run BEFORE.
            Resolved as a topological sort; cycles raise HookOrderingError.
        after: Item 21 -- list of extension names this hook must run AFTER.
            Same semantics as ``before``.
        blocking: Item 22 -- if True (default for BEFORE), exceptions from the
            hook propagate. If False (default for AFTER), exceptions are
            logged + a metric is emitted and the operation continues. Pass
            None to accept the timing-default.

    Returns:
        Decorator function that registers the hook

    Examples:
        # Apply to ALL methods of a class
        @hook_bll(ExtensionManager, timing=HookTiming.BEFORE, priority=5)
        def audit_all_operations(context: HookContext) -> None:
            logger.info(f"Executing {context.method_name}")

        # Apply to specific method using method reference
        @hook_bll(ExtensionManager.create, timing=HookTiming.BEFORE, priority=10)
        def validate_creation(context: HookContext) -> None:
            # Validation logic

        # Item 21 -- explicit ordering: audit must run AFTER mfa.
        @hook_bll(UserManager.create, timing=HookTiming.BEFORE, after=["mfa"])
        def audit_logging(context: HookContext) -> None:
            ...
    """
    # Determine if target is a class or method
    if inspect.isclass(target) and issubclass(target, AbstractBLLManager):
        # Class-level hook registration - applies to ALL methods
        target_class = target
        method_names = discover_hookable_methods(target_class)

    elif callable(target):
        # Check if this is a wrapped method from our hook system
        if hasattr(target, "_original_method"):
            # This is a wrapped method - extract the class and method name from the wrapped method
            original_method = target._original_method
            method_name = target.__name__  # This should be the original method name

            # Find the owning class by checking which class has this specific wrapped method
            target_class = None
            # Look for the class that has this specific wrapped method as an attribute
            frame = inspect.currentframe()
            try:
                # Try to get the calling module's globals first
                caller_frame = (
                    frame.f_back.f_back  # type: ignore[union-attr]
                )  # Go up two frames to get the test method frame
                if caller_frame:
                    caller_globals = caller_frame.f_globals
                    for name, obj in caller_globals.items():
                        if (
                            inspect.isclass(obj)
                            and issubclass(obj, AbstractBLLManager)
                            and hasattr(obj, method_name)
                            and getattr(obj, method_name) is target
                        ):
                            target_class = obj
                            break

                # If not found in caller globals, search through key modules only
                if target_class is None:
                    import sys

                    # Only search modules that are likely to contain our classes
                    search_modules = [
                        mod
                        for mod_name, mod in sys.modules.items()
                        if mod is not None
                        and (
                            "test" in mod_name.lower()
                            or "bll" in mod_name.lower()
                            or "logic" in mod_name.lower()
                            or mod_name == "__main__"
                        )
                    ]

                    for module in search_modules:
                        try:
                            for attr_name in dir(module):
                                try:
                                    attr = getattr(module, attr_name, None)
                                    if type(attr) is np.ndarray:
                                        # Avoids ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
                                        val_check_attr = list(attr)
                                    else:
                                        val_check_attr = attr  # type: ignore[assignment]
                                    if (
                                        val_check_attr
                                        and inspect.isclass(attr)
                                        and issubclass(attr, AbstractBLLManager)
                                        and hasattr(attr, method_name)
                                        and getattr(attr, method_name) is target
                                    ):
                                        target_class = attr
                                        break
                                except (TypeError, AttributeError, ImportError):
                                    continue
                        except (TypeError, AttributeError, ImportError):
                            continue
                        if target_class:
                            break

            finally:
                del frame  # Prevent reference cycles

            if target_class is None:
                raise ValueError(
                    f"Could not determine target class for hook target {target!r}. "
                    f"Pass a Manager class or ManagerClass.method_name."
                )

            method_names = [method_name]

        elif hasattr(target, "__self__") and inspect.isclass(target.__self__):
            # This is a bound method to a class (e.g., ManagerForTest.create)
            target_class = target.__self__  # type: ignore[assignment]
            method_name = target.__name__

            if not issubclass(target_class, AbstractBLLManager):  # type: ignore[arg-type]
                raise ValueError(f"Class {target_class.__name__} is not a BLL manager")  # type: ignore[union-attr]

            method_names = [method_name]

        elif not hasattr(target, "__self__") and hasattr(target, "__qualname__"):
            # Unbound method - extract class from qualname
            parts = target.__qualname__.split(".")
            if len(parts) >= 2:
                class_name = parts[-2]
                method_name = parts[-1]

                # Find the class in the current module context
                import sys

                current_module = sys.modules[target.__module__]

                if hasattr(current_module, class_name):
                    target_class = getattr(current_module, class_name)
                    if not (
                        inspect.isclass(target_class)
                        and issubclass(target_class, AbstractBLLManager)
                    ):
                        raise ValueError(f"Class {class_name} is not a BLL manager")
                else:
                    raise ValueError(
                        f"Could not find class {class_name} in module {target.__module__}"
                    )

                method_names = [method_name]
            else:
                raise ValueError(
                    f"Could not extract class and method from {target!r} "
                    f"(qualname={getattr(target, '__qualname__', '?')}). "
                    f"Pass ManagerClass.method_name."
                )
        else:
            raise ValueError(
                f"Invalid hook target: {target!r} (type={type(target).__name__}). "
                f"Expected a Manager class, Manager.method, or a decorated function."
            )
    else:
        raise ValueError(
            "Target must be either a BLL manager class or a method reference"
        )

    # Convert string timing to enum if needed
    if isinstance(timing, str):
        timing_enum = HookTiming.BEFORE if timing == "before" else HookTiming.AFTER
    else:
        timing_enum = timing

    def decorator(
        hook_func: Callable[[HookContext], Any],
    ) -> Callable[[HookContext], Any]:
        # Register hook for each method
        for method_name in method_names:
            # Verify method exists on class
            if not hasattr(target_class, method_name):
                raise ValueError(
                    f"Method {method_name} not found on class {target_class.__name__}"  # type: ignore[union-attr]
                )

            # Store hook metadata (for the first method if multiple)
            if not hasattr(hook_func, "_hook_metadata"):
                hook_func._hook_metadata = {  # type: ignore[attr-defined]
                    "target_class": target_class,
                    "method_names": method_names,
                    "timing": timing_enum.value,
                    "priority": priority,
                    "condition": condition,
                    "before": list(before or []),
                    "after": list(after or []),
                    "blocking": blocking,
                }

            # Register the hook
            _register_hook_on_class(
                target_class,  # type: ignore[arg-type]
                method_name,
                timing_enum.value,
                hook_func,
                priority,
                condition,
                before=before,
                after=after,
                blocking=blocking,
            )

        return hook_func

    return decorator


def non_critical_hook(
    target: Union[Type["AbstractBLLManager"], Callable],
    timing: Union[HookTiming, str] = HookTiming.AFTER,
    priority: int = 50,
    condition: Optional[Callable[[HookContext], bool]] | None = None,
    before: Optional[List[str]] | None = None,
    after: Optional[List[str]] | None = None,
) -> Callable:
    """Item 22 ergonomic alias for ``hook_bll(..., blocking=False)``.

    Use this for AFTER hooks that should never break the underlying
    operation (audit, notification, analytics).
    """
    return hook_bll(
        target=target,
        timing=timing,
        priority=priority,
        condition=condition,
        before=before,
        after=after,
        blocking=False,
    )


def _register_hook_on_class(
    target_class: Type["AbstractBLLManager"],
    method_name: str,
    timing: str,
    hook_func: Callable,
    priority: int,
    condition: Optional[Callable],
    before: Optional[List[str]] | None = None,
    after: Optional[List[str]] | None = None,
    blocking: Optional[bool] | None = None,
) -> None:
    """
    Register hook on the target class registry.

    Args:
        target_class: Class to register hook on
        method_name: Method to hook
        timing: When to execute ('before' or 'after')
        hook_func: Hook function
        priority: Execution priority
        condition: Optional condition function
        before: list of extension names this hook must run BEFORE (Item 21).
        after: list of extension names this hook must run AFTER (Item 21).
        blocking: per-hook blocking override (Item 22).
    """
    if not hasattr(target_class, "_hook_registry"):
        parent_registry = None
        for base in target_class.__bases__:
            if hasattr(base, "_hook_registry"):
                parent_registry = base._hook_registry
                break
        target_class._hook_registry = HookRegistry(parent_registry)  # type: ignore[attr-defined]

    target_class._hook_registry.register_hook(  # type: ignore[attr-defined]
        target_class,
        method_name,
        timing,
        hook_func,
        priority,
        condition,
        before=before,
        after=after,
        blocking=blocking,
    )


def discover_hookable_methods(manager_class: Type["AbstractBLLManager"]) -> List[str]:
    """
    Discover all public instance methods that can be hooked.

    Args:
        manager_class: The manager class to inspect

    Returns:
        List of method names that can be hooked
    """
    hookable_methods = []

    for name, method in inspect.getmembers(manager_class, predicate=inspect.isfunction):
        # Skip private methods and special methods
        if name.startswith("_") or name.startswith("__"):
            continue

        # Skip class methods and static methods
        if isinstance(method, (classmethod, staticmethod)):
            continue

        # Check if it's an instance method
        sig = inspect.signature(method)
        if sig.parameters and "self" in list(sig.parameters.keys())[:1]:
            hookable_methods.append(name)

    return hookable_methods


def auto_register_hooks(manager_class: Type["AbstractBLLManager"]) -> None:
    """
    Automatically create hook points for all public methods.

    Args:
        manager_class: The manager class to set up hooks for
    """
    if not hasattr(manager_class, "_hook_registry"):
        parent_registry = None
        for base in manager_class.__bases__:
            if hasattr(base, "_hook_registry"):
                parent_registry = base._hook_registry
                break
        manager_class._hook_registry = HookRegistry(parent_registry)  # type: ignore[attr-defined]

    hookable_methods = discover_hookable_methods(manager_class)

    for method_name in hookable_methods:
        if method_name not in manager_class._hook_registry.hooks:  # type: ignore[attr-defined]
            manager_class._hook_registry.hooks[method_name] = {  # type: ignore[attr-defined]
                "before": [],
                "after": [],
            }


def _should_execute_hook(hook_info: Dict[str, Any], context: HookContext) -> bool:
    """
    Check if hook should execute based on condition.

    Args:
        hook_info: Hook information dictionary
        context: Hook execution context

    Returns:
        True if hook should execute, False otherwise
    """
    condition = hook_info.get("condition")
    if condition is None:
        return True

    try:
        return condition(context)  # type: ignore[no-any-return]
    except Exception as e:
        logger.error(f"Hook condition failed: {e}")
        return False


def wrap_method_with_hooks(
    manager_class: Type["AbstractBLLManager"], method_name: str
) -> Callable:
    """
    Wrap a method to support hook execution.

    Args:
        manager_class: The manager class containing the method
        method_name: Name of the method to wrap

    Returns:
        Wrapped method that executes hooks
    """
    original_method = getattr(manager_class, method_name)

    def wrapped_method(self: "AbstractBLLManager", *args, **kwargs):
        # Auto-populate target_id if present and method uses id
        if hasattr(self, "target_id") and self.target_id and "id" not in kwargs:
            if method_name in ["get", "update", "delete"] and not args:
                kwargs["id"] = self.target_id

        context = HookContext(self, method_name, args, kwargs, timing=HookTiming.BEFORE)  # type: ignore[var-annotated]

        # Execute before hooks
        hooks = (
            self._hook_registry.get_hooks(method_name)  # type: ignore[attr-defined]
            if hasattr(self.__class__, "_hook_registry")
            else {"before": [], "after": []}
        )

        for hook_info in hooks["before"]:
            if _should_execute_hook(hook_info, context):
                try:
                    hook_info["func"](context)
                    # Update kwargs with any modifications from the hook
                    kwargs.update(context.kwargs)
                except Exception as e:
                    # Item 22: BEFORE hooks default blocking=True (security
                    # and validation must fail loudly). Authors that
                    # explicitly opt out via blocking=False get the
                    # log+metric+continue path.
                    if _hook_blocking(hook_info, default=True):
                        logger.error(
                            f"Error in before hook {hook_info['func'].__name__}: {e}"
                        )
                        raise
                    _emit_non_blocking_failure_metric(
                        method_name=method_name,
                        timing="before",
                        hook_func=hook_info["func"],
                        error=e,
                    )

        # Check if we should skip the original method
        if context.skip_execution:
            result = context.modified_result
        else:
            # Call the original method with potentially modified arguments
            result = original_method(self, *context.args, **kwargs)

        # Update context with result for after hooks
        context.result = result
        context.timing = HookTiming.AFTER

        # Execute after hooks
        for hook_info in hooks["after"]:
            if _should_execute_hook(hook_info, context):
                try:
                    import asyncio

                    func = hook_info["func"]
                    if asyncio.iscoroutinefunction(func):
                        call_async_without_waiting(func(context))
                    else:
                        func(context)

                    # Check if hook modified the result
                    if context.modified_result is not None:
                        result = context.modified_result
                except Exception as e:
                    # Item 22: AFTER hooks default blocking=False; observers
                    # should not break the operation they are observing.
                    # Authors that opt in via blocking=True get the
                    # propagation path.
                    if _hook_blocking(hook_info, default=False):
                        logger.error(
                            f"Error in after hook {hook_info['func'].__name__}: {e}"
                        )
                        raise
                    logger.error(
                        f"Error in after hook {hook_info['func'].__name__}: {e}"
                    )
                    _emit_non_blocking_failure_metric(
                        method_name=method_name,
                        timing="after",
                        hook_func=hook_info["func"],
                        error=e,
                    )

        return result

    def run_async_in_thread(coroutine):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coroutine)

    def call_async_without_waiting(async_function):
        thread = threading.Thread(target=run_async_in_thread, args=(async_function,))
        thread.daemon = (
            True  # Allows the program to exit even if the thread is still running
        )
        thread.start()

    # Preserve method metadata
    wrapped_method.__name__ = method_name
    wrapped_method.__doc__ = original_method.__doc__
    wrapped_method._original_method = original_method  # type: ignore[attr-defined]
    # Forward framework-decorator markers so router/SDK/test discovery
    # walking ``vars(manager_cls)`` finds the same metadata as on the
    # raw method. Without this, ``@custom_route`` and ``@rate_limit``
    # tags vanish through hook wrapping and the route never registers.
    for marker in (
        "__custom_route_spec__",
        "_static_route_config",
        "_rate_limit_spec",
        "_rate_limit_count",
        "_rate_limit_window_seconds",
        "_rate_limit_scope",
    ):
        if hasattr(original_method, marker):
            setattr(wrapped_method, marker, getattr(original_method, marker))

    return wrapped_method


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
            from zephyrex.lib.Pydantic2SQLAlchemy import DatabaseMixin

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


T = TypeVar("T")  # type: ignore[misc]
DtoT = TypeVar("DtoT")
ModelT = TypeVar("ModelT")


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


class _BoundModelDescriptor:
    """Descriptor providing registry-aware model access for BLL managers."""

    def __get__(self, instance, owner):
        if owner is None:
            return self

        model = getattr(owner, "_model", None)
        if model is None:
            if owner is AbstractBLLManager:
                return None
            raise AttributeError(f"{owner.__qualname__} does not have _model set!")

        if instance is None:
            return model

        return instance.model_registry.apply(model)


_entity_cache = None


def set_entity_cache(cache) -> None:
    """Install the Valkey entity cache. Called by ``EXT_DatabaseMemory.wire_framework_backends()``."""
    global _entity_cache
    _entity_cache = cache


def get_entity_cache():
    """Return the active entity cache, or None."""
    return _entity_cache


def _cache_sync_run(coro):
    """Drive an async cache coroutine from sync BLL code."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=2)
    return asyncio.run(coro)


class AbstractBLLManager(ABC, Generic[ModelT]):
    _model = None

    # Human-readable label for 404 messages (defaults to class name if None)
    _entity_label: ClassVar[Optional[str]] = None

    _cache_disabled: ClassVar[bool] = False
    _cache_index_fields: ClassVar[Set[str]] = set()
    _cache_ttl: ClassVar[Optional[int]] = None
    _response_cache_ttl: ClassVar[Optional[int]] = None
    _response_cache_vary_on_user: ClassVar[bool] = True

    # Search transformer functions
    search_transformers: Dict[str, Callable] = {}

    # Router configuration - can be overridden by subclasses
    endpoint_config: ClassVar[Dict[str, Any]] = {}
    custom_routes: ClassVar[List[Dict[str, Any]]] = []
    nested_resources: ClassVar[Dict[str, Any]] = {}
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {}

    # Manager factory configuration - can be overridden by subclasses
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = None
    requires_root_access: ClassVar[bool] = False

    # Static class-level access, must have `model_registry.apply()` run on to be registry-sensitive.
    # Instance and class-level access via descriptor. Instances receive registry-bound models,
    # while class-level access returns the raw model for validation helpers (e.g., Model.Create).
    Model = _BoundModelDescriptor()

    @classproperty
    def BaseModel(cls):
        return cls.Model

    # Instance object-level database entity, will automatically have the instance's model_registry and DB context applied before return.
    @property
    def DB(self):
        """Property that returns the SQLAlchemy model class from the Pydantic Model."""
        return self.Model.DB(self.model_registry.DB.manager.Base)

    def __init_subclass__(cls, **kwargs):
        """
        Automatically set up hooks when subclass is created.

        This method is called when a class inherits from AbstractBLLManager
        and sets up the hook system for the new class.
        """
        super().__init_subclass__(**kwargs)

        # Set up class-specific hook registry
        parent_registry = None
        for base in cls.__bases__:
            if hasattr(base, "_hook_registry"):
                parent_registry = base._hook_registry
                break

        cls._hook_registry = HookRegistry(parent_registry)

        # Auto-discover and wrap hookable methods
        auto_register_hooks(cls)

        # Wrap all hookable methods
        hookable_methods = discover_hookable_methods(cls)
        for method_name in hookable_methods:
            if hasattr(cls, method_name) and not hasattr(
                getattr(cls, method_name), "_original_method"
            ):
                wrapped = wrap_method_with_hooks(cls, method_name)
                setattr(cls, method_name, wrapped)

    def __init__(
        self,
        model_registry=None,
        requester_id: Optional[str] | None = None,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        parent: Optional[Any] | None = None,
    ):
        """
        Initialize the BLL manager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team (kept for backward compatibility)
            parent: Parent manager for nested operations (optional)
            model_registry: ModelRegistry instance for accessing registry-bound models (required)
        """
        self.model_registry = model_registry
        self.requester_id = requester_id
        self.target_id: Optional[str] = target_id
        self.target_team_id: Optional[str] = target_team_id
        self._target_user = None
        self._target_team = None
        self._target = None
        self._target_loaded = False
        self._parent = parent
        self.requester = None

        minimal_registry = not model_registry or not hasattr(
            model_registry, "is_committed"
        )

        if minimal_registry:
            self._register_search_transformers()
            return

        if not requester_id:
            raise HTTPException(status_code=400, detail="requester_id is required")

        if not model_registry.is_committed():
            raise ValueError(
                f"model_registry is required to be defined and committed in {self.__class__.__name__}."
            )

        from zephyrex.logic.BLL_Auth import TeamModel, UserModel

        cache = _entity_cache
        if cache is not None:
            try:
                cached_user = _cache_sync_run(cache.get_by_id("user", requester_id))
                if cached_user is not None:
                    self.requester = UserModel.model_validate(cached_user)
                    self._register_search_transformers()
                    return
            except Exception:
                pass

        Team = TeamModel.DB(self.model_registry.DB.manager.Base)
        User = UserModel.DB(self.model_registry.DB.manager.Base)

        session = self.model_registry.DB.session()
        try:
            self.requester = session.query(User).filter(User.id == requester_id).first()
        finally:
            try:
                session.close()
            except Exception as exc:
                logger.debug(
                    "Failed to close requester lookup session in %s: %s",
                    self.__class__.__name__,
                    exc,
                )
        if self.requester is None:
            raise HTTPException(
                status_code=404,
                detail=f"Requesting user with id {requester_id} not found.",
            )

        if cache is not None:
            try:
                dto_dict = (
                    self.requester.model_dump(mode="json")
                    if hasattr(self.requester, "model_dump")
                    else obj_to_dict(self.requester)
                )
                _cache_sync_run(
                    cache.put(
                        "user",
                        requester_id,
                        dto_dict,
                        {f: dto_dict.get(f) for f in ("email",) if dto_dict.get(f)},
                    )
                )
            except Exception:
                pass
        # Initialize any search transformers
        self._register_search_transformers()

    def _update_models_from_registry(self):
        """
        Update the class Model attributes to use registry-bound models with extensions.

        This method finds the registry-bound version of the manager's model and updates
        the class attributes so all methods use the extended models.
        """
        if not self.model_registry or not self.model_registry.is_committed():
            return

        # Get the manager's model class name (e.g., "UserModel" from UserManager)
        manager_name = self.__class__.__name__
        if manager_name.endswith("Manager"):
            model_name = manager_name[:-7] + "Model"  # Remove "Manager", add "Model"
        else:
            # Fallback: try to infer from the existing Model attribute
            model_name = (
                self.Model.__name__ if hasattr(self.Model, "__name__") else None
            )

        if not model_name:
            logger.debug(f"Could not determine model name for manager {manager_name}")
            return

        # Find the registry-bound model with the same name and module
        for bound_model in self.model_registry.bound_models:
            if (
                bound_model.__name__ == model_name
                and bound_model.__module__ == self.Model.__module__
            ):
                # Update the class attributes to use the registry-bound model
                self.__class__._model = bound_model
                logger.debug(
                    f"Updated {manager_name}.Model to use registry-bound {model_name}"
                )

                # Update Reference and Network models using the new programmatic approach
                if hasattr(bound_model, "Reference"):
                    self.__class__.ReferenceModel = bound_model.Reference
                    logger.debug(
                        f"Updated {manager_name}.ReferenceModel to use {model_name}.Reference"
                    )

                # For models with NetworkMixin, they have NetworkModel method
                if hasattr(bound_model, "NetworkModel"):
                    # Resolve the NetworkModel immediately since we have model_registry available
                    try:
                        resolved_network = bound_model.NetworkModel(self.model_registry)
                        self.__class__.NetworkModel = resolved_network
                        logger.debug(
                            f"Updated {manager_name}.NetworkModel to use resolved {model_name}.NetworkModel"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to resolve NetworkModel for {model_name}: {e}, keeping function reference"
                        )

                break

        # Also check if NetworkModel is still a callable and needs resolution
        # This handles cases where NetworkModel was set in __init_subclass__ as Model.NetworkModel
        if (
            callable(self.__class__.NetworkModel)
            and self.model_registry
            and hasattr(self.__class__.NetworkModel, "__name__")
            and self.__class__.NetworkModel.__name__ == "NetworkModel"
        ):
            try:
                resolved_network = self.__class__.NetworkModel(self.model_registry)
                self.__class__.NetworkModel = resolved_network
                logger.debug(f"Resolved callable NetworkModel for {manager_name}")
            except Exception as e:
                logger.warning(
                    f"Failed to resolve callable NetworkModel for {manager_name}: {e}"
                )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        db_manager = getattr(self.model_registry.DB, "manager", None)
        if db_manager and hasattr(db_manager, "cleanup_thread"):
            try:
                db_manager.cleanup_thread()
            except Exception as exc:
                logger.debug(
                    "Failed to cleanup database thread resources for %s: %s",
                    self.__class__.__name__,
                    exc,
                )

    @property  # type: ignore[no-redef]
    def DB(self):
        """Property that returns the SQLAlchemy model class from the Pydantic Model."""
        return self.Model.DB(self.model_registry.DB.manager.Base)

    @property
    def target(self) -> Any:
        """
        Lazy-loaded target record.

        Returns:
            The target entity record, loaded on first access
        """
        if not self._target_loaded and self.target_id:
            self._target = self.get(id=self.target_id)
            self._target_loaded = True
        return self._target

    @target.setter
    def target(self, value: Any) -> None:
        """
        Set target record and mark as loaded.

        Args:
            value: The target entity to set
        """
        self._target = value
        self._target_loaded = True
        if value and hasattr(value, "id"):
            self.target_id = value.id

    @property
    def target_team(self):
        """
        Get target team.

        Returns:
            The target team entity
        """

        return None

    @property
    def target_user_id(self) -> Optional[str]:
        """
        Get target user ID for backward compatibility.

        Returns:
            The target_id if set, otherwise the requester's ID
        """
        return self.target_id if self.target_id else self.requester.id  # type: ignore[union-attr]

    def _register_search_transformers(self):
        """
        Register custom search transformers for this manager.
        Override this method to register specific search transformers.

        Example:
            self.register_search_transformer('overdue', self._transform_overdue_search)
        """
        pass

    def register_search_transformer(self, field_name: str, transformer: Callable):
        """
        Register a search transformer function for a specific field.

        Args:
            field_name: The name of the field or concept to transform
            transformer: A function that takes a value and returns a list of filter conditions
        """
        self.search_transformers[field_name] = transformer

    def get_field_types(self):
        """Analyzes the Model class to categorize fields by type."""
        string_fields = []
        numeric_fields = []
        date_fields = []
        boolean_fields = []

        all_annotations = get_type_hints(self.model_registry.apply(self.Model))
        # Get all annotations from the model
        for field_name, field_info in all_annotations.items():
            # Handle Optional types
            actual_type = field_info
            origin = get_origin(field_info)

            if origin is Union:
                args = get_args(field_info)
                actual_type = args[0]

            # Categorize by type
            if actual_type == str:
                string_fields.append(field_name)
            elif actual_type in (int, float):
                numeric_fields.append(field_name)
            elif actual_type == bool:
                boolean_fields.append(field_name)
            elif actual_type in (date, datetime):
                date_fields.append(field_name)

        return string_fields, numeric_fields, date_fields, boolean_fields

    def build_search_filters(
        self,
        search_params: Dict[str, Any],
    ) -> List:
        """Build SQLAlchemy filters from search parameters."""
        filters = []
        string_fields, numeric_fields, date_fields, boolean_fields = (
            self.get_field_types()
        )

        for field_name, value in search_params.items():
            # Skip processing None values
            if value is None:
                continue

            # Check if we have a custom transformer for this field
            if field_name in self.search_transformers:
                search_transformer = self.search_transformers[field_name]
                custom_filters = self.search_transformers[field_name](value)

                # apply transformers only if they belong to self manager class
                if hasattr(search_transformer, "__self__"):
                    if self != search_transformer.__self__.__class__:
                        custom_filters = None

                if custom_filters:
                    if isinstance(custom_filters, list):
                        filters.extend(custom_filters)
                    else:
                        filters.append(custom_filters)
                    continue

            # If not a custom field, check if field exists in the model
            if not hasattr(self.DB, field_name):
                continue

            field = getattr(self.DB, field_name)

            # Handle string pattern matching operations
            if field_name in string_fields and isinstance(value, dict):
                field_processed = False

                if "inc" in value and value["inc"] is not None:
                    filters.append(field.ilike(f"%{value['inc']}%"))
                    field_processed = True

                if "sw" in value and value["sw"] is not None:
                    filters.append(field.ilike(f"{value['sw']}%"))
                    field_processed = True

                if "ew" in value and value["ew"] is not None:
                    filters.append(field.ilike(f"%{value['ew']}"))
                    field_processed = True

                if "eq" in value and value["eq"] is not None:
                    filters.append(field == value["eq"])
                    field_processed = True

                if field_processed:
                    continue

            # Handle numeric comparison operators
            elif field_name in numeric_fields and isinstance(value, dict):
                conditions = []

                if "eq" in value and value["eq"] is not None:
                    conditions.append(field == value["eq"])
                if "neq" in value and value["neq"] is not None:
                    conditions.append(field != value["neq"])
                if "lt" in value and value["lt"] is not None:
                    conditions.append(field < value["lt"])
                if "gt" in value and value["gt"] is not None:
                    conditions.append(field.__gt__(value["gt"]))
                if "lteq" in value and value["lteq"] is not None:
                    conditions.append(field <= value["lteq"])
                if "gteq" in value and value["gteq"] is not None:
                    conditions.append(field >= value["gteq"])

                if conditions:
                    filters.append(and_(*conditions))
                    continue

            # Handle date field operations
            elif field_name in date_fields and isinstance(value, dict):
                conditions = []

                if "before" in value and value["before"] is not None:
                    conditions.append(field.__lt__(value["before"]))
                if "after" in value and value["after"] is not None:
                    conditions.append(field.__gt__(value["after"]))
                if "eq" in value and value["eq"] is not None:
                    # eq for dates should behave like "on" - match the entire day
                    eq_value = value["eq"]
                    if isinstance(eq_value, datetime):
                        start_of_day = eq_value.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        start_of_next_day = start_of_day + timedelta(days=1)
                        conditions.append(
                            and_(field >= start_of_day, field < start_of_next_day)
                        )
                    elif isinstance(eq_value, date):
                        start_of_day = datetime.combine(eq_value, time.min)
                        start_of_next_day = datetime.combine(
                            eq_value + timedelta(days=1), time.min
                        )
                        conditions.append(
                            and_(field >= start_of_day, field < start_of_next_day)
                        )
                    else:
                        conditions.append(field == eq_value)
                if "on" in value and value["on"] is not None:
                    # For date equality, check for the entire day
                    # Check for SQLAlchemy date/datetime types
                    from sqlalchemy import Date, DateTime

                    if isinstance(field.type, (DateTime, Date)) or str(
                        field.type
                    ).upper() in ["DATETIME", "DATE"]:
                        on_value = value["on"]

                        # Convert string to date object if needed
                        if isinstance(on_value, str):
                            try:
                                if "T" in on_value:
                                    # Parse ISO datetime string
                                    on_datetime = datetime.fromisoformat(
                                        on_value.replace("Z", "+00:00")
                                    )
                                    on_date = on_datetime.date()
                                else:
                                    # Parse date string
                                    on_date = datetime.strptime(
                                        on_value, "%Y-%m-%d"
                                    ).date()
                            except ValueError:
                                continue
                        elif isinstance(on_value, datetime):
                            on_date = on_value.date()
                        elif isinstance(on_value, date):
                            on_date = on_value
                        else:
                            continue

                        # Create datetime objects for start and end of the day
                        start_of_day = datetime.combine(on_date, time.min)
                        # Use start of the *next* day for the upper bound (exclusive)
                        start_of_next_day = datetime.combine(
                            on_date + timedelta(days=1), time.min
                        )

                        conditions.append(
                            and_(field >= start_of_day, field < start_of_next_day)
                        )

                    else:
                        # Fallback for unexpected field types
                        conditions.append(field == value["on"])

                if conditions:
                    filters.append(and_(*conditions))
                    continue

            # Handle boolean field operations
            elif field_name in boolean_fields and isinstance(value, dict):
                if "eq" in value and value["eq"] is not None:
                    filters.append(field == value["eq"])
                    continue

            # For dictionaries that weren't handled by specific patterns,
            # extract the actual values rather than passing the dict directly
            if isinstance(value, dict):
                # Skip dictionaries that don't match our expected patterns
                continue

            # Handle direct value syntax for date fields (should behave like "on")
            if field_name in date_fields:
                # Parse string dates if needed
                parsed_value = value
                if isinstance(value, str):
                    try:
                        parsed_value = datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        )
                    except ValueError:
                        try:
                            parsed_value = date.fromisoformat(value)
                        except ValueError as e:
                            logger.warning(
                                f"Unparseable date filter value {value!r}: {e}"
                            )

                # Check if value is a datetime or date
                if isinstance(parsed_value, datetime):
                    # For datetime values, truncate microseconds and match exact second
                    truncated_value = parsed_value.replace(microsecond=0)
                    # Create a range from start to end of the second to handle microsecond differences
                    start_of_second = truncated_value
                    end_of_second = truncated_value + timedelta(seconds=1)
                    filters.append(
                        and_(field >= start_of_second, field < end_of_second)
                    )
                elif isinstance(parsed_value, date):
                    # For date values, create datetime range for the day
                    start_of_day = datetime.combine(parsed_value, time.min)
                    start_of_next_day = datetime.combine(
                        parsed_value + timedelta(days=1), time.min
                    )
                    filters.append(
                        and_(field >= start_of_day, field < start_of_next_day)
                    )
                else:
                    # Fallback for other types
                    filters.append(field == value)
            # Handle direct value syntax for boolean fields
            elif field_name in boolean_fields:
                # Direct boolean values should work the same as is_true
                filters.append(field == value)
            else:
                # Handle regular exact match (for non-dict values)
                filters.append(field == value)

        return filters

    def _parse_includes(self, include: Union[List[str], str]) -> List[str]:
        """Parse includes parameter into a list of relationship names.

        Args:
            include: List of relationships or CSV string of relationships

        Returns:
            List of relationship names with validation
        """
        if not include:
            return []

        if isinstance(include, str):
            # Handle CSV string - split on commas and strip whitespace
            include_list = [name.strip() for name in include.split(",") if name.strip()]
        elif isinstance(include, list):
            # Handle list - ensure all items are strings and not empty
            include_list = [str(name).strip() for name in include if str(name).strip()]
        else:
            # Invalid type
            return []

        MAX_INCLUDE_DEPTH = 4

        validated_includes = []
        for include_name in include_list:
            if include_name and all(c.isalnum() or c in "._" for c in include_name):
                depth = include_name.count(".") + 1
                if depth <= MAX_INCLUDE_DEPTH:
                    validated_includes.append(include_name)

        return validated_includes

    def _parse_fields(self, fields: Union[List[str], str]) -> List[str]:
        """Parse fields parameter into a list of field names.

        Args:
            fields: List of field names or CSV string of field names

        Returns:
            List of field names with validation
        """
        if not fields:
            return []

        if isinstance(fields, str):
            # Handle CSV string - split on commas and strip whitespace
            fields_list = [name.strip() for name in fields.split(",") if name.strip()]
        elif isinstance(fields, list):
            # Handle list - ensure all items are strings and not empty
            fields_list = [str(name).strip() for name in fields if str(name).strip()]
        else:
            # Invalid type
            return []

        # Validate field names (basic validation for now)
        validated_fields = []
        for field_name in fields_list:
            # Basic validation: ensure it contains only alphanumeric and underscore characters
            if field_name and all(c.isalnum() or c == "_" for c in field_name):
                validated_fields.append(field_name)

        return validated_fields

    def validate_fields(
        self, fields: Optional[Union[List[str], str]]
    ) -> Optional[List[str]]:
        """
        Validate that requested fields exist in the model.
        Returns the processed fields list.
        Raises HTTPException 422 if invalid fields are provided.

        Args:
            fields: List of field names or CSV string of field names

        Returns:
            Processed list of valid field names, or None/empty list if no fields provided

        Raises:
            HTTPException: 422 status if invalid fields are detected
        """
        if not fields:
            return fields  # type: ignore[return-value]

        # Parse fields - handle both CSV strings and lists
        fields_list = self._parse_fields(fields)

        if not fields_list:
            return fields_list

        # Get valid field names from the model
        valid_fields = set(self.Model.model_fields.keys())

        # Check for invalid fields
        provided_fields = set(fields_list)
        invalid_fields = provided_fields - valid_fields

        if invalid_fields:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Invalid fields provided",
                    "invalid_fields": sorted(list(invalid_fields)),
                    "valid_fields": sorted(list(valid_fields)),
                },
            )

        return fields_list

    def validate_includes(
        self, includes: Optional[Union[List[str], str]]
    ) -> Optional[List[str]]:
        """
        Validate that requested includes exist as valid relationships for the model.

        This is a lightweight wrapper that uses generate_joins() for validation
        without actually generating the join options.
        """
        if not includes:
            return includes  # type: ignore[return-value]

        includes_list = self._parse_includes(includes)

        if not includes_list:
            return includes_list

        # Use generate_joins() for validation - it will raise HTTPException if invalid
        # We discard the result since we only care about validation here
        try:
            self.generate_joins(self.DB, includes_list)
        except HTTPException:
            # Re-raise the 422 error from generate_joins
            raise

        return includes_list

    def _resolve_load_only_columns(self, fields_list: List[str]) -> List[Any]:
        """Resolve field names to SQLAlchemy load_only compatible attributes."""
        mapper = getattr(self.DB, "__mapper__", None)
        if not mapper:
            return []

        mapper_attrs = getattr(mapper, "attrs", {})
        mapper_keys = (
            set(mapper_attrs.keys()) if hasattr(mapper_attrs, "keys") else set()
        )
        column_field_keys: Set[str] = set()
        if hasattr(mapper, "column_attrs"):
            try:
                column_field_keys = {prop.key for prop in mapper.column_attrs}
            except Exception:
                column_field_keys = set()
        relationship_keys: Set[str] = set()
        if hasattr(mapper, "relationships"):
            try:
                relationship_keys = set(mapper.relationships.keys())
            except Exception:
                relationship_keys = set()

        if not column_field_keys and mapper_keys:
            # Fallback: treat mapper attribute keys that are not relationships as columns
            column_field_keys = {
                key for key in mapper_keys if key not in relationship_keys
            }

        resolved: List[Any] = []
        invalid: List[str] = []
        seen: Set[str] = set()

        for field_name in fields_list:
            if field_name in mapper_keys and hasattr(self.DB, field_name):
                if field_name in column_field_keys:
                    if field_name not in seen:
                        resolved.append(getattr(self.DB, field_name))
                        seen.add(field_name)
                elif field_name in relationship_keys:
                    continue
                else:
                    invalid.append(field_name)
            else:
                invalid.append(field_name)

        # Ensure core audit fields remain available for DTO validation
        required_field_names = [
            "id",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
        ]

        for required_name in required_field_names:
            if (
                required_name in mapper_keys
                and required_name in column_field_keys
                and hasattr(self.DB, required_name)
                and required_name not in seen
            ):
                resolved.append(getattr(self.DB, required_name))
                seen.add(required_name)

        # Ensure required fields from the Pydantic model remain available
        model_required_fields: Set[str] = set()
        model_class = getattr(self, "Model", None)
        model_fields = getattr(model_class, "model_fields", {}) if model_class else {}
        for field_name, field_info in model_fields.items():
            if hasattr(field_info, "is_required") and callable(field_info.is_required):
                if field_info.is_required():
                    model_required_fields.add(field_name)

        for required_name in model_required_fields:
            if (
                required_name in mapper_keys
                and required_name in column_field_keys
                and hasattr(self.DB, required_name)
                and required_name not in seen
            ):
                resolved.append(getattr(self.DB, required_name))
                seen.add(required_name)

        if invalid:
            raise ValueError(
                f"Invalid fields for {self.DB.__name__}: {', '.join(invalid)}"
            )

        return resolved

    @staticmethod
    def generate_joins(model_class, include_fields):
        """Generate join loads based on specified include fields.

        Args:
            model_class: SQLAlchemy model class
            include_fields: List of relationship names, supports dot notation for nested relationships

        Returns:
            List of SQLAlchemy joinedload options
        """
        """Generate join loads based on specified include fields."""
        from sqlalchemy.orm import RelationshipProperty
        from zephyrex.lib.Logging import logger
        from fastapi import HTTPException, status
        from zephyrex.lib.Logging import logger

        joins = []
        invalid_includes = []
        valid_relationships = []

        # Collect all valid relationships - try multiple detection methods
        try:
            # Method 1: Check __mapper__ (SQLAlchemy 1.x and 2.x)
            if hasattr(model_class, "__mapper__"):
                mapper = model_class.__mapper__
                if hasattr(mapper, "relationships"):
                    for rel_name in mapper.relationships.keys():
                        valid_relationships.append(rel_name)
        except Exception as e:
            logger.debug(f"Could not get relationships from __mapper__: {e}")

        # Method 2: Check via dir() and property inspection (fallback)
        if not valid_relationships:
            for attr_name in dir(model_class):
                if attr_name.startswith("_"):
                    continue
                try:
                    attr = getattr(model_class, attr_name)
                    # Check if it's a SQLAlchemy relationship
                    if hasattr(attr, "property"):
                        if isinstance(attr.property, RelationshipProperty):
                            valid_relationships.append(attr_name)
                        elif hasattr(attr.property, "mapper"):
                            valid_relationships.append(attr_name)
                except Exception:
                    continue

        # Remove duplicates
        valid_relationships = sorted(list(set(valid_relationships)))

        # Helper: resolve a single attribute name to an actual relationship attribute
        def _resolve_relationship_attribute(cls, name):
            """Try to resolve a relationship attribute on cls for the given include name.

            Resolution strategy:
            1. If attribute exists on the class and is a relationship, return it.
            2. Inspect SQLAlchemy mapper relationships and try to match by relationship key.
            3. If not found, look for a relationship whose local FK column matches '{name}_id'.
            Returns the attribute descriptor or None.
            """
            # 1) direct attribute check
            try:
                if hasattr(cls, name):
                    candidate = getattr(cls, name)
                    if hasattr(candidate, "property") and hasattr(
                        candidate.property, "mapper"
                    ):
                        return candidate
            except Exception:
                # fall through to mapper-based resolution
                pass

            # 2) mapper-based resolution
            try:
                from sqlalchemy.inspection import inspect as sa_inspect

                mapper = sa_inspect(cls)
                # Try to find relationship by key first
                for rel in mapper.relationships:
                    if rel.key == name:
                        return getattr(cls, rel.key)

                # 3) Try to match by local foreign key column name (e.g., created_by_user -> created_by_user_id)
                fk_name = f"{name}_id"
                for rel in mapper.relationships:
                    # rel.local_columns is a set of Column objects
                    for col in getattr(rel, "local_columns", set()):
                        try:
                            col_name = getattr(col, "name", getattr(col, "key", None))
                        except Exception:
                            col_name = None
                        if col_name == fk_name or col_name == name:
                            return getattr(cls, rel.key)
                # 4) If no relationship found, but there is a FK column named fk_name, attempt to create a dynamic relationship
                #    that points to the referenced table's model. This creates a view-only relationship on the class
                #    so joinedload can be used for includes like 'created_by_user' when only '<name>_id' exists.
                try:
                    # Try to access table column object
                    col_obj = None
                    table = getattr(cls, "__table__", None)
                    if table is not None and fk_name in table.c:
                        col_obj = table.c[fk_name]
                    else:
                        # Try InstrumentedAttribute on class
                        candidate = getattr(cls, fk_name, None)
                        if candidate is not None and hasattr(candidate, "property"):
                            # attempt to pull column from descriptor
                            cols = getattr(candidate.property, "columns", None)
                            if cols:
                                col_obj = list(cols)[0]

                    if col_obj is not None and getattr(col_obj, "foreign_keys", None):
                        # Get referenced table name from the first FK
                        fk_iter = iter(col_obj.foreign_keys)
                        first_fk = next(fk_iter, None)
                        if first_fk is not None and hasattr(first_fk, "column"):
                            ref_table = getattr(first_fk.column, "table", None)
                            ref_table_name = getattr(ref_table, "name", None)
                            if ref_table_name:
                                try:
                                    import stringcase
                                    from zephyrex.lib.Environment import inflection

                                    # Derive candidate class names (likely Pydantic model names -> SQLAlchemy model classes)
                                    singular = (
                                        inflection.singular_noun(ref_table_name)
                                        if hasattr(inflection, "singular_noun")
                                        else None
                                    )
                                    if not singular:
                                        # fallback: strip trailing 's' if present
                                        singular = (
                                            ref_table_name[:-1]
                                            if ref_table_name.endswith("s")
                                            else ref_table_name
                                        )

                                    candidate_class = (
                                        stringcase.pascalcase(singular) + "Model"
                                    )
                                    # Create a view-only relationship using the candidate class name string
                                    from sqlalchemy.orm import (
                                        relationship as sa_relationship,
                                    )

                                    rel_attr = sa_relationship(
                                        candidate_class,
                                        foreign_keys=[getattr(cls, fk_name)],
                                        viewonly=True,
                                    )
                                    setattr(cls, name, rel_attr)
                                    return getattr(cls, name)
                                except Exception:
                                    # If dynamic relationship creation fails, ignore and continue
                                    pass
                except Exception:
                    # ignore any errors in dynamic relationship creation
                    pass
            except Exception:
                pass

            return None

        for field in include_fields:
            try:
                # Handle nested includes (e.g., 'user_teams.team.roles')
                if "." in field:
                    parts = field.split(".")

                    # Resolve first part to an attribute (relationship)
                    first_attr = _resolve_relationship_attribute(model_class, parts[0])
                    if not first_attr:
                        logger.warning(
                            f"Relationship '{parts[0]}' not found on {model_class.__name__}"
                        )
                        continue

                    current_join = joinedload(first_attr)
                    # Drill down into nested model class
                    try:
                        current_model_class = first_attr.property.mapper.class_
                    except Exception:
                        logger.warning(
                            f"Could not resolve mapper for relationship '{parts[0]}' on {model_class.__name__}"
                        )
                        continue

                    for part in parts[1:]:
                        nested_attr = _resolve_relationship_attribute(
                            current_model_class, part
                        )
                        if (
                            nested_attr
                            and hasattr(nested_attr, "property")
                            and hasattr(nested_attr.property, "mapper")
                        ):
                            current_join = current_join.joinedload(nested_attr)
                            current_model_class = nested_attr.property.mapper.class_
                        else:
                            logger.warning(
                                f"Relationship '{part}' not found on {current_model_class.__name__}"
                            )
                            break
                    else:
                        joins.append(current_join)

                else:
                    # Simple include - try to resolve to a relationship attribute
                    attr = _resolve_relationship_attribute(model_class, field)
                    if attr is not None:
                        joins.append(joinedload(attr))
                    else:
                        logger.warning(
                            f"Relationship '{field}' not found on {model_class.__name__}"
                        )

            except (AttributeError, TypeError) as e:
                logger.warning(
                    f"Error processing include field '{field}' on {model_class.__name__}: {e}"
                )
                continue

        return joins

    @property
    def db(self) -> Session:
        """Property that returns an active database session from ModelRegistry."""
        return self.model_registry.DB.session()  # type: ignore[no-any-return]

    def create_validation(self, entity):
        """Override this method to add validation logic for entity creation."""
        pass

    def update_validation(self, entity):
        """Override this method to add validation logic for entity update."""
        pass

    def delete_validation(self, entity):
        """Override this method to add validation logic for entity deletion."""
        pass

    def search_validation(self, params):
        """Override this method to add validation logic for entity search."""
        pass

    def create(self, **kwargs) -> ModelT:
        """Create one or more entities."""
        # Handle single entity or list of entities
        if "entities" in kwargs and isinstance(kwargs["entities"], list):
            entities = kwargs.pop("entities")
            results = []
            for entity_data in entities:
                # Merge entity data with remaining kwargs
                entity_kwargs = {**kwargs, **entity_data}
                results.append(self._create_single_entity(**entity_kwargs))
            return results
        else:
            return self._create_single_entity(**kwargs)

    # Fields a client must never be able to set on Create/Update bodies.
    # The server is the sole authority on identity and audit timestamps —
    # honouring them from the request would let a malicious or sloppy
    # caller spoof ownership and break the audit trail.
    _SERVER_CONTROLLED_AUDIT_FIELDS: ClassVar[tuple] = (
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "created_by_user_id",
        "updated_by_user_id",
        "deleted_by_user_id",
    )

    # Per-manager opt-in: fields whose value must equal ``self.requester.id``
    # when supplied by a non-root caller. Subclasses override to lock down
    # ownership-claiming fields exposed in their ``Create`` schemas (e.g.
    # ``user_id`` on a notification). ROOT_ID and SYSTEM_ID may set these
    # fields freely so administrative imports keep working.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ()

    @classmethod
    def _strip_server_controlled_fields(cls, kwargs: Dict[str, Any]) -> None:
        """Remove audit/identity fields a client should never be able to set."""
        for banned in cls._SERVER_CONTROLLED_AUDIT_FIELDS:
            kwargs.pop(banned, None)

    def _enforce_caller_owned_fields(self, kwargs: Dict[str, Any]) -> None:
        """Reject Create/Update calls that try to claim another user's ID.

        For each field in ``_CALLER_OWNED_FIELDS``: if the caller supplied
        a value that is not the requester's own id, the call is rejected
        with 403. ROOT_ID and SYSTEM_ID bypass this check so server-side
        imports and admin tooling can still set arbitrary owners.

        ``None`` values pass through (they fall back to the default
        target-id resolution path).
        """
        if not self._CALLER_OWNED_FIELDS:
            return
        try:
            from zephyrex.database.StaticPermissions import (
                is_root_id,
                is_system_id,
            )
        except ImportError:
            is_root_id = lambda _id: False  # noqa: E731
            is_system_id = lambda _id: False  # noqa: E731
        requester_id = getattr(self.requester, "id", None)
        if is_root_id(requester_id) or is_system_id(requester_id):
            return
        for field in self._CALLER_OWNED_FIELDS:
            supplied = kwargs.get(field)
            if supplied is None:
                continue
            if supplied != requester_id:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Cannot set {field}={supplied!r}: callers may only "
                        f"claim ownership for themselves"
                    ),
                )

    def _create_single_entity(self, **kwargs) -> Any:
        """Create a single entity."""
        # Store original kwargs to preserve hook modifications
        # NOTE: server-controlled audit fields are stripped before this copy
        # so hooks cannot accidentally re-introduce a client-supplied id or
        # spoofed created_by_user_id.
        self._strip_server_controlled_fields(kwargs)
        self._enforce_caller_owned_fields(kwargs)
        original_kwargs = kwargs.copy()

        args = self.model_registry.apply(self.Model).Create(**kwargs)
        self.create_validation(args)

        # Convert arguments to dictionary, excluding unset values
        create_args = {
            k: v
            for k, v in args.model_dump(exclude_unset=True).items()
            if v is not None or k == "user_id"  # Keep user_id even if None
        }

        # **CRITICAL**: Preserve hook-modified arguments that may not be in the Pydantic schema
        # This ensures attributes like 'hook_processed' added by hooks are preserved
        for key, value in original_kwargs.items():
            if key not in create_args and not hasattr(
                self.model_registry.apply(self.Model).Create, key
            ):
                # Skip hook-related parameters that shouldn't be passed to database
                if key in ["hook_processed"]:
                    continue
                # Only add if it's not already in create_args and not a valid Pydantic field
                # This preserves hook additions while avoiding conflicts
                create_args[key] = value

        # Check if the database class has a user_id column and add target_id if it does
        # Only add user_id if it wasn't explicitly set in the original kwargs
        if hasattr(self.DB, "user_id") and "user_id" not in kwargs:
            create_args["user_id"] = self.target_id

        entity = self.DB.create(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(self.Model),
            **create_args,
        )

        cache = _entity_cache
        if cache is not None and not self._cache_disabled and entity is not None:
            try:
                dto_dict = (
                    entity.model_dump(mode="json")
                    if hasattr(entity, "model_dump")
                    else obj_to_dict(entity)
                )
                eid = dto_dict.get("id")
                if eid:
                    index_vals = {
                        f: dto_dict.get(f)
                        for f in self._cache_index_fields
                        if f in dto_dict and dto_dict.get(f)
                    }
                    _cache_sync_run(
                        cache.put(
                            self.DB.__tablename__, eid, dto_dict, index_vals or None
                        )
                    )
            except Exception:
                pass

        return entity

    def get(
        self,
        include: Optional[Union[List[str], str]] | None = None,
        fields: Optional[Union[List[str], str]] | None = None,
        **kwargs,
    ) -> ModelT:
        """Get an entity with optional included relationships.

        Validates fields/includes, generates join options, delegates to
        ``DB.get``, and raises 404 when the record is not found.  Subclasses
        that need only pre- or post-processing can call ``super().get()``
        instead of reimplementing the whole pipeline.

        The 404 detail uses ``_entity_label`` (falls back to the class name).

        Args:
            include: List of relationships to include, or CSV string of relationships.
                    Supports nested relationships with dot notation (e.g., 'user_teams.team.roles')
            fields: List of specific fields to include in response, or CSV string of field names
            **kwargs: Additional parameters to pass to the database get method

        Returns:
            Entity with included relationships loaded

        Raises:
            HTTPException: 404 when the entity does not exist
        """
        from fastapi import status

        entity_id = kwargs.get("id")
        cache = _entity_cache
        if (
            cache is not None
            and not self._cache_disabled
            and entity_id
            and not include
            and not fields
        ):
            try:
                cached = _cache_sync_run(
                    cache.get_by_id(self.DB.__tablename__, entity_id)
                )
                if cached is not None:
                    return self.model_registry.apply(self.Model).model_validate(cached)
            except Exception:
                pass

        options = []

        fields_list = self.validate_fields(fields)

        if include:
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)
        if fields_list:
            from sqlalchemy.orm import load_only

            columns = self._resolve_load_only_columns(fields_list)
            if columns:
                options.append(load_only(*columns))

        db_kwargs = {k: v for k, v in kwargs.items() if k not in ["hook_processed"]}

        result = self.DB.get(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.Model,
            options=options,
            **db_kwargs,
        )

        if result is None:
            label = self._entity_label or self.__class__.__name__.replace("Manager", "")
            eid = kwargs.get("id") or next(
                (v for k, v in kwargs.items() if k.endswith("_id")), "unknown"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{label} with ID '{eid}' not found",
            )

        if (
            cache is not None
            and not self._cache_disabled
            and entity_id
            and not include
            and not fields
        ):
            try:
                dto_dict = (
                    result.model_dump(mode="json")
                    if hasattr(result, "model_dump")
                    else obj_to_dict(result)
                )
                index_vals = {
                    f: dto_dict.get(f)
                    for f in self._cache_index_fields
                    if f in dto_dict and dto_dict.get(f)
                }
                _cache_sync_run(
                    cache.put(
                        self.DB.__tablename__, entity_id, dto_dict, index_vals or None
                    )
                )
            except Exception:
                pass

        return result

    def count(self, **kwargs) -> int:
        """Count entities matching the given filters."""
        kwargs.pop("hook_processed", None)
        return self.DB.count(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            **kwargs,
        )

    def list(
        self,
        include: Optional[Union[List[str], str]] | None = None,
        fields: Optional[Union[List[str], str]] | None = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        page_size: Optional[int] | None = None,
        pageSize: Optional[int] | None = None,
        return_type: str = "dto",
        **kwargs,
    ) -> List[ModelT]:
        """List entities with optional included relationships.

        Pagination supports negative page numbers: page=-1 returns the
        last page, page=-2 the second-to-last, etc.
        """
        if pageSize is not None and page_size is None:
            page_size = pageSize
        if page is not None and page_size is not None:
            limit = page_size
            if page < 0:
                total = self.count(**kwargs)
                total_pages = max(1, -(-total // page_size))
                resolved = total_pages + 1 + page
                offset = max(0, (resolved - 1) * page_size)
            else:
                offset = (page - 1) * page_size

        options = []
        order_by = None
        # Separate kwargs for simple filter_by and complex dicts for build_search_filters
        simple_kwargs = {}
        complex_search_params = {}
        for key, value in kwargs.items():
            # Skip hook-related parameters
            if key in ["hook_processed"]:
                continue
            if isinstance(value, dict):
                complex_search_params[key] = value
            else:
                simple_kwargs[key] = value

        if include:
            # Parse includes - handle both CSV strings and lists
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)
        if fields:
            from sqlalchemy.orm import load_only

            fields_list = self.validate_fields(fields)
            if fields_list:
                columns = self._resolve_load_only_columns(fields_list)
                if columns:
                    options.append(load_only(*columns))
        if sort_by:
            from sqlalchemy import asc, desc

            if hasattr(self.DB, sort_by):
                column = getattr(self.DB, sort_by)
                if sort_order.lower() == "asc":  # type: ignore[union-attr]
                    order_by = [asc(column)]
                else:
                    order_by = [desc(column)]
            else:
                valid_fields = set(self.Model.model_fields.keys())
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": f"Invalid sort_by field: '{sort_by}'",
                        "invalid_field": sort_by,
                        "valid_fields": sorted(list(valid_fields)),
                    },
                )

        # Generate filters from complex search_params only
        search_filters = self.build_search_filters(complex_search_params)
        # Combine with any explicitly passed filters
        combined_filters = filters + search_filters if filters else search_filters
        combined_filters = self._normalize_filters(combined_filters)  # type: ignore[assignment]

        self.parent_validation(simple_kwargs)

        return self.DB.list(  # type: ignore[no-any-return]
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type=return_type,
            override_dto=self.model_registry.apply(self.Model),
            options=options,
            order_by=order_by,
            limit=limit,
            offset=offset,
            filters=combined_filters,  # Use combined_filters here
            **simple_kwargs,  # Pass simple_kwargs for filter_by
        )

    def search(
        self,
        include: Optional[Union[List[str], str]] | None = None,
        fields: Optional[Union[List[str], str]] | None = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        page_size: Optional[int] | None = None,
        pageSize: Optional[int] | None = None,
        **search_params,
    ) -> List[ModelT]:
        """Search entities with optional included relationships."""
        if pageSize is not None and page_size is None:
            page_size = pageSize
        if page is not None and page_size is not None:
            limit = page_size
            offset = (page - 1) * page_size

        options = []
        order_by = None
        # Separate kwargs for simple filter_by and complex dicts for build_search_filters
        simple_kwargs = {}
        complex_search_params = {}
        for key, value in search_params.items():
            # Skip hook-related parameters
            if key in ["hook_processed"]:
                continue
            if isinstance(value, dict):
                complex_search_params[key] = value
            else:
                simple_kwargs[key] = value

        self.search_validation(simple_kwargs)

        # Extract and save return_type from simple_kwargs
        return_type = simple_kwargs.pop(
            "return_type", "dto"
        )  # Remove and save return_type

        # Convert include to SQLAlchemy joinedload options
        if include:
            # Parse includes - handle both CSV strings and lists
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)

        # Convert fields to SQLAlchemy load_only option
        if fields:
            from sqlalchemy.orm import load_only

            fields_list = self.validate_fields(fields)
            if fields_list:
                columns = self._resolve_load_only_columns(fields_list)
                if columns:
                    options.append(load_only(*columns))

        # Convert sort_by and sort_order to SQLAlchemy order_by expression
        if sort_by:
            from sqlalchemy import asc, desc

            if hasattr(self.DB, sort_by):
                column = getattr(self.DB, sort_by)
                if sort_order.lower() == "asc":  # type: ignore[union-attr]
                    order_by = [asc(column)]
                else:
                    order_by = [desc(column)]

        # Generate filters from complex search_params only
        search_filters = self.build_search_filters(complex_search_params)
        combined_filters = filters + search_filters if filters else search_filters
        combined_filters = self._normalize_filters(combined_filters)  # type: ignore[assignment]

        # Pass the converted SQLAlchemy constructs to the DBClass.list method
        # Use combined_filters for the 'filters' arg and simple_kwargs for '**kwargs'
        return self.DB.list(  # type: ignore[no-any-return]
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type=return_type,  # Use the saved value instead of hardcoding "dto"
            options=options,
            order_by=order_by,
            limit=limit,
            offset=offset,
            filters=combined_filters,  # Filters from build_search_filters
            **simple_kwargs,  # Simple equality kwargs for filter_by
        )

    def _normalize_filters(self, filters: Optional[List[Any]]) -> Optional[List[Any]]:
        if not filters:
            return filters

        normalized: List[Any] = []
        for filter_condition in filters:
            normalized.append(self._resolve_filter_condition(filter_condition))
        return normalized

    def _resolve_filter_condition(self, filter_condition: Any) -> Any:
        if isinstance(filter_condition, FieldComparison):
            column = getattr(self.DB, filter_condition.field_name)
            value = filter_condition.value
            operator = filter_condition.operator

            if operator == "eq":
                return column.is_(None) if value is None else column == value
            if operator == "ne":
                return column.is_not(None) if value is None else column != value
            if operator == "lt":
                return column < value
            if operator == "le":
                return column <= value
            if operator == "gt":
                return column > value
            if operator == "ge":
                return column >= value
            if operator == "in":
                return column.in_(value)
            if operator == "not_in":
                return (
                    column.not_in(value)
                    if hasattr(column, "not_in")
                    else ~column.in_(value)
                )
            if operator == "like":
                return column.like(value)
            if operator == "ilike":
                return column.ilike(value)
            if operator == "contains":
                return column.contains(value)
            if operator == "startswith":
                return column.startswith(value)
            if operator == "endswith":
                return column.endswith(value)
            if operator == "is":
                return column.is_(value)
            if operator == "isnot":
                return column.is_not(value)

        return filter_condition

    _require_etag: ClassVar[bool] = False

    def _compute_entity_etag(self, entity) -> str:
        """Compute a weak ETag from the entity's updated_at timestamp."""
        import hashlib
        ts = getattr(entity, "updated_at", None) or getattr(entity, "created_at", "")
        return f'W/"{hashlib.sha256(str(ts).encode()).hexdigest()[:16]}"'

    def update(self, id: str, **kwargs) -> ModelT:
        """Update an entity by ID."""
        if_match = kwargs.pop("_if_match", None)

        # Drop audit/identity fields from the inbound payload. Server-managed
        # bookkeeping (updated_at, updated_by_user_id, etc.) must not be
        # client-controllable.
        self._strip_server_controlled_fields(kwargs)
        self._enforce_caller_owned_fields(kwargs)
        logger.debug("Updating entity with ID: %s", id)
        try:
            args = self.model_registry.apply(self.Model).Update(**kwargs)
        except ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={"message": "Validation error", "details": e.errors()},
            )

        # Convert arguments to dictionary, excluding unset values
        update_args = {k: v for k, v in args.model_dump(exclude_unset=True).items()}

        # Get the entity before update (for after hooks)
        entity_before = self.get(id=id)

        if self._require_etag and not if_match:
            raise HTTPException(status_code=428, detail="Precondition Required — If-Match header missing")
        if if_match and entity_before:
            current_etag = self._compute_entity_etag(entity_before)
            if if_match.strip('"') not in current_etag:
                raise HTTPException(status_code=412, detail="Precondition Failed — entity has been modified")

        updated_entity = self.DB.update(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(self.Model),
            new_properties=update_args,
            id=id,
        )

        cache = _entity_cache
        if cache is not None and not self._cache_disabled:
            try:
                table = self.DB.__tablename__
                old_index_vals = {
                    f: getattr(entity_before, f, None) for f in self._cache_index_fields
                }
                _cache_sync_run(
                    cache.invalidate(
                        table,
                        id,
                        {k: v for k, v in old_index_vals.items() if v} or None,
                    )
                )
                if updated_entity is not None:
                    dto_dict = (
                        updated_entity.model_dump(mode="json")
                        if hasattr(updated_entity, "model_dump")
                        else obj_to_dict(updated_entity)
                    )
                    index_vals = {
                        f: dto_dict.get(f)
                        for f in self._cache_index_fields
                        if f in dto_dict and dto_dict.get(f)
                    }
                    _cache_sync_run(cache.put(table, id, dto_dict, index_vals or None))
            except Exception:
                pass

        return updated_entity

    def batch_update(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Update multiple entities in a batch.

        Returns a dict with ``results`` (per-item outcomes) and
        ``errors`` (failed items). When any item fails, the endpoint
        layer returns HTTP 207 Multi-Status with all per-item results
        preserved — successful mutations are never discarded.
        """
        results = []
        errors = []

        for item in items:
            entity_id = item.get("id", "unknown")
            try:
                if not item.get("id"):
                    raise ValueError("Missing required 'id' field in batch update item")
                update_data = item.get("data", {})
                updated_entity = self.update(id=item["id"], **update_data)
                results.append({"id": entity_id, "status": 200, "data": updated_entity})
            except Exception as e:
                status_code = getattr(e, "status_code", 400)
                results.append({"id": entity_id, "status": status_code, "error": str(e)})
                errors.append({"id": entity_id, "error": str(e)})

        return {"results": results, "errors": errors}

    def delete(self, id: str) -> None:
        """Delete an entity by ID."""
        cache = _entity_cache
        if cache is not None and not self._cache_disabled:
            try:
                old = self.get(id=id)
                old_index_vals = {
                    f: getattr(old, f, None) for f in self._cache_index_fields
                }
                _cache_sync_run(
                    cache.invalidate(
                        self.DB.__tablename__,
                        id,
                        {k: v for k, v in old_index_vals.items() if v} or None,
                    )
                )
            except Exception:
                try:
                    _cache_sync_run(cache.invalidate(self.DB.__tablename__, id))
                except Exception:
                    pass

        self.DB.delete(
            requester_id=self.requester.id,  # type: ignore[union-attr]
            model_registry=self.model_registry,
            id=id,
        )

    def batch_delete(self, ids: List[str]) -> Dict[str, Any]:
        """Delete multiple entities in a batch.

        Returns a dict with ``results`` (per-item outcomes) and
        ``errors``. Partial failures return HTTP 207 at the endpoint
        layer — successful deletes are never discarded.
        """
        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for entity_id in ids:
            try:
                self.delete(id=entity_id)
                results.append({"id": entity_id, "status": 204})
            except Exception as e:
                status_code = getattr(e, "status_code", 400)
                results.append({"id": entity_id, "status": status_code, "error": str(e)})
                errors.append({"id": entity_id, "error": str(e)})

        return {"results": results, "errors": errors}

    # checks if parent exists by reference_id
    def parent_validation(self, args):
        """Override this method to add validation logic for parent entities."""
        if self._parent:
            ref_model = self._parent.Model.Reference
            if ref_model:
                parent_class = ref_model.__bases__[0]
                for key in parent_class.__annotations__.keys():
                    if args.get(key) is not None:
                        self._parent.get(id=args[key])
