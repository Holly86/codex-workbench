# Codex app-server integration

## Version authority

The inspected executable is /usr/local/bin/codex and reports codex-cli 0.146.0. This local version
is the binding protocol authority for the first implementation. The official local help exposes:

    codex app-server
    codex app-server daemon
    codex app-server proxy
    codex app-server generate-ts
    codex app-server generate-json-schema

The selected application transport is the default stdio listener. Daemon, proxy, Unix socket, and
experimental WebSocket modes are not used. The current authentication/capability phase still does
not start a child process or make an app-server request.

## Generated schemas

The following commands succeeded locally:

    codex app-server generate-ts --out schemas/codex-app-server/typescript
    codex app-server generate-json-schema --out schemas/codex-app-server/json

The output contains the request/response/notification unions, v1 initialization types, v2 protocol
types, individual JSON schemas, and bundled protocol schemas. It was generated without the
--experimental flag. Generated files must not be edited. Provenance is recorded in
schemas/codex-app-server/VERSION and GENERATED.md.

For an intentional upgrade:

1. record and install the target CLI version;
2. update VERSION;
3. run scripts/update-app-server-schemas.sh;
4. review additions, removals, renamed methods, union variants, and required fields;
5. update protocol adapters and golden conformance tests;
6. run every backend/frontend check and container build;
7. document migration impact before merging.

The update script refuses to generate when the installed and recorded versions differ.

## Wire protocol

Codex 0.146.0 uses bidirectional JSON-RPC-shaped messages with the jsonrpc member omitted. Stdio is
newline-delimited JSON: exactly one JSON value per line on stdin/stdout. Requests have method,
params, and id; responses echo id with result or error; notifications have method and params but no
id. Server-initiated approval/tool requests also carry an id and require a client response.

Stdout is protocol-only. Stderr is captured independently as bounded diagnostic output and never
parsed as JSON. The concrete reader will cap line size, decode UTF-8 strictly, parse one object,
validate its envelope, and route it without blocking the stream.

## Connection initialization

Every new process connection has one handshake:

1. start codex app-server --stdio with a direct argument vector;
2. send initialize with clientInfo name codex_workbench, title Codex Workbench, and application
   version;
3. await and validate InitializeResponse;
4. send the initialized notification;
5. mark the generation ready and permit other requests.

Requests before initialization and repeated initialize are protocol errors. Experimental API
capability remains false by default. It may only be enabled after a reviewed use case and generated
schema coverage. Notification opt-out is not initially used because the reducer needs complete
lifecycle information.

The version-specific feature classification, including capabilities that are excluded,
experimental, derived, or pending a runtime probe, is in docs/app-server-capability-matrix.md.

## Request correlation

A process-generation-scoped counter allocates integer ids. The pending-call registry maps ids to
method, response type, owning operation, deadline, and a future. Sending and registry insertion are
serialized so an immediate response cannot race registration.

Result and error settle the future once. Unknown, duplicate, malformed, and late responses are
protocol anomalies. Deadlines remove pending calls; mutable calls are not automatically retried.
When the child exits, every pending call fails with process generation and exit context, but without
stderr secrets.

Server request ids are a separate namespace owned by Codex. The backend retains their native
string-or-integer representation and responds exactly once. Browser-facing approval ids are random
capabilities bound to authenticated state and never expose authority through raw JSON-RPC ids.

## Core method use

Initial production scope uses only reviewed stable methods:

- model/list for a server-derived model picker;
- thread/list and thread/read for discovery/read projections;
- thread/start and thread/resume for conversation ownership;
- turn/start for text and localImage input;
- turn/interrupt for explicit cancellation;
- approval server requests and their typed responses;
- account/rateLimits/read and account/usage/read as strictly separated projections.

Potentially destructive or broad methods such as thread/delete, filesystem writes,
thread/shellCommand, command/exec, config writes, marketplace/plugin operations, and experimental
process spawning are not generically proxied. Each future feature requires its own authorization
and UI.

## Thread and turn flow

thread/start returns a Thread and subscribes the connection to events. thread/resume reloads stored
history and also establishes active use. turn/start supplies threadId, ordered input items, selected
repository cwd, explicit model options, approval policy, and a sandbox policy. The immediate result
contains an in-progress Turn; it is not completion.

Inputs in 0.146.0 include text, image URL, localImage path, skill, mention, and other generated union
variants. Pasted or uploaded image bytes are staged server-side and sent as localImage with an
absolute path accessible only inside the backend/app-server container. The browser cannot submit
that path directly.

`turn/start` accepts a `SandboxPolicy`, while thread start/resume use the exact `SandboxMode` enum.
Approval policy is a separate `AskForApproval` value at both levels. Schema comments state that
turn overrides continue into subsequent turns. The adapter and UI must preserve those semantics;
see docs/permissions-and-approvals.md.

turn/interrupt is requested with both threadId and turnId. Success means the request was accepted;
the UI remains interrupting until turn/completed reports interrupted or another terminal status.

## Event projection

Important notifications include thread started/status/closed, turn started/completed, item
started/completed, agent-message deltas, plan and reasoning deltas, command output deltas, file
change and diff updates, token usage, warning, and error events.

Reducers apply these rules:

- threadId, turnId, and itemId scope every update that provides them;
- item/started establishes an in-progress item;
- deltas append in arrival order but remain provisional;
- item/completed replaces provisional content and is authoritative;
- turn/completed is the terminal turn authority;
- warnings are visible but do not imply terminal failure;
- error notification followed by failed turn completion is one failure, not two turns;
- unknown union variants are retained as safe generic events and counted for compatibility review.

A bounded internal fan-out creates sanitized Workbench events for authenticated WebSockets.
Backpressure disconnects slow clients. Reconnect reconstructs state with thread/read/resume rather
than replaying raw stdout.

The normalized event and activity contract is specified in docs/streaming-and-activity-model.md.
Testing, thinking, and reconnecting are Workbench aggregations, not protocol statuses.

## Approval flow

In 0.146.0, command approvals arrive through
item/commandExecution/requestApproval and file approvals through
item/fileChange/requestApproval. Other typed server requests include tool/requestUserInput,
item/permissions/requestApproval, MCP elicitation, dynamic tool calls, token refresh, and
attestation. Unsupported requests default to a typed refusal/cancel response when the schema
permits; they are never accepted implicitly.

For command and file approvals:

1. item/started makes the pending action visible;
2. the server request is validated and registered with process/thread/turn/item identity;
3. the backend emits a sanitized approval_requested event;
4. an authenticated, CSRF-protected browser submits one offered decision;
5. the backend atomically claims the pending record and writes the JSON-RPC response;
6. serverRequest/resolved closes the pending prompt;
7. item/completed supplies authoritative execution/file status.

Command decisions include accept, acceptForSession, decline, cancel, and a typed exec-policy
amendment variant as well as a typed network-policy amendment variant. File decisions include
accept, acceptForSession, decline, and cancel. Session scope and policy amendments need visibly
stronger UI treatment.

`item/permissions/requestApproval` is present, but the local feature list marks its associated
request-permissions tool under development. `item/tool/requestUserInput` is explicitly experimental.
Neither is enabled until a runtime probe and dedicated safe response path are complete.

## Usage, limits, and compaction

`thread/tokenUsage/updated` supplies exact token counters for latest and total usage plus an
optional model context window. It does not name an official current-context-used counter or a
complete turn aggregate. `contextCompaction` items are the primary compaction lifecycle signal;
`thread/compacted` is deprecated.

`account/rateLimits/read` supplies official primary/secondary windows, reset times, credits,
spend-control state, and typed exhaustion reasons; its update notification is sparse. These values
are account limits and must never be synthesized from token usage. Exact projection rules are in
docs/usage-and-rate-limits.md.

## Child supervision and shutdown

The concrete supervisor will use asyncio.create_subprocess_exec, never a shell. Startup inherits a
minimal allowlist including HOME and required runtime variables; no browser-controlled environment
or CLI option is accepted. A dedicated task owns stdin writes. Graceful application shutdown stops
new turns, cancels or resolves pending requests safely, closes stdin, waits briefly, then terminates
the child process group if required.

Unexpected EOF/exit changes readiness to failed, marks active projections disconnected, invalidates
pending approvals, and retries with capped exponential backoff plus jitter. After initialization,
reconciliation uses read/list calls and does not claim in-flight work completed.

## Compatibility tests planned

The implementation phase must add:

- fixture validation against checked-in JSON schemas;
- initialize/initialized golden exchange;
- concurrent out-of-order response correlation;
- server-request response and invalidation cases;
- partial line, invalid UTF-8, malformed JSON, oversized line, EOF, and stderr flood cases;
- child crash/restart generation isolation;
- event reducer tests for deltas versus authoritative completed items;
- localImage path containment and turn interruption.
