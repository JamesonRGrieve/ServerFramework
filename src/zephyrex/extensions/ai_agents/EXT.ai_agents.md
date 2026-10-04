# AI Agents Extension

Agents are configured identities that take turns. A turn is one
`InvocationInstance`: the agent's model is prompted with its context
prompts and state, offered the abilities it is granted as tools, and drives a
bounded tool loop (`AgentTurnExecutor`). Everything a turn does is an
`Activity` under the turn's root `thinking_turn` activity.

## What wakes an agent

An `InvocationTrigger` fires turns, many times over:

| `invocation_type` | Fires                                                                 |
|-------------------|-----------------------------------------------------------------------|
| `schedule`        | on `cron`; with `due_at`, from the first tick after it                |
| `timer`           | after and every `interval_seconds`, or once (`one_shot`) at `due_at`  |
| `event`           | on `event_source` `conversation_message`: a user's message in a conversation the agent sits in |

A **task** is a trigger: `invocation_payload` is its instructions, `due_at`
when it is due, `priority` (1 most urgent, 5 least) how urgent. The
`InvocationMonitorService` (started through `register_services`) fires due
schedule and timer triggers, most urgent first. A seat in a conversation
(`ConversationAgent`) with `auto_respond` takes a turn on every user message.
`POST /v1/agent/{agent_id}/turn` runs a turn now.

## Access

- An agent and a project belong to their creator (ROOT and SYSTEM may name
  another owner); updates never move the owner or team.
- Everything of an agent (grants, context prompts, seats, provider instance
  links, short-term memories, triggers, turns) inherits access from it
  (`permission_references = ["agent"]`); activities inherit from their turn,
  project context from the project. Creating any of them needs edit on the
  parent. Triggers and turns are in their agent's team.
- Every record a create references (rotation, prompt, conversation, provider
  instance, ability, parent) is read as the requester.
- A turn's lifecycle and a trigger's bookkeeping are written by the server
  (ROOT) only.

## Tools

An agent may use only its enabled `AgentAbility` grants, never
`NEVER_AGENT_INVOCABLE` (`thinking_turn`, `take_turn`, `grant_ability`).
A grant names an extension's static ability (`@classmethod` +
`@ability("name")`); `AbilityInvoker` resolves it on that extension and
fills `requester_id` with the agent's owner, so a turn acts with its owner's
permissions and the model never chooses whose. Abilities of the same name
from two extensions are offered as `<extension>__<name>`. `speak`,
`memorize`, `trim`, `recall` and `abilities` are performed by the executor.
Short-term memory is `AgentMemory`; long-term memory is the `ai_memories`
extension.

## Routes

- CRUD: `/v1/agent`, `/v1/invocation-trigger`, `/v1/invocation-instance`,
  `/v1/agent-ability`, `/v1/agent-memory`, `/v1/activity`, `/v1/project`,
  and the default routes of the link models.
- `POST /v1/agent/{agent_id}/turn` `{payload}`: the finished turn.
- `GET /v1/agent/{agent_id}/abilities`: the agent's tools.
- `GET /v1/activity/hierarchy/{invocation_instance_id}`: a turn's activity
  trees.

## Abilities

`list_agents`, `take_turn`, `schedule_task`, `list_tasks`, `cancel_task`,
`grant_ability`, `turn_activity`, each with a required `requester_id`.
