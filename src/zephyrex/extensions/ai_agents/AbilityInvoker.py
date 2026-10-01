"""General, access-controlled ability invocation for agent turns.

Resolves an ``@ability`` by name against the running extension registry and
invokes it under the agent's identity, so a turn can execute registered
abilities as tools.

Access is **default-deny** and enforced *independently of the tool list shown to
the model*:

- an agent may invoke only abilities on its allowlist (``AgentAbility`` rows,
  passed in as ``allowed``);
- an ability on the global never-grantable denylist is refused for every agent,
  regardless of allowlist;
- the requested ability name is re-checked at invocation time, so a hallucinated
  or prompt-injected tool name can never widen access — the offered-tools list is
  never trusted as the authorization.

Tool schemas for the model's ``tools=`` parameter are introspected from each
ability method's signature + docstring, so adding an ability exposes it as a
tool with no changes here.
"""

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union, get_args, get_origin

from zephyrex.lib.Logging import logger

# Parameters that are part of an ability's calling convention rather than model-
# supplied arguments, and so must never appear in a tool's JSON schema.
_SKIP_PARAMS = frozenset({"cls", "self", "bonded_instance", "kwargs", "args"})

# Global never-grantable set: abilities that must never be agent-invocable even
# if an allowlist grant exists. These are extension/admin meta-abilities that
# manage the platform itself rather than doing work on the agent's behalf. This
# is the coarse, always-on gate; the per-agent allowlist is the fine one.
NEVER_AGENT_INVOCABLE: frozenset = frozenset(
    {
        "manage_ai_providers",
        "configure_ai_models",
        "manage_agents",
        "configure_agent_providers",
        "manage_agent_abilities",
        "optimize_model_selection",
        # The turn itself, not a callable tool: a turn's root Activity is a
        # thinking_turn invocation, but the model must never "call" it.
        "thinking_turn",
    }
)

_PY_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class AbilityAccessDenied(Exception):
    """Raised when an agent is not permitted to invoke the requested ability."""


class ToolInvocationError(Exception):
    """Raised when a permitted ability cannot be resolved or executed."""


@dataclass
class ResolvedAbility:
    """A dispatchable ability: its owner class and the concrete callable."""

    kind: str  # "provider" | "meta"
    owner: type
    method: Callable[..., Any]
    name: str
    extension_name: str


def _json_type_for(annotation: Any) -> Tuple[str, bool]:
    """Map a Python annotation to a (json_schema_type, is_optional) pair.

    ``Optional[X]`` (i.e. ``Union[X, None]``) yields the JSON type of ``X`` and
    ``is_optional=True``; anything unrecognised degrades to ``"string"``.
    """
    origin = get_origin(annotation)
    if origin is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        optional = len(non_none) < len(get_args(annotation))
        inner_type = _PY_TO_JSON_TYPE.get(non_none[0], "string") if non_none else "string"
        return inner_type, optional
    if origin in (list, List):
        return "array", False
    if origin in (dict, Dict):
        return "object", False
    return _PY_TO_JSON_TYPE.get(annotation, "string"), False


def ability_to_tool_schema(
    name: str, method: Callable[..., Any], description: Optional[str] = None
) -> Dict[str, Any]:
    """Introspect an ability callable into an OpenAI function-tool schema.

    Parameters are taken from the callable's signature (skipping the calling-
    convention params in ``_SKIP_PARAMS`` and any ``*args``/``**kwargs``);
    annotations become JSON types; params without a default are ``required``.
    The description defaults to the method's docstring first line.
    """
    properties: Dict[str, Any] = {}
    required: List[str] = []
    try:
        signature = inspect.signature(method)
    except (ValueError, TypeError):
        signature = None
    if signature is not None:
        for param_name, param in signature.parameters.items():
            if param_name in _SKIP_PARAMS:
                continue
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            json_type, optional = _json_type_for(param.annotation)
            properties[param_name] = {"type": json_type}
            if param.default is inspect.Parameter.empty and not optional:
                required.append(param_name)

    doc = (description or inspect.getdoc(method) or "").strip()
    short_description = doc.split("\n\n", 1)[0].strip() if doc else name

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": short_description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


class AbilityInvoker:
    """Resolve and invoke abilities as agent tools, with default-deny access.

    ``provider_instance_resolver`` is an optional callable
    ``(extension_name, ability_name) -> (provider_class, provider_instance) |
    None`` the caller supplies so provider abilities can be bonded against a
    specific provider + instance the agent is entitled to use — the executor
    owns that choice (via the agent's grants / rotation), not the invoker. When
    it returns ``None`` (or is not supplied) a provider ability cannot execute
    and a :class:`ToolInvocationError` is raised rather than silently reaching
    for an arbitrary instance.
    """

    def __init__(
        self,
        model_registry: Any,
        requester_id: str,
        provider_instance_resolver: Optional[
            Callable[[str, str], Optional[Tuple[type, Any]]]
        ] = None,
        never_invocable: frozenset = NEVER_AGENT_INVOCABLE,
    ) -> None:
        self.model_registry = model_registry
        self.requester_id = requester_id
        self.provider_instance_resolver = provider_instance_resolver
        self.never_invocable = never_invocable

    # -- access gate ------------------------------------------------------

    def is_allowed(self, ability_name: str, allowed: Set[str]) -> bool:
        """Return whether ``ability_name`` may be invoked given ``allowed``.

        Default-deny: an ability must be on the per-agent allowlist AND not on
        the global never-grantable denylist.
        """
        if ability_name in self.never_invocable:
            return False
        return ability_name in allowed

    # -- resolution -------------------------------------------------------

    @staticmethod
    def _find_method(owner: type, ability_name: str) -> Optional[Callable[..., Any]]:
        """Find the callable on ``owner`` whose ``_ability_info`` name matches.

        Mis-decorated abilities (bare ``@ability`` that replaced the method with
        the decorator itself) carry no ``_ability_info`` on a real callable and
        so resolve to ``None`` — i.e. non-invocable, which is the safe default.
        """
        for _attr, member in inspect.getmembers(owner):
            info = getattr(member, "_ability_info", None)
            if info and info.get("name") == ability_name and callable(member):
                return member
        return None

    def resolve(self, ability_name: str) -> Optional[ResolvedAbility]:
        """Resolve an ability name to a dispatchable callable, or ``None``.

        Provider abilities are resolved first, by scanning each loaded
        extension's ``providers`` (the extension class's own discovery — the
        registry's eager ``provider_abilities`` dict is not reliably populated
        in every registry, whereas ``ext_cls.providers`` is). Properly decorated
        meta abilities on the extension classes are resolved second. The
        resolved ``owner``/``method`` are used for schema introspection; the
        specific provider instance to bond at invocation is supplied separately
        by ``provider_instance_resolver``.
        """
        registry = getattr(self.model_registry, "extension_registry", None)
        if registry is None:
            return None

        extensions = getattr(registry, "extensions", [])

        for ext_cls in extensions:
            try:
                providers = ext_cls.providers
            except Exception:  # provider discovery is best-effort per extension
                providers = []
            for provider_cls in providers or []:
                if ability_name in getattr(provider_cls, "_abilities", set()):
                    method = self._find_method(provider_cls, ability_name)
                    if method is not None:
                        return ResolvedAbility(
                            "provider",
                            provider_cls,
                            method,
                            ability_name,
                            getattr(ext_cls, "name", ""),
                        )

        for ext_cls in extensions:
            method = self._find_method(ext_cls, ability_name)
            if method is not None:
                return ResolvedAbility(
                    "meta", ext_cls, method, ability_name, getattr(ext_cls, "name", "")
                )
        return None

    def build_tools(self, allowed: Set[str]) -> List[Dict[str, Any]]:
        """Build the model-facing tool schemas for the allowed, resolvable set.

        Applies the same gate as invocation (denylist + allowlist) so the model
        is never offered a tool it would then be refused, and skips names that
        cannot be resolved to a callable.
        """
        tools: List[Dict[str, Any]] = []
        for ability_name in sorted(allowed):
            if not self.is_allowed(ability_name, allowed):
                continue
            resolved = self.resolve(ability_name)
            if resolved is None:
                logger.debug(
                    f"Ability {ability_name!r} allowed but not resolvable to a "
                    "callable; omitting from tool list"
                )
                continue
            tools.append(ability_to_tool_schema(ability_name, resolved.method))
        return tools

    # -- invocation -------------------------------------------------------

    @staticmethod
    def _coerce_arguments(arguments: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
        """Normalise a tool call's arguments to a dict (models emit a JSON str)."""
        if arguments is None:
            return {}
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            stripped = arguments.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ToolInvocationError(
                    f"Tool arguments were not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ToolInvocationError("Tool arguments must be a JSON object")
            return parsed
        raise ToolInvocationError(
            f"Unsupported tool arguments type: {type(arguments).__name__}"
        )

    async def invoke(
        self,
        ability_name: str,
        arguments: Union[str, Dict[str, Any], None],
        allowed: Set[str],
    ) -> Dict[str, Any]:
        """Invoke an allowed ability and return ``{success, content}``.

        Enforces the access gate BEFORE any resolution or execution, so an
        ability outside ``allowed`` (or on the global denylist) is refused even
        though the invoker is otherwise fully capable.

        Only :class:`AbilityAccessDenied` propagates — it is a policy/security
        event the executor must handle explicitly. Every other failure
        (unresolvable ability, malformed arguments, no entitled instance,
        ability runtime error) is captured into
        ``{"success": False, "error": ...}`` so a single bad tool call feeds an
        error back to the model rather than killing the whole turn.
        """
        if not self.is_allowed(ability_name, allowed):
            raise AbilityAccessDenied(
                f"Agent is not permitted to invoke ability {ability_name!r}"
            )

        try:
            resolved = self.resolve(ability_name)
            if resolved is None:
                raise ToolInvocationError(
                    f"Ability {ability_name!r} is not resolvable to a callable"
                )
            call_args = self._coerce_arguments(arguments)
            if resolved.kind == "provider":
                result = self._invoke_provider(resolved, call_args)
            else:
                result = self._invoke_meta(resolved, call_args)
            if inspect.isawaitable(result):
                result = await result
        except AbilityAccessDenied:
            raise
        except Exception as exc:  # dispatch or runtime failure — captured, not fatal
            logger.error(f"Ability {ability_name!r} failed during invocation: {exc}")
            return {"success": False, "error": str(exc)}

        return {"success": True, "content": result}

    def _invoke_provider(
        self, resolved: ResolvedAbility, call_args: Dict[str, Any]
    ) -> Any:
        """Bond the entitled provider instance and call the ability on it.

        The provider class + instance come from ``provider_instance_resolver``
        (the executor's entitlement decision), not from ``resolved.owner`` —
        several providers may implement the same ability and the agent is only
        entitled to specific instances.
        """
        if self.provider_instance_resolver is None:
            raise ToolInvocationError(
                f"No provider instance resolver configured to run provider "
                f"ability {resolved.name!r}"
            )
        picked = self.provider_instance_resolver(
            resolved.extension_name, resolved.name
        )
        if picked is None:
            raise ToolInvocationError(
                f"No entitled provider instance available for ability "
                f"{resolved.name!r} (extension {resolved.extension_name!r})"
            )
        provider_cls, instance = picked
        bonded = provider_cls.bond_instance(instance)
        if bonded is None:
            raise ToolInvocationError(
                f"Could not bond a provider instance for ability {resolved.name!r}"
            )
        method = self._find_method(provider_cls, resolved.name)
        if method is None:
            raise ToolInvocationError(
                f"Provider {provider_cls.__name__} does not implement "
                f"ability {resolved.name!r}"
            )
        return method(bonded, **call_args)

    def _invoke_meta(
        self, resolved: ResolvedAbility, call_args: Dict[str, Any]
    ) -> Any:
        """Call a meta ability, threading the model registry when accepted."""
        try:
            signature = inspect.signature(resolved.method)
            if "model_registry" in signature.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            ):
                call_args.setdefault("model_registry", self.model_registry)
        except (ValueError, TypeError):
            pass
        return resolved.method(**call_args)
