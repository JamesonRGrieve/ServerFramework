import asyncio
import inspect
import threading
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
)

import numpy as np

from zephyrex.lib.Logging import logger

if TYPE_CHECKING:
    from zephyrex.logic.AbstractLogicManager.manager import AbstractBLLManager


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
