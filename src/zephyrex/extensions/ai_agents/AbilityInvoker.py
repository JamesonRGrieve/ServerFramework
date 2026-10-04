# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension abilities as an agent's tools, behind a default-deny gate.

A tool is one of the agent's ability grants (:class:`AbilityGrant`), named
as :func:`tool_names` gives it. Only an extension's own (static) abilities
are tools: a ``@classmethod`` marked ``@ability`` on the extension class that
performs it. An ability acts for a user through its ``requester_id``; the
invoker fills that in with the identity the turn acts as, so the model never
chooses whose permissions a tool uses, and never sees the parameter.

Access is decided at invocation, not by the tool list the model was shown: a
tool must be granted, and its ability must not be one no agent may use. A
hallucinated or injected tool name is refused like any other ungranted one.
"""

import inspect
import json
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
    Union,
    get_args,
    get_origin,
)

from fastapi import HTTPException

from zephyrex.extensions.ai_agents.BLL_AI_Agents import AbilityGrant
from zephyrex.extensions.ExternalErrors import BaseExternalError

# Parameters of an ability's calling convention, never the model's to fill.
CALLING_CONVENTION = frozenset({"cls", "self", "requester_id"})
REQUESTER = "requester_id"

# Abilities no agent may use, granted or not: the turn itself, running other
# turns, and changing what agents may do.
NEVER_AGENT_INVOCABLE: frozenset[str] = frozenset(
    {"thinking_turn", "take_turn", "grant_ability"}
)

_JSON_TYPE_OF = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

ToolArguments = Union[str, Dict[str, Any], None]


class AbilityAccessDenied(Exception):
    """The agent may not use the tool it called."""


class ToolInvocationError(Exception):
    """A permitted tool could not run: unknown, mis-called, or failed."""


def _json_type(annotation: Any) -> Tuple[str, bool]:
    """``annotation``'s JSON schema type, and whether it admits None."""
    origin = get_origin(annotation)
    if origin is Union:
        members = get_args(annotation)
        present = [a for a in members if a is not type(None)]
        inner = _JSON_TYPE_OF.get(present[0], "string") if present else "string"
        return inner, len(present) < len(members)
    if origin is list:
        return "array", False
    if origin is dict:
        return "object", False
    return _JSON_TYPE_OF.get(annotation, "string"), False


def ability_to_tool_schema(
    name: str, method: Callable[..., Any], description: Optional[str] = None
) -> Dict[str, Any]:
    """An OpenAI function-tool schema for ``method``, called ``name``.

    The parameters are the method's own, less its calling convention
    (``cls``, ``requester_id``) and any ``*args``/``**kwargs``; those without
    a default, and not Optional, are required. The description is the first
    paragraph of the docstring."""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for param_name, param in inspect.signature(method).parameters.items():
        if param_name in CALLING_CONVENTION or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        json_type, optional = _json_type(param.annotation)
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty and not optional:
            required.append(param_name)
    doc = (description or inspect.getdoc(method) or "").strip()
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": doc.split("\n\n", 1)[0].strip() if doc else name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def coerce_arguments(arguments: ToolArguments) -> Dict[str, Any]:
    """A tool call's arguments as a dict (models send a JSON object string)."""
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return dict(arguments)
    if not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as error:
        raise ToolInvocationError(f"Tool arguments were not JSON: {error}") from None
    if not isinstance(parsed, dict):
        raise ToolInvocationError("Tool arguments must be a JSON object")
    return parsed


class AbilityInvoker:
    """Resolves and runs an agent's granted tools, acting as
    ``requester_id``."""

    def __init__(
        self,
        model_registry: Any,
        requester_id: str,
        never_invocable: frozenset[str] = NEVER_AGENT_INVOCABLE,
    ) -> None:
        self.model_registry = model_registry
        self.requester_id = requester_id
        self.never_invocable = never_invocable

    def is_allowed(self, tool: str, allowed: Mapping[str, AbilityGrant]) -> bool:
        """Whether ``tool`` is granted, and its ability one an agent may use."""
        grant = allowed.get(tool)
        return grant is not None and grant.name not in self.never_invocable

    def resolve(self, grant: AbilityGrant) -> Optional[Callable[..., Any]]:
        """The extension's ability method the grant names, or None when the
        extension is not loaded or has no such static ability."""
        registry = getattr(self.model_registry, "extension_registry", None)
        if registry is None:
            return None
        for extension in registry.extensions:
            if extension.name != grant.extension:
                continue
            # Read statically: getattr on every attribute would run the
            # extension's classproperties.
            for attribute, member in inspect.getmembers_static(extension):
                if not isinstance(member, classmethod):
                    continue
                info = getattr(member.__func__, "_ability_info", None)
                if info and info.get("name") == grant.name:
                    method: Callable[..., Any] = getattr(extension, attribute)
                    return method
        return None

    def build_tools(self, allowed: Mapping[str, AbilityGrant]) -> List[Dict[str, Any]]:
        """Schemas for the granted tools the agent may use and that resolve."""
        tools: List[Dict[str, Any]] = []
        for tool in sorted(allowed):
            if not self.is_allowed(tool, allowed):
                continue
            method = self.resolve(allowed[tool])
            if method is not None:
                tools.append(ability_to_tool_schema(tool, method))
        return tools

    async def invoke(
        self, tool: str, arguments: ToolArguments, allowed: Mapping[str, AbilityGrant]
    ) -> Any:
        """Run ``tool`` with the model's ``arguments``, as the requester.

        Raises AbilityAccessDenied for a tool the agent may not use (before
        anything resolves or runs), and ToolInvocationError when a permitted
        tool cannot run or refuses its input, with the reason to tell the
        model."""
        if not self.is_allowed(tool, allowed):
            raise AbilityAccessDenied(f"The agent may not use {tool!r}")
        method = self.resolve(allowed[tool])
        if method is None:
            raise ToolInvocationError(f"{tool!r} is not an ability that can run")
        call = coerce_arguments(arguments)
        call.pop(REQUESTER, None)
        signature = inspect.signature(method)
        if REQUESTER in signature.parameters:
            call[REQUESTER] = self.requester_id
        try:
            signature.bind(**call)
        except TypeError as error:
            raise ToolInvocationError(f"{tool!r} was called wrongly: {error}") from None
        try:
            result = method(**call)
            if inspect.isawaitable(result):
                result = await result
        except HTTPException as refused:
            raise ToolInvocationError(f"{tool!r} refused: {refused.detail}") from None
        except BaseExternalError as failed:
            raise ToolInvocationError(f"{tool!r} failed: {failed}") from None
        return result
