# Streaming and activity model

## Normalized event boundary

One future app-server reader validates JSONL envelopes and routes responses, server requests, and
notifications separately. A domain reducer then emits stable Workbench events. The browser never
subscribes to raw JSON-RPC payloads or generated Codex unions.

Every browser event has a connection-local monotonic sequence, observation time, normalized type,
and optional thread/turn/item identity. The typed event family in
`backend/src/codex_workbench/domain/events.py` currently defines assistant/reasoning deltas, plan
replacement, tool activity, approval lifecycle, usage, activity, turn lifecycle, connection state,
errors, and compatibility-unknown events.

| Codex input | Normalized output | Reducer rule |
| --- | --- | --- |
| `item/agentMessage/delta` | `assistant_text_delta` | Append as provisional display content. |
| reasoning summary events | `reasoning_summary_delta` | Preserve summary index; only show delivered text. |
| `turn/plan/updated` | `plan_updated` | Replace the plan snapshot. |
| `item/started` / `item/completed` | tool/activity or timeline events | Started is provisional; completed item is authoritative. |
| command output delta | bounded tool output update | Append in order; never log or replay as security metadata. |
| file patch and turn diff updates | editing/diff update | Replace current patch/diff projection. |
| approval server request / resolved notification | approval requested/resolved | Browser sees only sanitized random approval id. |
| token usage update | `usage_updated` | Replace named usage sub-snapshots. |
| `turn/completed` | `turn_lifecycle` | Terminal authority: completed, failed, or cancelled. |
| `error` | `error` | Keep retry flag; deduplicate against failed completion. |
| unknown union variant | `compatibility_unknown` | Retain method identity and count; do not forward raw payload. |

Slow WebSocket clients are disconnected rather than receiving an unbounded queue. A reconnect asks
for a fresh authenticated thread projection. Deltas are display hints and are not promised as a
durable replay log.

## Activity aggregation

`AgentActivityState` is explicitly a Workbench enum. It is not serialized to Codex and must not be
described as an app-server status. Mapping rules are deterministic and contain no progress
percentage.

| Workbench state | Evidence | Nature |
| --- | --- | --- |
| `idle` | `ThreadStatus.type = idle` and no active turn | direct projection |
| `starting` | accepted Workbench start operation before `turn/started` | Workbench lifecycle |
| `thinking` | active turn without a more specific current item/delta | aggregation |
| `planning` | plan item or `turn/plan/updated` | derived from official event |
| `reading` | command action `read`/`listFiles` | derived classification |
| `searching` | command action `search` or web-search item | derived classification |
| `executing` | command, MCP, dynamic tool, hook, or collaboration item | derived classification |
| `editing` | active file-change item or patch update | derived classification |
| `testing` | reviewed test/build command classifier matches current command | Workbench heuristic, otherwise `executing` |
| `awaiting_approval` | approval server request or `waitingOnApproval` active flag | direct projection plus registry |
| `responding` | current assistant message delta/item | derived from official event |
| `completed` | successful `turn/completed` | direct terminal mapping |
| `failed` | failed completed turn or unrecoverable transport projection | direct/Workbench failure mapping |
| `cancelled` | completed turn status `interrupted` | direct terminal mapping |
| `reconnecting` | managed child/WebSocket recovery state | Workbench transport state |

Specific active work outranks generic thinking. Awaiting approval outranks other non-terminal
states. Terminal states remain until a new turn starts. Reconnecting never fabricates a terminal
result, and `thread/environment/disconnected` is not treated as an app-server transport disconnect.

An `AgentActivity` snapshot may contain a human label, last relevant activity, start and observation
times, elapsed milliseconds computed from monotonic server timestamps, current tool/command,
pending approval id, and a safe error. Commands and paths are sanitized before browser delivery.
No percentage, remaining-duration estimate, animated fake step, or inferred model-internal thought
is produced.

## Tests and builds

Codex 0.146.0 has no first-class test or build event. The future classifier may recognize an
allowlisted executable/subcommand from the parsed command and command actions. This changes only
the UI label to `testing`; it does not change authorization, approval, or terminal success. Exit
status and completed command item remain authoritative. An unrecognized command stays
`executing`.

## Connection and error semantics

Transport `connecting`, `connected`, `reconnecting`, `disconnected`, and `failed` are Workbench
states emitted by the future supervisor. Unexpected EOF fails pending calls, invalidates approvals,
marks active projections disconnected, and starts capped backoff. The frontend displays the stale
snapshot as stale and waits for reconciliation via thread read/resume. It never interprets silence
as completion.
