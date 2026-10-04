# SPDX-License-Identifier: AGPL-3.0-or-later
"""One agent turn: the thinking-turn loop over one InvocationInstance.

1. The system prompt is the agent's context prompts (or a default) with the
   turn's state filled in; the model is offered the agent's granted tools.
2. The model is called, and each tool it asks for is run, until it answers
   without asking or ``max_steps`` round trips have passed.
3. Every tool call is a child Activity of the turn's root ``thinking_turn``
   Activity; the root records the model's reasoning and final answer.
4. ``speak`` (posting a message), ``memorize``, ``trim``, ``recall`` and
   ``abilities`` are performed here, since they need the turn; every other
   tool is an extension ability run by :class:`AbilityInvoker`.
5. The instance records running, then succeeded or failed.

The turn acts as the agent's owner: tools, messages, memories and the turn's
activities are theirs, under their permissions. The turn's lifecycle is the
server's bookkeeping, written as ROOT once the requester has been shown to
see the instance.

The model transport (``chat_fn``) defaults to native chat over the provider
instances the agent is pinned to (``ProviderInstanceAgent``, restricted by
``ProviderInstanceAgentAbility``), or, pinned to none, its rotation; a
caller may hand in another implementing the same contract.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
)

from fastapi import HTTPException

from zephyrex.extensions.ai_agents.AbilityInvoker import (
    AbilityAccessDenied,
    AbilityInvoker,
    ToolInvocationError,
    ability_to_tool_schema,
    coerce_arguments,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    EXTENSION_NAME,
    AbilityGrant,
    ActivityManager,
    ActivityState,
    AgentAbilityManager,
    AgentContextPromptManager,
    AgentManager,
    AgentMemoryManager,
    ConversationAgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
    ProviderInstanceAgentAbilityManager,
    ProviderInstanceAgentManager,
    tool_names,
)
from zephyrex.extensions.ai_memories.BLL_AI_Memories import MemoryManager
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    PermanentExternalError,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, RotationManager

# The model transport: (messages, tools) -> {"message": {role, content,
# tool_calls, reasoning}, ...}. Failures raise (typed external errors).
ChatTransport = Callable[
    [List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], Awaitable[Dict[str, Any]]
]
ToolCall = Tuple[Optional[str], Optional[str], Any]

# Long-term memories a recall returns, and that a turn's prompt carries.
RECALL_LIMIT = 5
PROMPT_MEMORY_LIMIT = 10
RECENT_TURNS = 5

# Model <-> tool round trips a turn may take before it must conclude.
DEFAULT_MAX_STEPS = 8

THINKING_TURN_ABILITY = "thinking_turn"
SPEAK_ABILITY = "speak"
MEMORIZE_ABILITY = "memorize"
TRIM_ABILITY = "trim"
RECALL_ABILITY = "recall"
ABILITIES_ABILITY = "abilities"
SELF_ABILITIES = frozenset(
    {SPEAK_ABILITY, MEMORIZE_ABILITY, TRIM_ABILITY, RECALL_ABILITY, ABILITIES_ABILITY}
)

# Reserved short-term memory keys hold the executor's working state (the
# abilities discovered with `abilities`); they surface through their own
# placeholder, never as the agent's notes.
RESERVED_MEMORY_PREFIX = "__"
SEARCHED_ABILITIES_KEY = "__searched_abilities__"

NATIVE = "native"
IN_BAND = "in_band"
IN_BAND_CALL = re.compile(r"```tool\b\s*(.*?)```", re.DOTALL)
UNFILLED_PLACEHOLDER = re.compile(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}")

# Used when the agent has no context prompts; the same {VARIABLE}s as a
# configured thinking-turn prompt, so state is filled in either way.
DEFAULT_THINKING_TURN_PROMPT = (
    "You are an autonomous agent taking one turn. Think about the current state, "
    "then act only through the tools you have been granted; you may call several, "
    "and each result returns before you continue. Speaking to a person is the "
    "`speak` tool and is optional — if you have nothing worth their attention, do "
    "not call it and simply end the turn. Be economical: a turn that concludes "
    "nothing needs doing is a good turn.\n\n"
    "Current time: {CURRENT_TIME}\n"
    "Who invoked you: {INVOCATION_CONTEXT}\n"
    "Recent activity: {RECENT_ACTIVITY}\n"
    "Relevant memory: {AGENT_MEMORY}\n"
    "{ADDITIONAL_CONTEXT}"
)


class TurnContext:
    """What a turn's tools need: the instance, its root activity, the agent,
    who it acts as, and what it may use."""

    def __init__(
        self,
        instance: Any,
        root: Any,
        agent: Any,
        acting: str,
        allowed: Dict[str, AbilityGrant],
        invoker: AbilityInvoker,
    ) -> None:
        self.instance = instance
        self.root = root
        self.agent = agent
        self.acting = acting
        self.allowed = allowed
        self.invoker = invoker

    def ability_id(self, tool: Optional[str]) -> str:
        """The Ability an activity for ``tool`` is typed by: the grant's,
        else (an ungranted call) the turn's own."""
        grant = self.allowed.get(tool or "")
        return grant.ability_id if grant else str(self.root.ability_id)

    def self_ability(self, tool: str) -> Optional[str]:
        """The executor ability ``tool`` names, if it names one."""
        grant = self.allowed.get(tool)
        if grant and grant.extension == EXTENSION_NAME and grant.name in SELF_ABILITIES:
            return grant.name
        return None


class AgentTurnExecutor:
    """Runs turns as ``requester_id``, who must see the turn's instance."""

    def __init__(
        self,
        model_registry: Any,
        requester_id: str,
        chat_fn: Optional[ChatTransport] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        tool_mode: str = NATIVE,
    ) -> None:
        self.model_registry = model_registry
        self.requester_id = requester_id
        self._chat_fn_override = chat_fn
        self.max_steps = max_steps
        # NATIVE passes a tools=[...] catalog and reads structured tool_calls;
        # IN_BAND passes none and parses ```tool JSON blocks from the text,
        # for models without native function-calling.
        self.tool_mode = tool_mode

    def _as(self, manager_class: Any, requester_id: str) -> Any:
        return manager_class(
            requester_id=requester_id, model_registry=self.model_registry
        )

    def _bookkeeping(self, manager_class: Any) -> Any:
        return self._as(manager_class, env("ROOT_ID"))

    async def run(self, invocation_instance_id: str) -> Dict[str, Any]:
        """Run the turn and return a summary. A failed turn is recorded on
        its instance (status 'failed', error) and returned, never raised, so
        one bad turn never stops what scheduled it."""
        instance = self._as(InvocationInstanceManager, self.requester_id).get(
            id=invocation_instance_id
        )
        lifecycle = self._bookkeeping(InvocationInstanceManager)
        lifecycle.update(
            id=instance.id, status="running", started_at=datetime.now(timezone.utc)
        )
        try:
            summary = await self._run_turn(instance)
        except (BaseExternalError, HTTPException, ToolInvocationError) as failed:
            reason = failed.detail if isinstance(failed, HTTPException) else str(failed)
            return self._failed(lifecycle, instance.id, str(reason))
        except Exception:  # the task-runner boundary: record, never propagate
            logger.exception("Agent turn %s failed", instance.id)
            return self._failed(lifecycle, instance.id, "internal error")
        lifecycle.update(
            id=instance.id, status="succeeded", completed_at=datetime.now(timezone.utc)
        )
        return {"status": "succeeded", **summary}

    @staticmethod
    def _failed(lifecycle: Any, instance_id: str, reason: str) -> Dict[str, Any]:
        logger.warning("Agent turn %s failed: %s", instance_id, reason)
        lifecycle.update(
            id=instance_id,
            status="failed",
            error=reason,
            completed_at=datetime.now(timezone.utc),
        )
        return {"status": "failed", "error": reason}

    async def _run_turn(self, instance: Any) -> Dict[str, Any]:
        agent = self._as(AgentManager, self.requester_id).get(id=instance.agent_id)
        acting = agent.user_id or self.requester_id
        allowed = tool_names(self._as(AgentAbilityManager, acting).grants(agent.id))
        invoker = AbilityInvoker(
            model_registry=self.model_registry, requester_id=acting
        )
        activities = self._as(ActivityManager, acting)
        root = activities.create(
            invocation_instance_id=instance.id,
            ability_id=ensure_ability(self.model_registry, THINKING_TURN_ABILITY),
            title="Thinking turn",
            body="",
        )
        turn = TurnContext(instance, root, agent, acting, allowed, invoker)

        in_band = self.tool_mode == IN_BAND
        system_prompt = self._build_system_prompt(agent, instance, acting)
        tools = None if in_band else self._build_tools(turn)
        if in_band:
            system_prompt += self._in_band_tool_instructions(turn)
        chat_fn = self._chat_fn_override or self._make_rotation_chat(agent, acting)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instance.payload or "Take your turn."},
        ]
        final_content = ""
        final_reasoning = ""
        spoke = False
        tool_calls_made = 0

        for _step in range(self.max_steps):
            message = (await chat_fn(messages, tools))["message"]
            content = message.get("content")
            final_reasoning = message.get("reasoning") or final_reasoning
            messages.append(self._assistant_message_dict(message))
            calls: List[ToolCall]
            if in_band:
                calls = self._parse_in_band_calls(content)
                if content:
                    final_content = self._strip_tool_blocks(content) or final_content
            else:
                calls = [
                    (c.get("id"), c.get("name"), c.get("arguments"))
                    for c in (message.get("tool_calls") or [])
                ]
                if content:
                    final_content = content
            if not calls:
                break

            in_band_results: List[str] = []
            for call_id, name, arguments in calls:
                tool_calls_made += 1
                result, did_speak = await self._dispatch_tool(name, arguments, turn)
                spoke = spoke or did_speak
                if in_band:
                    in_band_results.append(f"{name}: {result}")
                else:
                    messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": result}
                    )
            if in_band:
                # Without native tools there is no tool role: the results
                # come back as the next user message.
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool results:\n" + "\n".join(in_band_results),
                    }
                )

        body = "\n\n".join(
            section.strip()
            for section in (final_reasoning, final_content)
            if section and section.strip()
        )
        activities.update(id=root.id, body=body or "(no output)")
        return {
            "instance_id": instance.id,
            "root_activity_id": root.id,
            "spoke": spoke,
            "tool_calls": tool_calls_made,
        }

    def _record(
        self, turn: TurnContext, tool: str, title: str, body: str, state: ActivityState
    ) -> None:
        self._as(ActivityManager, turn.acting).create(
            invocation_instance_id=turn.instance.id,
            ability_id=turn.ability_id(tool),
            parent_id=turn.root.id,
            title=title,
            body=body,
            state=state,
        )

    async def _dispatch_tool(
        self, name: Optional[str], arguments: Any, turn: TurnContext
    ) -> Tuple[str, bool]:
        """Run one tool call: ``(what to tell the model, whether it spoke)``.

        A call the agent may not make is refused, recorded and told to the
        model; a tool that cannot run is recorded as an error and its reason
        told to the model; neither ends the turn."""
        if not name:
            return "Tool call refused: it named no ability.", False
        if not turn.invoker.is_allowed(name, turn.allowed):
            self._record(
                turn,
                name,
                f"Denied: {name}",
                f"The agent may not use {name}",
                ActivityState.ERROR,
            )
            return f"Refused: you are not permitted to use {name}.", False
        try:
            args = coerce_arguments(arguments)
            performed = turn.self_ability(name)
            if performed == SPEAK_ABILITY:
                return await self._do_speak(args, turn, name)
            if performed is not None:
                return await self._do_self(performed, args, turn, name), False
            outcome = self._stringify(
                await turn.invoker.invoke(name, args, turn.allowed)
            )
        except AbilityAccessDenied as denied:
            self._record(
                turn, name, f"Denied: {name}", str(denied), ActivityState.ERROR
            )
            return f"Refused: {denied}", False
        except ToolInvocationError as failed:
            self._record(turn, name, name, str(failed), ActivityState.ERROR)
            return f"Failed: {failed}", False
        self._record(turn, name, name, outcome, ActivityState.SUCCESS)
        return outcome, False

    async def _do_self(
        self, ability: str, args: Dict[str, Any], turn: TurnContext, tool: str
    ) -> str:
        if ability == ABILITIES_ABILITY:
            return self._do_abilities(args, turn, tool)
        if ability == MEMORIZE_ABILITY:
            result = await self._do_memorize(args, turn)
        elif ability == TRIM_ABILITY:
            result = self._do_trim(args, turn)
        else:
            result = await self._do_recall(args, turn)
        self._record(turn, tool, ability, result, ActivityState.SUCCESS)
        return result

    async def _do_speak(
        self, args: Dict[str, Any], turn: TurnContext, tool: str
    ) -> Tuple[str, bool]:
        """Post the agent's message in the conversation the turn is about
        (or the one named), as the agent."""
        from zephyrex.extensions.conversations.BLL_Conversations import MessageManager

        text = args.get("message") or args.get("content") or args.get("body") or ""
        if not text:
            return "Nothing was said (empty message).", False
        conversation_id = args.get("conversation_id") or self._resolve_conversation(
            turn
        )
        if not conversation_id:
            self._record(turn, tool, SPEAK_ABILITY, text, ActivityState.WARNING)
            return "No conversation available to speak into; message not sent.", False
        try:
            self._as(MessageManager, turn.acting).create_agent_message(
                conversation_id=conversation_id, content=text
            )
        except HTTPException as refused:
            raise ToolInvocationError(f"speak refused: {refused.detail}") from None
        self._record(turn, tool, SPEAK_ABILITY, text, ActivityState.SUCCESS)
        return "Message sent.", True

    def _long_term(self, acting: str) -> MemoryManager:
        """Long-term memory (the ai_memories store), kept for the agent's
        owner."""
        manager: MemoryManager = self._as(MemoryManager, acting)
        return manager

    def _short_term(self, acting: str) -> AgentMemoryManager:
        manager: AgentMemoryManager = self._as(AgentMemoryManager, acting)
        return manager

    async def _do_memorize(self, args: Dict[str, Any], turn: TurnContext) -> str:
        """Short-term (keyed working memory) by default; long-term (the
        ai_memories store) when ``long``."""
        key = args.get("key") or None
        content = args.get("body") or args.get("content") or ""
        if not content:
            return "Nothing memorized (empty content)."
        if args.get("long"):
            await self._long_term(turn.acting).keep(turn.agent.id, content, key=key)
            return f"Stored to long-term memory{f' under {key!r}' if key else ''}."
        if not key:
            return "Short-term memory requires a key."
        self._short_term(turn.acting).remember(turn.agent.id, key, content)
        return f"Remembered {key!r} in short-term memory."

    def _do_trim(self, args: Dict[str, Any], turn: TurnContext) -> str:
        keys = args.get("memories") or args.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        removed = self._short_term(turn.acting).forget(turn.agent.id, list(keys))
        return (
            f"Trimmed {removed} short-term memory entr{'y' if removed == 1 else 'ies'}."
        )

    async def _do_recall(self, args: Dict[str, Any], turn: TurnContext) -> str:
        query = args.get("search") or args.get("query") or args.get("about") or ""
        found = await self._long_term(turn.acting).recall(
            turn.agent.id, str(query), RECALL_LIMIT
        )
        if not found:
            return "(no matching long-term memories)"
        return "\n".join(f"- {memory.content}" for memory in found)

    def _do_abilities(self, args: Dict[str, Any], turn: TurnContext, tool: str) -> str:
        """The tools the agent may use, optionally those whose name or
        description contains ``search``; what is found is kept (under the
        reserved short-term key) for later turns' prompts."""
        search = str(args.get("search") or args.get("query") or args.get("about") or "")
        needle = search.strip().lower()
        matches = [
            d
            for d in self._discoverable_abilities(turn)
            if not needle
            or needle in d["name"].lower()
            or needle in d["description"].lower()
        ]
        if matches:
            self._remember_searched_abilities(turn, matches)
            names = ", ".join(d["name"] for d in matches)
            body = (
                f"Discovered {len(matches)} abilit{'y' if len(matches) == 1 else 'ies'}: "
                f"{names}\n{json.dumps(matches)}"
            )
        elif needle:
            body = f"No abilities matched {search!r}."
        else:
            body = "No abilities are currently available to you."
        self._record(turn, tool, ABILITIES_ABILITY, body, ActivityState.SUCCESS)
        return json.dumps(matches)

    def _discoverable_abilities(self, turn: TurnContext) -> List[Dict[str, Any]]:
        """``{name, description, parameters}`` for each tool the agent may
        use: executor abilities from their signatures, extension abilities
        from their methods."""
        found: List[Dict[str, Any]] = []
        for tool in sorted(turn.allowed):
            schema = self._tool_schema(tool, turn)
            if schema is not None:
                function = schema["function"]
                found.append(
                    {
                        "name": function["name"],
                        "description": function["description"],
                        "parameters": function["parameters"],
                    }
                )
        return found

    def _tool_schema(self, tool: str, turn: TurnContext) -> Optional[Dict[str, Any]]:
        if not turn.invoker.is_allowed(tool, turn.allowed):
            return None
        performed = turn.self_ability(tool)
        if performed is not None:
            return ability_to_tool_schema(tool, SELF_ABILITY_SIGNATURES[performed])
        method = turn.invoker.resolve(turn.allowed[tool])
        return None if method is None else ability_to_tool_schema(tool, method)

    def _remember_searched_abilities(
        self, turn: TurnContext, matches: List[Dict[str, Any]]
    ) -> None:
        """Merge newly discovered abilities into the reserved key, by name."""
        memory = self._short_term(turn.acting)
        stored = memory.as_dict(turn.agent.id).get(SEARCHED_ABILITIES_KEY)
        by_name: Dict[str, Dict[str, Any]] = {}
        if stored:
            for entry in json.loads(stored):
                by_name[entry["name"]] = entry
        for descriptor in matches:
            by_name[descriptor["name"]] = descriptor
        memory.remember(
            turn.agent.id,
            SEARCHED_ABILITIES_KEY,
            json.dumps(sorted(by_name.values(), key=lambda d: d["name"])),
        )

    def _build_tools(self, turn: TurnContext) -> Optional[List[Dict[str, Any]]]:
        """The model-facing tools: every granted tool that can run."""
        tools = [
            schema
            for schema in (
                self._tool_schema(tool, turn) for tool in sorted(turn.allowed)
            )
            if schema is not None
        ]
        return tools or None

    def _build_system_prompt(self, agent: Any, instance: Any, acting: str) -> str:
        """The agent's context prompts (else the default) with the turn's
        state in their {VARIABLE} placeholders."""
        base = "\n\n".join(
            self._as(AgentContextPromptManager, acting).contents(agent.id)
        )
        substitutions: Dict[str, Any] = {
            "CURRENT_TIME": datetime.now(timezone.utc).isoformat(),
            "INVOCATION_CONTEXT": self._invocation_context(instance, acting),
            "RECENT_ACTIVITY": self._recent_activity(agent, acting),
            "AGENT_MEMORY": self._agent_memory(agent, acting),
            "ADDITIONAL_CONTEXT": instance.payload or "",
        }
        substitutions.update(self._sentience_substitutions(agent, instance, acting))
        return self._inject(base or DEFAULT_THINKING_TURN_PROMPT, substitutions)

    def _sentience_substitutions(
        self, agent: Any, instance: Any, acting: str
    ) -> Dict[str, Any]:
        """The {{PLACEHOLDER}}s of the sentience-style prompt: timing,
        continuity and memory, from the agent's real turns."""
        ordered = sorted(
            self._as(InvocationInstanceManager, acting).list(agent_id=agent.id),
            key=lambda i: i.created_at or datetime.min,
        )
        prior = None
        for turn in ordered:
            if turn.id == instance.id:
                break
            prior = turn
        previous_thought = ""
        if prior is not None:
            roots = [
                a
                for a in self._as(ActivityManager, acting).list(
                    invocation_instance_id=prior.id
                )
                if a.parent_id is None
            ]
            previous_thought = roots[0].body if roots else ""
        short_term = self._short_term(acting).as_dict(agent.id)
        visible_memory = {
            key: value
            for key, value in short_term.items()
            if not key.startswith(RESERVED_MEMORY_PREFIX)
        }
        return {
            "ITERATIONS": len(ordered),
            "TIMEZONE": "UTC",
            "START_TIME": datetime.now(timezone.utc).isoformat(),
            "PREVIOUS_START_TIME": (prior.started_at if prior else None) or "",
            "PREVIOUS_END_TIME": (prior.completed_at if prior else None) or "",
            "MAXIMUM_CONTEXT_SIZE": "unknown",
            "CURRENT_CONTEXT_SIZE": "unknown",
            "IN_CONTEXT_ABILITIES": short_term.get(SEARCHED_ABILITIES_KEY, "[]"),
            "SHORT_TERM_MEMORIES": json.dumps(visible_memory),
            "SENSORY_INPUTS": json.dumps(
                {
                    "invocation": self._invocation_context(instance, acting),
                    "message": instance.payload or "",
                }
            ),
            "ABILITY_RESULTS": "[]",
            "PREVIOUS_THOUGHT": previous_thought,
            "PREVIOUS_THOUGHTS": self._recent_activity(agent, acting),
        }

    def _in_band_tool_instructions(self, turn: TurnContext) -> str:
        """For in-band mode: how to call a tool, and which may be called."""
        callable_tools = sorted(
            tool for tool in turn.allowed if turn.invoker.is_allowed(tool, turn.allowed)
        )
        listed = ", ".join(callable_tools) if callable_tools else "(none)"
        return (
            "\n\n# Tool Call Format\n"
            "To use an ability, emit one or more fenced code blocks with the "
            "language `tool`, each containing a JSON object of the form "
            '{"ability": "<name>", "args": { ... }}. Text outside these blocks '
            "is internal monologue and is NOT visible to anyone unless you use "
            f"the speak ability. Abilities callable this cycle: {listed}.\n"
            "Example:\n"
            "```tool\n"
            '{"ability": "speak", "args": {"to": "James", "body": "Hello."}}\n'
            "```"
        )

    @staticmethod
    def _parse_in_band_calls(text: Optional[str]) -> List[ToolCall]:
        """```tool fenced JSON blocks as ``(id, name, args)`` calls, from
        ``{"ability"|"tool": name, "args": {...}}`` or ``{name: {...}}``."""
        calls: List[ToolCall] = []
        for match in IN_BAND_CALL.finditer(text or ""):
            try:
                block = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(block, dict):
                continue
            name = block.get("ability") or block.get("tool")
            args: Any = block.get("args") or {}
            if not name and len(block) == 1:
                name, value = next(iter(block.items()))
                args = value.get("args", value) if isinstance(value, dict) else {}
            if name and isinstance(args, dict):
                calls.append((None, name, args))
        return calls

    @staticmethod
    def _strip_tool_blocks(text: str) -> str:
        """The monologue without its ```tool blocks."""
        return IN_BAND_CALL.sub("", text).strip()

    def _invocation_context(self, instance: Any, acting: str) -> str:
        if instance.trigger_message_id:
            return f"a conversation message ({instance.trigger_message_id})"
        if instance.invocation_trigger_id:
            trigger = self._as(InvocationTriggerManager, acting).get(
                id=instance.invocation_trigger_id
            )
            due = f", due {trigger.due_at.isoformat()}" if trigger.due_at else ""
            return (
                f"a configured {trigger.invocation_type} trigger ({trigger.id}, "
                f"priority {trigger.priority}{due})"
            )
        return "an ad-hoc invocation"

    def _recent_activity(self, agent: Any, acting: str) -> str:
        """The outcomes of the agent's last few turns."""
        recent = sorted(
            self._as(InvocationInstanceManager, acting).list(agent_id=agent.id),
            key=lambda i: i.created_at or datetime.min,
            reverse=True,
        )[:RECENT_TURNS]
        if not recent:
            return "none yet"
        return "; ".join(f"{turn.status} turn" for turn in recent)

    def _agent_memory(self, agent: Any, acting: str) -> str:
        """The agent's most recent long-term memories (``recall`` searches
        the rest)."""
        memories = self._long_term(acting).recent(agent.id, PROMPT_MEMORY_LIMIT)
        return "\n".join(f"- {m.content}" for m in memories if m.content) or "none"

    def _make_rotation_chat(self, agent: Any, acting: str) -> ChatTransport:
        """Native chat, failing over across model instances: the agent's
        pinned provider instances when it has any, else its rotation.
        Tokens used are recorded against the agent's owner."""

        async def chat(
            messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]
        ) -> Dict[str, Any]:
            from zephyrex.extensions.ai.EXT_AI import CHAT, EXT_AI

            rotation = self._thinking_rotation(agent, acting, CHAT)
            answer: Dict[str, Any] = await rotation.arotate(
                EXT_AI.provider_call(CHAT),
                messages,
                tools,
                requester_id=acting,
                ability=CHAT,
            )
            return answer

        return chat

    def _thinking_rotation(self, agent: Any, acting: str, ability: str) -> Any:
        """What the agent thinks over for ``ability``: its usable pinned
        instances, overriding its rotation, or (pinned to none) its
        rotation. A pinned agent none of whose instances is usable, or an
        agent with neither, cannot think: a permanent error."""
        pinned = self._as(ProviderInstanceAgentManager, acting).pinned(agent.id)
        if pinned:
            usable = self._usable_pinned(agent, acting, pinned, ability)
            if not usable:
                raise PermanentExternalError(
                    f"none of the agent's {len(pinned)} pinned provider instances "
                    f"can be used for {ability}: each is disabled, deleted, no "
                    "longer visible to its owner, not allowed that ability, or "
                    "does not offer it"
                )
            return PinnedInstanceRotation(
                model_registry=self.model_registry,
                requester_id=acting,
                agent_id=agent.id,
                instance_ids=usable,
            )
        if not agent.rotation_id:
            raise PermanentExternalError("the agent has no rotation configured")
        return RotationManager(
            requester_id=acting,
            target_id=agent.rotation_id,
            model_registry=self.model_registry,
        )

    def _usable_pinned(
        self, agent: Any, acting: str, pinned: List[str], ability: str
    ) -> List[str]:
        """The pinned instances (in order) a turn may think on now: still
        visible to the agent's owner (so not deleted), enabled, allowed the
        ability by the agent's restrictions, and of a provider offering it."""
        from zephyrex.extensions.ai.EXT_AI import EXT_AI

        restricted = self._as(ProviderInstanceAgentAbilityManager, acting).allowed(
            agent.id
        )
        ability_ids = catalog_ability_ids(self.model_registry, EXT_AI.name, ability)
        instances = self._as(ProviderInstanceManager, acting)
        usable: List[str] = []
        for instance_id in pinned:
            try:
                instance = instances.get(id=instance_id)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                continue
            if instance.enabled is False:
                continue
            if instance_id in restricted and not restricted[instance_id] & ability_ids:
                continue
            try:
                provider = EXT_AI.provider_class_for(instance)
            except LookupError:
                continue
            if ability in provider._abilities:
                usable.append(instance_id)
        return usable

    def _resolve_conversation(self, turn: TurnContext) -> Optional[str]:
        """The conversation to speak into: the triggering message's, else
        one the agent actively takes part in."""
        from zephyrex.extensions.conversations.BLL_Conversations import MessageManager

        if turn.instance.trigger_message_id:
            try:
                message = self._as(MessageManager, turn.acting).get(
                    id=turn.instance.trigger_message_id
                )
                return str(message.conversation_id)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
        seats = self._as(ConversationAgentManager, turn.acting).list(
            agent_id=turn.agent.id, active=True
        )
        return str(seats[0].conversation_id) if seats else None

    @staticmethod
    def _assistant_message_dict(message: Mapping[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        return out

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str)

    @staticmethod
    def _inject(template: str, substitutions: Mapping[str, Any]) -> str:
        """Fill ``{KEY}`` and ``{{KEY}}``, then drop unfilled ``{{KEY}}``s so
        a raw template never reaches the model. Other braces (JSON in the
        prompt) are left alone."""
        result = template
        for key, value in substitutions.items():
            result = result.replace("{{" + key + "}}", str(value))
            result = result.replace("{" + key + "}", str(value))
        return UNFILLED_PLACEHOLDER.sub("", result)


def _speak_signature(message: str, conversation_id: str = "") -> None:
    """Send a message to the operator/conversation. Use only when you have
    something worth their attention.

    Args:
        message: What to say to the operator.
        conversation_id: Optional conversation to speak into; defaults to the
            turn's conversation.
    """


def _memorize_signature(body: str, key: str = "", long: bool = False) -> None:
    """Save a memory. Short-term (default) is keyed working memory; set long=true
    to store it durably in long-term memory.

    Args:
        body: The content to remember.
        key: Short-term memory key (required unless long); re-using a key
            overwrites it.
        long: Store in long-term memory instead of short-term working memory.
    """


def _trim_signature(memories: List[str]) -> None:
    """Remove entries from short-term working memory by key.

    Args:
        memories: The short-term memory keys to drop.
    """


def _recall_signature(search: str = "") -> None:
    """Search long-term memory for entries relevant to a query.

    Args:
        search: What to look for; empty returns the most recent memories.
    """


def _abilities_signature(search: str = "") -> None:
    """Discover the abilities available to you. Returns each ability's name,
    purpose, and parameters so you can call it.

    Args:
        search: Optional term to filter abilities by name or purpose; empty
            returns everything you are entitled to use.
    """


# The executor abilities' tool schemas come from these signatures; they are
# never called.
SELF_ABILITY_SIGNATURES: Dict[str, Callable[..., Any]] = {
    SPEAK_ABILITY: _speak_signature,
    MEMORIZE_ABILITY: _memorize_signature,
    TRIM_ABILITY: _trim_signature,
    RECALL_ABILITY: _recall_signature,
    ABILITIES_ABILITY: _abilities_signature,
}


def ensure_ability(
    model_registry: Any, name: str, extension: str = EXTENSION_NAME
) -> str:
    """The id of ``extension``'s Ability row called ``name``, made (with
    the extension's row) if the registry was never seeded with it.

    A turn's root activity is typed by ``thinking_turn``, so the row must
    exist even where seeding has not run. Abilities and extensions are
    system records: SYSTEM writes them, as seeding does, so every user may
    read them (a ROOT-made row is ROOT's alone)."""
    system = env("SYSTEM_ID")
    extensions = ExtensionManager(requester_id=system, model_registry=model_registry)
    abilities = AbilityManager(requester_id=system, model_registry=model_registry)
    found = extensions.list(name=extension)
    extension_id = found[0].id if found else extensions.create(name=extension).id
    existing = abilities.list(name=name, extension_id=extension_id)
    if existing:
        return str(existing[0].id)
    return str(abilities.create(name=name, extension_id=extension_id, meta=True).id)


def catalog_ability_ids(model_registry: Any, extension: str, name: str) -> Set[str]:
    """The ids of ``extension``'s Ability rows called ``name`` (none where
    it was never seeded)."""
    system = env("SYSTEM_ID")
    found = ExtensionManager(requester_id=system, model_registry=model_registry).list(
        name=extension
    )
    if not found:
        return set()
    return {
        str(row.id)
        for row in AbilityManager(
            requester_id=system, model_registry=model_registry
        ).list(name=name, extension_id=found[0].id)
    }


@dataclass(frozen=True)
class PinnedLink:
    """One step of a :class:`PinnedInstanceRotation`, shaped as the rows a
    rotation walks."""

    id: str
    provider_instance_id: str
    parent_id: Optional[str] = None


class PinnedInstanceRotation(RotationManager):
    """A rotation over the provider instances an agent is pinned to, in
    order, instead of a stored rotation's: the framework's retry, failover
    and typed-error policy, applied to them. Each is read as the agent's
    owner when tried."""

    def __init__(
        self,
        model_registry: Any,
        requester_id: str,
        agent_id: str,
        instance_ids: List[str],
    ) -> None:
        super().__init__(
            model_registry=model_registry,
            requester_id=requester_id,
            target_id=f"pinned instances of agent {agent_id}",
        )
        self._links = [
            PinnedLink(id=f"{agent_id}:{instance_id}", provider_instance_id=instance_id)
            for instance_id in instance_ids
        ]

    def _get_ordered_rotation_provider_instances(self) -> List[PinnedLink]:
        return list(self._links)


async def fire_turn(
    model_registry: Any,
    agent_id: str,
    payload: Optional[str],
    *,
    trigger_id: Optional[str] = None,
    trigger_message_id: Optional[str] = None,
    executor: Optional[AgentTurnExecutor] = None,
) -> Any:
    """One turn of the agent, its owner's: made as them, run, and returned
    finished (succeeded or failed). HTTPException when the turn cannot be
    made (the agent is gone, or its owner may no longer run it)."""
    root = env("ROOT_ID")
    agent = AgentManager(requester_id=root, model_registry=model_registry).get(
        id=agent_id
    )
    owner = agent.user_id or root
    instances = InvocationInstanceManager(
        requester_id=owner, model_registry=model_registry
    )
    instance = instances.create(
        agent_id=agent.id,
        invocation_trigger_id=trigger_id,
        trigger_message_id=trigger_message_id,
        payload=payload,
    )
    runner = executor or AgentTurnExecutor(
        model_registry=model_registry, requester_id=owner
    )
    await runner.run(instance.id)
    return instances.get(id=instance.id)
