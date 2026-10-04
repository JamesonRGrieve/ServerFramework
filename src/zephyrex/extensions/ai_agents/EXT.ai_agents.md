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
| `event`           | on its `event_source`: `conversation_message` (a user's message in a conversation the agent sits in), `webhook` (a signed call) or `email` (mail to the trigger's address) |

A **task** is a trigger: `invocation_payload` is its instructions, `due_at`
when it is due, `priority` (1 most urgent, 5 least) how urgent. The
`InvocationMonitorService` (started through `register_services`) fires due
schedule and timer triggers, most urgent first. A seat in a conversation
(`ConversationAgent`) with `auto_respond` takes a turn on every user message.
`POST /v1/agent/{agent_id}/turn` runs a turn now. Every turn is its agent's
owner's.

### Webhooks

`POST /v1/invocation-trigger/{id}/webhook-secret` (edit on the trigger)
makes a webhook trigger's signing secret and shows it once; it is kept
encrypted (`SecretEncryption`), never serialized, and a new one replaces
it. A call to `POST /v1/invocation-trigger/{id}/webhook` sends a JSON
object and carries
`X-Zephyrex-Timestamp` (Unix seconds) and `X-Zephyrex-Signature:
sha256=<hex HMAC-SHA256 of "<timestamp>." + raw body>`. A wrong, missing,
stale (outside 300 s) or replayed signature, or an unknown trigger, is 401;
a body over 64 KiB is 413; a disabled trigger is 409. The endpoint takes
cross-site writes and never acts with a session: the body is the turn's
payload, and the turn runs as the agent's owner. It is rate-limited per
client address (60 a minute).

### Email

An email trigger gets an unguessable address at `AI_AGENTS_EMAIL_DOMAIN`
(creating one without the domain set is 422). Mail the email extension
receives (its `InboundEmail` hook point, `receive_inbound_email`) fires each
enabled email trigger it is addressed to whose `event_filter` matches:
`{"from": "<address or @domain>", "subject": "<phrase>"}`, either or both.
The message (sender, recipients, subject, text) is the turn's payload.

A turn fired by a webhook or an email is handed the trigger's instructions
(`invocation_payload`), when it has any, then the event.

## Thinking

A turn thinks on the agent's pinned provider instances
(`ProviderInstanceAgent`), in the order linked, overriding its rotation;
with none pinned, on its rotation. A pinned instance is skipped while it is
disabled, deleted or no longer visible to the agent's owner, or when its
`ProviderInstanceAgentAbility` rows (if it has any) do not allow `chat`, or
its provider does not offer it. The usable ones are rotated with the
framework's retry, failover and typed-error policy; when none is usable the
turn fails with a permanent error saying so. A pinned instance must be
visible to both whoever links it and the agent's owner.

## Access

- An agent and a project belong to their creator (ROOT and SYSTEM may name
  another owner); updates never move the owner or team.
- Everything of an agent (grants, context prompts, seats, provider instance
  links, short-term memories, triggers, turns) inherits access from it
  (`permission_references = ["agent"]`); activities inherit from their turn,
  project context and project conversations from the project. Creating any
  of them needs edit on the parent. Triggers and turns are in their agent's
  team.
- A `ProjectConversation` files a conversation in a project. Linking needs
  edit on the project and a conversation the linker sees; whoever sees the
  project sees its links.
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
  and the default routes of the link models (`/v1/project_conversation`,
  `/v1/provider_instance_agent`, ...).
- `POST /v1/agent/{agent_id}/turn` `{payload}`: the finished turn.
- `POST /v1/invocation-trigger/{id}/webhook-secret`, `POST
  /v1/invocation-trigger/{id}/webhook`: see Webhooks.
- `GET /v1/agent/{agent_id}/abilities`: the agent's tools.
- `GET /v1/activity/hierarchy/{invocation_instance_id}`: a turn's activity
  trees.

## Abilities

`list_agents`, `take_turn`, `schedule_task`, `list_tasks`, `cancel_task`,
`grant_ability`, `turn_activity`, `link_conversation`,
`unlink_conversation`, each with a required `requester_id`.
