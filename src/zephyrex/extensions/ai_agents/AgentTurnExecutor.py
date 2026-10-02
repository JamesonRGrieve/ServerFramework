"""Agent turn execution — the thinking-turn loop.

Runs one agent turn (one :class:`InvocationInstanceModel`):

1. builds the system prompt from the thinking-turn prompt + injected state +
   the agent's granted tool catalog;
2. calls the model and drives a native tool-call loop, bounded by ``max_steps``;
3. records every tool call as a child :class:`ActivityModel` under the turn's
   root ``thinking_turn`` Activity;
4. handles the ``speak`` ability specially — it produces an operator-facing
   Message (the only thing a turn renders as a chat bubble); every other tool
   routes through the access-gated :class:`AbilityInvoker`;
5. records the instance lifecycle (running -> succeeded/failed).

The model transport (``chat_fn``) is injectable: it defaults to a rotation-based
native chat over the agent's provider instances, but a caller (or a test) may
supply one directly to drive the loop deterministically. Everything else —
prompt assembly, the loop, the access gate, Activity/Message writes, the
instance lifecycle — runs for real against the database.
"""

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from zephyrex.extensions.ai_agents.AbilityInvoker import (
    AbilityAccessDenied,
    AbilityInvoker,
    ToolInvocationError,
    ability_to_tool_schema,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityManager,
    ActivityState,
    AgentAbilityManager,
    AgentContextPromptManager,
    AgentManager,
    AgentMemoryManager,
    ConversationAgentManager,
    InvocationInstanceManager,
)
from zephyrex.extensions.ai_agents.MemoryProvider import default_memory_provider
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

# The model transport: (messages, tools) -> {"success", "message"} or
# {"success": False, "error"}.
ChatTransport = Callable[
    [List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], Awaitable[Dict[str, Any]]
]

# Bounded tool loop: how many model<->tool round trips a single turn may take
# before it is forced to conclude. Prevents a runaway tool-calling turn.
DEFAULT_MAX_STEPS = 8

# The ability whose invocation the turn's root Activity represents. Never a
# callable tool (see AbilityInvoker.NEVER_AGENT_INVOCABLE).
THINKING_TURN_ABILITY = "thinking_turn"

# The sole operator-communication ability. Handled by the executor (it creates a
# Message), not routed through the general invoker, because it needs turn context
# (conversation, instance) the invoker does not carry.
SPEAK_ABILITY = "speak"

# Memory abilities, also executor-handled ("self" abilities that operate on the
# agent's own memory): memorize (short-term key/value or long-term store), trim
# (drop short-term keys), recall (search long-term memory).
MEMORIZE_ABILITY = "memorize"
TRIM_ABILITY = "trim"
RECALL_ABILITY = "recall"

# Discovery ability: lets a turn enumerate/search the abilities it is entitled to
# use (its own granted, invocable set), so the model can find its capabilities at
# runtime rather than relying solely on the static prompt. Executor-handled: it
# needs the turn's allowlist + invoker to introspect the callable catalog.
ABILITIES_ABILITY = "abilities"

# Abilities the executor performs itself rather than routing through the general
# AbilityInvoker (they need turn/agent context the invoker does not carry).
SELF_ABILITIES = frozenset(
    {
        SPEAK_ABILITY,
        MEMORIZE_ABILITY,
        TRIM_ABILITY,
        RECALL_ABILITY,
        ABILITIES_ABILITY,
    }
)

# Reserved short-term memory keys hold executor-internal working state (e.g. the
# set of abilities discovered via `abilities`), surfaced through dedicated prompt
# placeholders rather than the general short-term memory view. They are excluded
# from {{SHORT_TERM_MEMORIES}} so they never read as ordinary agent notes.
RESERVED_MEMORY_PREFIX = "__"
SEARCHED_ABILITIES_KEY = "__searched_abilities__"

# Committed, generic fallback used when no thinking-turn prompt is configured for
# the agent (the private per-deployment prompt lives, gitignored, in the
# ai_prompts seed dir and is layered in as an agent context prompt). Uses the
# same {VARIABLE} placeholders so state injection works either way.
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


class AgentTurnExecutor:
    """Executes a single agent turn against an InvocationInstance."""

    def __init__(
        self,
        model_registry: Any,
        requester_id: str,
        chat_fn: Optional[ChatTransport] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        tool_mode: str = "native",
        memory_provider: Optional[Any] = None,
    ) -> None:
        self.model_registry = model_registry
        self.requester_id = requester_id
        self._chat_fn_override = chat_fn
        self.max_steps = max_steps
        # Long-term memory backend (SQLite by default; injectable for tests).
        self._memory_provider = memory_provider
        # "native"  -> pass a tools=[...] catalog, read structured tool_calls.
        # "in_band" -> pass no tools; the model expresses tool calls as fenced
        #              ```tool JSON blocks in its text, which we parse. Needed
        #              for models/backends without native function-calling.
        self.tool_mode = tool_mode

    # -- public entrypoint -------------------------------------------------

    async def run(self, invocation_instance_id: str) -> Dict[str, Any]:
        """Run the turn for ``invocation_instance_id`` and return a summary.

        Turn failures are captured onto the instance (status='failed', error)
        and returned as ``{"status": "failed", ...}`` rather than raised, so a
        single bad turn never crashes the driver that scheduled it.
        """
        instances = InvocationInstanceManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        )
        instance = instances.get(id=invocation_instance_id)
        instances.update(
            id=instance.id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        try:
            summary = await self._run_turn(instance)
            instances.update(
                id=instance.id,
                status="succeeded",
                completed_at=datetime.now(timezone.utc),
            )
            return {"status": "succeeded", **summary}
        except Exception as exc:  # a turn failure must not crash the driver
            logger.error(f"Agent turn {instance.id} failed: {exc}")
            instances.update(
                id=instance.id,
                status="failed",
                error=str(exc),
                completed_at=datetime.now(timezone.utc),
            )
            return {"status": "failed", "error": str(exc)}

    # -- turn body ---------------------------------------------------------

    async def _run_turn(self, instance: Any) -> Dict[str, Any]:
        agent = AgentManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        ).get(id=instance.agent_id)

        # The turn acts under the agent's own identity so tool calls and message
        # writes are ACL-scoped to what the agent's owner may reach.
        acting_requester = getattr(agent, "user_id", None) or self.requester_id

        ability_ids = AgentAbilityManager(
            requester_id=acting_requester, model_registry=self.model_registry
        ).enabled_abilities(agent.id)
        allowed = set(ability_ids.keys())

        # thinking_turn is intrinsic (not a granted tool) but its Ability id is
        # required for every turn's root Activity — ensure the row exists.
        if THINKING_TURN_ABILITY not in ability_ids:
            ability_ids[THINKING_TURN_ABILITY] = ensure_ability(
                self.model_registry, THINKING_TURN_ABILITY
            )

        invoker = AbilityInvoker(
            model_registry=self.model_registry,
            requester_id=acting_requester,
            provider_instance_resolver=self._make_provider_instance_resolver(
                acting_requester
            ),
        )

        system_prompt = self._build_system_prompt(agent, instance, acting_requester)
        in_band = self.tool_mode == "in_band"
        # Native mode passes a tool catalog; in-band mode passes none (the model
        # writes tool calls as text) and appends the in-band call convention +
        # the granted tool list to the system prompt.
        tools = None if in_band else self._build_tools(invoker, allowed)
        if in_band:
            system_prompt = system_prompt + self._in_band_tool_instructions(
                invoker, allowed
            )

        root_ability_id = self._ability_id(THINKING_TURN_ABILITY, ability_ids)
        activities = ActivityManager(
            requester_id=acting_requester, model_registry=self.model_registry
        )
        root = activities.create(
            invocation_instance_id=instance.id,
            ability_id=root_ability_id,
            title="Thinking turn",
            body="",
        )

        chat_fn = self._chat_fn_override or self._make_rotation_chat(
            agent, acting_requester
        )

        kickoff = instance.payload or "Take your turn."
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": kickoff},
        ]

        final_content = ""
        final_reasoning = ""
        spoke = False
        tool_calls_made = 0

        for _step in range(self.max_steps):
            result = await chat_fn(messages, tools)
            if not result.get("success"):
                raise ToolInvocationError(result.get("error", "model call failed"))
            message = result["message"]
            content = message.get("content")
            # Capture the model's deliberation (when a provider exposes it) so
            # the turn's thought is recorded even on a tool-call turn where
            # content is empty. In in-band mode the thought is the content with
            # the ```tool blocks stripped out.
            final_reasoning = message.get("reasoning") or final_reasoning
            messages.append(self._assistant_message_dict(message))

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
                tool_result, did_speak = await self._dispatch_tool(
                    name=name,
                    arguments=arguments,
                    allowed=allowed,
                    ability_ids=ability_ids,
                    invoker=invoker,
                    instance=instance,
                    root=root,
                    agent=agent,
                    acting_requester=acting_requester,
                )
                spoke = spoke or did_speak
                if in_band:
                    in_band_results.append(f"{name}: {tool_result}")
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": tool_result,
                        }
                    )
            if in_band:
                # No tool role without native tools: feed results back as a
                # user turn so the model can continue.
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool results:\n" + "\n".join(in_band_results),
                    }
                )

        body_sections = [
            section.strip()
            for section in (final_reasoning, final_content)
            if section and section.strip()
        ]
        activities.update(id=root.id, body="\n\n".join(body_sections) or "(no output)")
        return {
            "instance_id": instance.id,
            "root_activity_id": root.id,
            "spoke": spoke,
            "tool_calls": tool_calls_made,
        }

    # -- tool dispatch -----------------------------------------------------

    async def _dispatch_tool(
        self,
        name: Optional[str],
        arguments: Any,
        allowed: set,
        ability_ids: Dict[str, str],
        invoker: AbilityInvoker,
        instance: Any,
        root: Any,
        agent: Any,
        acting_requester: str,
    ) -> Any:
        """Dispatch one tool call. Returns ``(result_text, did_speak)``.

        Executor-handled "self" abilities (speak, memorize, trim, recall) are
        performed here (they need turn/agent context); everything else routes
        through the access-gated invoker. All are gated: a call the agent is not
        permitted is refused, recorded, and fed back to the model, never
        silently performed.
        """
        activities = ActivityManager(
            requester_id=acting_requester, model_registry=self.model_registry
        )

        if name in SELF_ABILITIES:
            if not invoker.is_allowed(name, allowed):
                activities.create(
                    invocation_instance_id=instance.id,
                    ability_id=self._ability_id(
                        name, ability_ids, fallback=root.ability_id
                    ),
                    parent_id=root.id,
                    title=f"Denied: {name}",
                    body=f"Agent is not permitted to use {name}",
                    state=ActivityState.ERROR,
                )
                return f"Refused: you are not permitted to use {name}.", False
            if name == SPEAK_ABILITY:
                return await self._do_speak(
                    arguments, instance, root, agent, acting_requester, ability_ids
                )
            if name == MEMORIZE_ABILITY:
                return (
                    self._do_memorize(
                        arguments, instance, root, agent, acting_requester, ability_ids
                    ),
                    False,
                )
            if name == TRIM_ABILITY:
                return (
                    self._do_trim(
                        arguments, instance, root, agent, acting_requester, ability_ids
                    ),
                    False,
                )
            if name == RECALL_ABILITY:
                return (
                    self._do_recall(
                        arguments, instance, root, agent, acting_requester, ability_ids
                    ),
                    False,
                )
            if name == ABILITIES_ABILITY:
                return (
                    self._do_abilities(
                        arguments,
                        instance,
                        root,
                        agent,
                        acting_requester,
                        ability_ids,
                        allowed,
                        invoker,
                    ),
                    False,
                )

        child_ability_id = self._ability_id(name, ability_ids, fallback=root.ability_id)
        try:
            outcome = await invoker.invoke(name, arguments, allowed)
        except AbilityAccessDenied as denied:
            # Security event: record it and tell the model, do not perform it.
            activities.create(
                invocation_instance_id=instance.id,
                ability_id=child_ability_id,
                parent_id=root.id,
                title=f"Denied: {name}",
                body=str(denied),
                state=ActivityState.ERROR,
            )
            return f"Refused: {denied}", False

        success = outcome.get("success")
        content = outcome.get("content") if success else outcome.get("error")
        activities.create(
            invocation_instance_id=instance.id,
            ability_id=child_ability_id,
            parent_id=root.id,
            title=str(name),
            body=self._stringify(content),
            state=ActivityState.SUCCESS if success else ActivityState.ERROR,
        )
        return self._stringify(content), False

    async def _do_speak(
        self,
        arguments: Any,
        instance: Any,
        root: Any,
        agent: Any,
        acting_requester: str,
        ability_ids: Dict[str, str],
    ) -> Any:
        """Create an operator-facing Message and record a speak Activity."""
        args = self._parse_arguments(arguments)
        # Accept "message"/"content" (native tool schema) or "body" (sentience).
        text = args.get("message") or args.get("content") or args.get("body") or ""
        if not text:
            return "Nothing was said (empty message).", False

        conversation_id = args.get("conversation_id") or self._resolve_conversation(
            instance, agent, acting_requester
        )
        activities = ActivityManager(
            requester_id=acting_requester, model_registry=self.model_registry
        )
        speak_ability_id = self._ability_id(
            SPEAK_ABILITY, ability_ids, fallback=root.ability_id
        )

        if not conversation_id:
            activities.create(
                invocation_instance_id=instance.id,
                ability_id=speak_ability_id,
                parent_id=root.id,
                title="speak",
                body=text,
                state=ActivityState.WARNING,
            )
            return "No conversation available to speak into; message not sent.", False

        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        conversations = ConversationManager(
            requester_id=acting_requester, model_registry=self.model_registry
        )
        # Agent messages carry no user_id (the convention for agent-authored
        # messages) — this is also the loop guard the conversation-message hook
        # relies on to avoid re-triggering itself on the agent's own reply.
        message_fields: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "content": text,
            "user_id": None,
        }
        # Link the message back to this turn when the field is available (added
        # to MessageModel by the ai_agents augmentation hook).
        try:
            conversations.messages.create(
                invocation_instance_id=instance.id, **message_fields
            )
        except TypeError:
            conversations.messages.create(**message_fields)

        activities.create(
            invocation_instance_id=instance.id,
            ability_id=speak_ability_id,
            parent_id=root.id,
            title="speak",
            body=text,
            state=ActivityState.SUCCESS,
        )
        return "Message sent.", True

    # -- memory (self) abilities ------------------------------------------

    @property
    def memory_provider(self) -> Any:
        """Long-term memory backend (lazily the configured default)."""
        if self._memory_provider is None:
            self._memory_provider = default_memory_provider()
        return self._memory_provider

    def _memory_manager(self, acting_requester: str) -> AgentMemoryManager:
        return AgentMemoryManager(
            requester_id=acting_requester, model_registry=self.model_registry
        )

    def _record_self_activity(
        self, name, body, instance, root, acting_requester, ability_ids, state
    ) -> None:
        ActivityManager(
            requester_id=acting_requester, model_registry=self.model_registry
        ).create(
            invocation_instance_id=instance.id,
            ability_id=self._ability_id(name, ability_ids, fallback=root.ability_id),
            parent_id=root.id,
            title=name,
            body=body,
            state=state,
        )

    def _do_memorize(
        self, arguments, instance, root, agent, acting_requester, ability_ids
    ) -> str:
        """Save a memory: short-term (keyed working memory) by default, or
        long-term (durable provider store) when ``long`` is true."""
        args = self._parse_arguments(arguments)
        key = args.get("key")
        content = args.get("body") or args.get("content") or ""
        long = bool(args.get("long"))
        if not content:
            return "Nothing memorized (empty content)."
        if long:
            self.memory_provider.store(agent.id, content, key=key)
            result = f"Stored to long-term memory{f' under {key!r}' if key else ''}."
        else:
            if not key:
                return "Short-term memory requires a key."
            self._memory_manager(acting_requester).remember(agent.id, key, content)
            result = f"Remembered {key!r} in short-term memory."
        self._record_self_activity(
            MEMORIZE_ABILITY,
            result,
            instance,
            root,
            acting_requester,
            ability_ids,
            ActivityState.SUCCESS,
        )
        return result

    def _do_trim(
        self, arguments, instance, root, agent, acting_requester, ability_ids
    ) -> str:
        """Drop short-term memory keys."""
        args = self._parse_arguments(arguments)
        keys = args.get("memories") or args.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        removed = self._memory_manager(acting_requester).forget(agent.id, keys)
        result = (
            f"Trimmed {removed} short-term memory entr{'y' if removed == 1 else 'ies'}."
        )
        self._record_self_activity(
            TRIM_ABILITY,
            result,
            instance,
            root,
            acting_requester,
            ability_ids,
            ActivityState.SUCCESS,
        )
        return result

    def _do_recall(
        self, arguments, instance, root, agent, acting_requester, ability_ids
    ) -> str:
        """Search long-term memory and return the matches to the model."""
        args = self._parse_arguments(arguments)
        query = args.get("search") or args.get("query") or args.get("about") or ""
        results = self.memory_provider.recall(agent.id, query, limit=5)
        if results:
            body = "\n".join(f"- {r.get('content','')}" for r in results)
        else:
            body = "(no matching long-term memories)"
        self._record_self_activity(
            RECALL_ABILITY,
            body,
            instance,
            root,
            acting_requester,
            ability_ids,
            ActivityState.SUCCESS,
        )
        return body

    def _do_abilities(
        self,
        arguments,
        instance,
        root,
        agent,
        acting_requester,
        ability_ids,
        allowed,
        invoker,
    ) -> str:
        """Discover the abilities this agent is entitled to use.

        Returns the agent's granted, invocable abilities (self abilities plus any
        allowlisted extension abilities), optionally narrowed by a ``search``
        term matched against each ability's name and description. Discovered
        abilities are persisted to the reserved short-term key so they surface in
        the prompt's "Searched Abilities" ({{IN_CONTEXT_ABILITIES}}) section on
        subsequent turns, per the discovery design.
        """
        args = self._parse_arguments(arguments)
        search = (
            args.get("search") or args.get("query") or args.get("about") or ""
        ).strip()

        catalog = self._discoverable_abilities(allowed, invoker)
        if search:
            needle = search.lower()
            matches = [
                d
                for d in catalog
                if needle in d["name"].lower() or needle in d["description"].lower()
            ]
        else:
            matches = catalog

        if matches:
            self._remember_searched_abilities(acting_requester, agent.id, matches)

        payload = json.dumps(matches)
        if matches:
            names = ", ".join(d["name"] for d in matches)
            body = f"Discovered {len(matches)} abilit{'y' if len(matches) == 1 else 'ies'}: {names}\n{payload}"
        else:
            body = (
                f"No abilities matched {search!r}."
                if search
                else "No abilities are currently available to you."
            )
        self._record_self_activity(
            ABILITIES_ABILITY,
            body,
            instance,
            root,
            acting_requester,
            ability_ids,
            ActivityState.SUCCESS,
        )
        return payload

    def _discoverable_abilities(
        self, allowed: set, invoker: AbilityInvoker
    ) -> List[Dict[str, Any]]:
        """The agent's granted, invocable abilities as ``{name, description,
        parameters}`` descriptors — self abilities described from their local
        signatures, extension abilities introspected via the invoker."""
        descriptors: List[Dict[str, Any]] = []
        for name in sorted(allowed):
            if not invoker.is_allowed(name, allowed):
                continue
            signature = SELF_ABILITY_SIGNATURES.get(name)
            if signature is not None:
                function = ability_to_tool_schema(name, signature)["function"]
            else:
                resolved = invoker.resolve(name)
                if resolved is None:
                    continue
                function = ability_to_tool_schema(name, resolved.method)["function"]
            descriptors.append(
                {
                    "name": function["name"],
                    "description": function["description"],
                    "parameters": function["parameters"],
                }
            )
        return descriptors

    def _remember_searched_abilities(
        self, acting_requester: str, agent_id: str, matches: List[Dict[str, Any]]
    ) -> None:
        """Merge newly discovered abilities into the reserved short-term key
        (dedup by name), so they persist into later turns' context."""
        memory = self._memory_manager(acting_requester)
        stored = memory.as_dict(agent_id).get(SEARCHED_ABILITIES_KEY)
        by_name: Dict[str, Dict[str, Any]] = {}
        if stored:
            try:
                for entry in json.loads(stored):
                    if isinstance(entry, dict) and entry.get("name"):
                        by_name[entry["name"]] = entry
            except (json.JSONDecodeError, TypeError):
                pass
        for descriptor in matches:
            by_name[descriptor["name"]] = descriptor
        memory.remember(
            agent_id,
            SEARCHED_ABILITIES_KEY,
            json.dumps(sorted(by_name.values(), key=lambda d: d["name"])),
        )

    # -- prompt + tools ----------------------------------------------------

    def _build_system_prompt(
        self, agent: Any, instance: Any, acting_requester: str
    ) -> str:
        """Assemble the turn's system prompt: base thinking-turn prompt (the
        agent's configured context prompts, else the committed default) with the
        current state injected into its {VARIABLE} placeholders."""
        base = self._agent_context_prompt(agent, acting_requester)
        if not base:
            base = DEFAULT_THINKING_TURN_PROMPT

        substitutions = {
            "CURRENT_TIME": datetime.now(timezone.utc).isoformat(),
            "INVOCATION_CONTEXT": self._invocation_context(instance),
            "RECENT_ACTIVITY": self._recent_activity(agent, acting_requester),
            "AGENT_MEMORY": self._agent_memory(agent, acting_requester),
            "ADDITIONAL_CONTEXT": instance.payload or "",
        }
        # Also fill the richer {{PLACEHOLDER}} set used by the sentience-style
        # prompt (time/cycle/sensory/previous-thought), so that prompt works
        # verbatim under the new native-tool executor.
        substitutions.update(
            self._sentience_substitutions(agent, instance, acting_requester)
        )
        return self._inject(base, substitutions)

    def _sentience_substitutions(
        self, agent: Any, instance: Any, acting_requester: str
    ) -> Dict[str, Any]:
        """Compute the sentience prompt's metadata / memory / continuity
        placeholders from real turn state."""
        import json

        instances = InvocationInstanceManager(
            requester_id=acting_requester, model_registry=self.model_registry
        )
        ordered = sorted(
            instances.list(agent_id=agent.id),
            key=lambda i: getattr(i, "created_at", None) or datetime.min,
        )
        prior = None
        for turn in ordered:
            if turn.id == instance.id:
                break
            prior = turn

        previous_thought = ""
        if prior is not None:
            acts = ActivityManager(
                requester_id=acting_requester, model_registry=self.model_registry
            ).list(invocation_instance_id=prior.id)
            roots = [a for a in acts if a.parent_id is None]
            previous_thought = roots[0].body if roots else ""

        sensory = json.dumps(
            {
                "invocation": self._invocation_context(instance),
                "message": instance.payload or "",
            }
        )
        short_term = self._memory_manager(acting_requester).as_dict(agent.id)
        # Reserved keys (executor working state) surface through their own
        # placeholders, not the general short-term memory view.
        visible_memory = {
            key: value
            for key, value in short_term.items()
            if not key.startswith(RESERVED_MEMORY_PREFIX)
        }
        return {
            "ITERATIONS": len(ordered),
            "TIMEZONE": "UTC",
            "START_TIME": datetime.now(timezone.utc).isoformat(),
            "PREVIOUS_START_TIME": getattr(prior, "started_at", "") or "",
            "PREVIOUS_END_TIME": getattr(prior, "completed_at", "") or "",
            "MAXIMUM_CONTEXT_SIZE": "unknown",
            "CURRENT_CONTEXT_SIZE": "unknown",
            "IN_CONTEXT_ABILITIES": short_term.get(SEARCHED_ABILITIES_KEY, "[]"),
            "SHORT_TERM_MEMORIES": json.dumps(visible_memory),
            "SENSORY_INPUTS": sensory,
            "ABILITY_RESULTS": "[]",
            "PREVIOUS_THOUGHT": previous_thought,
            "PREVIOUS_THOUGHTS": self._recent_activity(agent, acting_requester),
        }

    def _agent_context_prompt(self, agent: Any, acting_requester: str) -> str:
        """Concatenate the agent's linked context prompts (may be empty)."""
        try:
            links = AgentContextPromptManager(
                requester_id=acting_requester, model_registry=self.model_registry
            )
            prompts = links.prompts
            parts: List[str] = []
            for link in links.list(agent_id=agent.id):
                prompt = prompts.get(id=link.prompt_id)
                content = getattr(prompt, "content", None)
                if content:
                    parts.append(content)
            return "\n\n".join(parts)
        except Exception as exc:  # context prompts are optional enrichment
            logger.debug(f"No context prompts for agent {agent.id}: {exc}")
            return ""

    def _build_tools(
        self, invoker: AbilityInvoker, allowed: set
    ) -> Optional[List[Dict[str, Any]]]:
        """Build the model-facing tool list: the invoker's resolvable, gated
        extension tools plus the executor-handled self abilities (speak,
        memorize, trim, recall, abilities) the agent is granted — the invoker
        cannot resolve these to a callable, since the executor performs them."""
        tools = invoker.build_tools(allowed)
        for name, signature in SELF_ABILITY_SIGNATURES.items():
            if name in allowed and invoker.is_allowed(name, allowed):
                tools.append(ability_to_tool_schema(name, signature))
        return tools or None

    # -- in-band tooling (models without native function-calling) ---------

    def _in_band_tool_instructions(self, invoker: AbilityInvoker, allowed: set) -> str:
        """Appended to the system prompt in in-band mode: the concrete call
        format plus the abilities callable this cycle."""
        callable_names = sorted(
            name for name in allowed if invoker.is_allowed(name, allowed)
        )
        listed = ", ".join(callable_names) if callable_names else "(none)"
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
    def _parse_in_band_calls(text: Optional[str]):
        """Parse ```tool fenced JSON blocks into ``(id, name, args)`` tuples.

        Accepts ``{"ability"|"tool": name, "args": {...}}`` and the single-key
        ``{name: {...}}`` / ``{name: {"args": {...}}}`` shapes.
        """
        import re

        if not text:
            return []
        calls = []
        for match in re.finditer(r"```tool\b\s*(.*?)```", text, re.DOTALL):
            block = match.group(1).strip()
            try:
                obj = json.loads(block)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            name = None
            args: Dict[str, Any] = {}
            if obj.get("ability") or obj.get("tool"):
                name = obj.get("ability") or obj.get("tool")
                args = obj.get("args") or {}
            elif len(obj) == 1:
                name = next(iter(obj))
                value = obj[name]
                if isinstance(value, dict):
                    args = value.get("args", value)
            if name and isinstance(args, dict):
                calls.append((None, name, args))
        return calls

    @staticmethod
    def _strip_tool_blocks(text: str) -> str:
        """Remove ```tool blocks so the recorded thought is just the monologue."""
        import re

        return re.sub(r"```tool\b\s*.*?```", "", text, flags=re.DOTALL).strip()

    # -- state injection helpers ------------------------------------------

    def _invocation_context(self, instance: Any) -> str:
        if getattr(instance, "trigger_message_id", None):
            return f"a conversation message ({instance.trigger_message_id})"
        if getattr(instance, "invocation_trigger_id", None):
            return f"a configured trigger ({instance.invocation_trigger_id})"
        return "an ad-hoc invocation"

    def _recent_activity(self, agent: Any, acting_requester: str) -> str:
        """A short, best-effort summary of the agent's recent turns."""
        try:
            instances = InvocationInstanceManager(
                requester_id=acting_requester, model_registry=self.model_registry
            )
            recent = instances.list(agent_id=agent.id)
            recent = sorted(
                recent,
                key=lambda i: getattr(i, "created_at", None) or datetime.min,
                reverse=True,
            )[:5]
            if not recent:
                return "none yet"
            return "; ".join(f"{getattr(i, 'status', '?')} turn" for i in recent)
        except Exception:
            return "unavailable"

    def _agent_memory(self, agent: Any, acting_requester: str) -> str:
        """Long-term memory injected into the prompt: the agent's most recent
        durable memories from the memory provider (recall on demand is the
        `recall` ability)."""
        try:
            memories = self.memory_provider.recent(agent.id, limit=10)
            snippets = [m.get("content", "") for m in (memories or [])]
            return "\n".join(f"- {s}" for s in snippets if s) or "none"
        except Exception:
            return "none"

    # -- model transport ---------------------------------------------------

    def _make_rotation_chat(self, agent: Any, acting_requester: str) -> ChatTransport:
        """Default chat transport: native chat over the agent's rotation, with
        the rotation's failover across the agent's model instances. The
        tokens each turn uses are recorded against the agent's user."""

        async def chat(
            messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]
        ) -> Dict[str, Any]:
            rotation_id = getattr(agent, "rotation_id", None)
            if not rotation_id:
                return {"success": False, "error": "agent has no rotation configured"}
            from zephyrex.extensions.ai.EXT_AI import EXT_AI
            from zephyrex.logic.BLL_Providers import RotationManager

            rotation = RotationManager(
                requester_id=acting_requester,
                target_id=rotation_id,
                model_registry=self.model_registry,
            )
            try:
                answer = await rotation.arotate(
                    EXT_AI.provider_call("chat"),
                    messages,
                    tools,
                    requester_id=acting_requester,
                    ability="chat",
                )
            except Exception as exc:
                return {"success": False, "error": str(exc)}
            return {"success": True, **answer}

        return chat

    def _make_provider_instance_resolver(
        self, acting_requester: str
    ) -> Callable[[str, str], Optional[Any]]:
        """Resolve (provider_class, instance) for a provider ability the agent
        may use. Best-effort: finds an enabled provider instance whose provider
        implements the ability; returns None (→ safe tool failure) if none."""

        def resolve(extension_name: str, ability_name: str):
            try:
                registry = getattr(self.model_registry, "extension_registry", None)
                if registry is None:
                    return None
                name_map = getattr(registry, "_extension_name_map", {})
                ext_cls = name_map.get(extension_name)
                if ext_cls is None:
                    return None
                providers = [
                    p
                    for p in (getattr(ext_cls, "providers", []) or [])
                    if ability_name in getattr(p, "_abilities", set())
                ]
                if not providers:
                    return None
                from zephyrex.logic.BLL_Providers import ProviderInstanceManager

                instances = ProviderInstanceManager(
                    requester_id=acting_requester, model_registry=self.model_registry
                ).list(enabled=True)
                for provider_cls in providers:
                    pname = getattr(provider_cls, "name", "").lower()
                    for inst in instances or []:
                        if (getattr(inst, "model_name", "") or "").lower() == pname:
                            return provider_cls, inst
                return None
            except Exception:
                return None

        return resolve

    # -- misc helpers ------------------------------------------------------

    def _resolve_conversation(
        self, instance: Any, agent: Any, acting_requester: str
    ) -> Optional[str]:
        """Pick the conversation to speak into: the triggering message's
        conversation, else one the agent actively participates in."""
        trigger_message_id = getattr(instance, "trigger_message_id", None)
        if trigger_message_id:
            try:
                from zephyrex.extensions.conversations.BLL_Conversations import (
                    MessageManager,
                )

                message = MessageManager(
                    requester_id=acting_requester, model_registry=self.model_registry
                ).get(id=trigger_message_id)
                if getattr(message, "conversation_id", None):
                    return message.conversation_id
            except Exception:
                pass
        try:
            links = ConversationAgentManager(
                requester_id=acting_requester, model_registry=self.model_registry
            ).list(agent_id=agent.id, active=True)
            if links:
                return links[0].conversation_id
        except Exception:
            pass
        return None

    def _resolve_ability_id(self, name: Optional[str]) -> Optional[str]:
        """Resolve an ability name to its seeded Ability id (ROOT-scoped)."""
        if not name:
            return None
        try:
            from zephyrex.logic.BLL_Extensions import AbilityManager

            matches = AbilityManager(
                requester_id=env("ROOT_ID"), model_registry=self.model_registry
            ).list(name=name)
            return matches[0].id if matches else None
        except Exception:
            return None

    @staticmethod
    def _ability_id(
        name: Optional[str],
        ability_ids: Dict[str, str],
        fallback: Optional[str] = None,
    ) -> str:
        """Resolve an ability name to its id from the pre-built grant map,
        falling back to a provided id (typically the root's) then to the
        intrinsic thinking_turn id."""
        if name and name in ability_ids:
            return ability_ids[name]
        if fallback:
            return fallback
        return ability_ids.get(THINKING_TURN_ABILITY, "")

    @staticmethod
    def _assistant_message_dict(message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise an assistant chat message for appending to the history."""
        out: Dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        return out

    @staticmethod
    def _parse_arguments(arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str) and arguments.strip():
            try:
                parsed = json.loads(arguments)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _inject(template: str, substitutions: Dict[str, str]) -> str:
        """Fill ``{KEY}`` and ``{{KEY}}`` placeholders, then strip any unfilled
        ``{{KEY}}`` so raw templates never reach the model. Bare ``{``/``}`` (e.g.
        JSON braces in the prompt) are left untouched."""
        import re

        result = template
        for key, value in substitutions.items():
            result = result.replace("{{" + key + "}}", str(value))
            result = result.replace("{" + key + "}", str(value))
        return re.sub(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", "", result)


def _speak_signature(message: str, conversation_id: str = ""):
    """Send a message to the operator/conversation. Use only when you have
    something worth their attention.

    Args:
        message: What to say to the operator.
        conversation_id: Optional conversation to speak into; defaults to the
            turn's conversation.
    """


def _memorize_signature(body: str, key: str = "", long: bool = False):
    """Save a memory. Short-term (default) is keyed working memory; set long=true
    to store it durably in long-term memory.

    Args:
        body: The content to remember.
        key: Short-term memory key (required unless long); re-using a key
            overwrites it.
        long: Store in long-term memory instead of short-term working memory.
    """


def _trim_signature(memories: list):
    """Remove entries from short-term working memory by key.

    Args:
        memories: The short-term memory keys to drop.
    """


def _recall_signature(search: str = ""):
    """Search long-term memory for entries relevant to a query.

    Args:
        search: What to look for; empty returns the most recent memories.
    """


def _abilities_signature(search: str = ""):
    """Discover the abilities available to you. Returns each ability's name,
    purpose, and parameters so you can call it.

    Args:
        search: Optional term to filter abilities by name or purpose; empty
            returns everything you are entitled to use.
    """


# Signature sources for the executor-handled self abilities. Introspected into
# tool schemas (native mode) and discovery descriptors (the `abilities` tool);
# never called. Order controls the native tool-list order.
SELF_ABILITY_SIGNATURES: Dict[str, Callable[..., Any]] = {
    SPEAK_ABILITY: _speak_signature,
    MEMORIZE_ABILITY: _memorize_signature,
    TRIM_ABILITY: _trim_signature,
    RECALL_ABILITY: _recall_signature,
    ABILITIES_ABILITY: _abilities_signature,
}


def ensure_ability(
    model_registry: Any, name: str, extension_name: str = "ai_agents"
) -> str:
    """Get-or-create a seeded Ability row by name, returning its id.

    The turn executor relies on the ``thinking_turn`` (and, when granted,
    ``speak``) Ability rows existing so a turn's Activities can carry the
    required ``ability_id``. Ability seeding from the extension registry is not
    guaranteed to have populated a given registry, so this makes the executor
    self-sufficient: it resolves the row by name and, if absent, creates it
    (creating the owning Extension row too when necessary). Idempotent — a
    second call finds the existing row. All writes are ROOT-scoped since
    Abilities/Extensions are system entities.
    """
    from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager

    ability_manager = AbilityManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    existing = ability_manager.list(name=name)
    if existing:
        return existing[0].id

    extension_manager = ExtensionManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    ext_matches = extension_manager.list(name=extension_name)
    extension_id = (
        ext_matches[0].id
        if ext_matches
        else extension_manager.create(name=extension_name).id
    )
    return ability_manager.create(name=name, extension_id=extension_id, meta=True).id
