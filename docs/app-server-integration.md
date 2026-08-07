# Codex app-server integration

## Version authority

The inspected executable is /usr/local/bin/codex and reports codex-cli 0.146.0. This local version
is the binding protocol authority for the first implementation. The official local help exposes:

    codex app-server
    codex app-server daemon
    codex app-server proxy
    codex app-server generate-ts
    codex app-server generate-json-schema

The selected transport is explicit `--stdio`. Daemon, proxy, Unix socket, and app-server WebSocket
modes are not used. One managed child starts only after the Workbench security service is ready.

The runtime compatibility probe preserves the validated npm launcher path
`/usr/local/bin/codex` and directly executes only `--version`. After trimming outer whitespace it
accepts exactly one UTF-8 line `codex-cli 0.146.0`; a final LF or CRLF is equivalent. Missing
`codex-cli`, `codex 0.146.0`, a bare version, extra stdout lines, a non-zero exit, any non-whitespace
stderr, timeout or another semantic version fail closed. There is no substring, prefix-only,
lexicographic or multi-line-last-value fallback.

The operator-safe failure taxonomy is `binary_not_found`, `execution_failed`,
`malformed_version_output`, `unsupported_version`, `timeout`, `permission_denied` and
`unexpected_stderr`. Logs never include captured output, executable paths, auth state or raw
environment values. This also means a known harmless stderr warning is not mistaken for a version;
its underlying temp/home/permission condition must be corrected before readiness.

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
newline-delimited JSON: exactly one JSON value per line on stdin/stdout. Requests have method and
id, plus `params` only when their generated request variant defines it; responses echo id with
result or error; notifications have method and usually params but no id. Server-initiated
approval/tool requests also carry an id and require a client response.

Stdout is protocol-only. Stderr is drained independently, line-bounded and logged only as a
content-free redacted diagnostic marker plus byte count. The reader caps line size, decodes UTF-8
strictly, parses one object, validates its envelope, and routes it through a bounded queue.

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
method, owning operation, generation, and a future; the awaiting call enforces its deadline. Sending and registry insertion are
serialized so an immediate response cannot race registration.

Result and error settle the future once. Unknown, duplicate, malformed, and late responses are
protocol anomalies. Deadlines remove pending calls; mutable calls are not automatically retried.
When the child exits, every pending call fails with process generation and exit context, but without
stderr secrets.

Server request ids are a separate namespace owned by Codex. The backend retains their native
string-or-integer representation and responds exactly once. Browser-facing approval ids are random
capabilities bound to authenticated state and never expose authority through raw JSON-RPC ids.

## Core method use

This production phase uses only reviewed methods:

- thread/list and thread/read for discovery/read projections;
- thread/start and thread/resume for conversation ownership;
- turn/start for text and localImage input;
- turn/interrupt for explicit cancellation;
- approval server requests and their typed responses;
- account/rateLimits/read for the account-limit projection.

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

`item/permissions/requestApproval` has a dedicated conservative projection: the browser can accept
or decline, and acceptance returns only the server-retained permission object for turn scope.
`item/tool/requestUserInput` remains experimental and is safely answered with no answers. MCP
elicitation, dynamic tool calls, attestation/token-related and unknown requests are declined or
errored using a method-specific safe response; none is implicitly accepted.

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

The supervisor uses asyncio.create_subprocess_exec, never a shell. Startup inherits a
minimal allowlist including HOME and required runtime variables; no browser-controlled environment
or CLI option is accepted. A dedicated task owns stdin writes. Graceful application shutdown stops
new turns, cancels or resolves pending requests safely, closes stdin, waits briefly, then terminates
the child process group if required.

The Synology backend image includes Bash, CA certificates, Git, Make, npm, OpenSSH client, procps
and ripgrep so the pinned Codex binary and repository-oriented turns do not depend on tools from
the existing SSH container. The image remains non-root and read-only; only explicit workspace,
Workbench-data, Codex-home and bounded tmpfs paths are writable. `HOME=/home/workbench`,
`CODEX_HOME=/home/workbench/.codex` and `TMPDIR=/tmp` are explicit. Only the backend enables Docker
Init, since the Python image has no competing init and owns children; neither image contains
s6-overlay. SIGTERM and a 30-second grace period complement the supervisor's own process-group
shutdown. A version-probe timeout kills the entire isolated probe process group as well.

The production mount intentionally points at the existing Synology Codex home. That shares account
and Codex-owned thread state but does not add locking between an SSH CLI process and Workbench.
Operators must serialize active use and quiesce both processes for a consistent backup. App-server
and its commands also share the backend UID, so Full Access does not isolate Workbench secrets from
the child; this deployment risk is documented in the security model.

Unexpected EOF/exit changes readiness to failed, marks active projections reconnecting, invalidates
pending approvals, and retries with capped exponential backoff and a bounded restart window. After initialization,
reconciliation uses read/list calls and does not claim in-flight work completed.

## Compatibility tests

The deterministic fake executable implements the exact version and app-server argument startup, handshake,
out-of-order responses, assistant/reasoning/plan/command/file/usage/rate notifications, approvals,
completion, interrupt, malformed JSON, clean EOF and crash scenarios. Tests cover request timeout
and cancellation, unknown ids, generation restart/restart limit, version mismatch, normalized
snapshots/replay/gaps/slow clients, approval replay/invalidation, Full Access binding, upload
validation/cancellation/symlink replacement, and authoritative terminal activity. Generated schema
directories are separately required to remain byte-for-byte unchanged in normal verification.

Version-specific regressions cover no-newline, LF, CRLF and outer-whitespace success plus wrong
version, missing/wrong prefix, extra stdout, stderr warning, missing binary, permission denial,
timeout and non-zero exit. Health tests prove 200 only after successful initialize and 503 after a
failed compatibility probe.

## Repository-contained real runtime smoke

`PYTHONPATH=backend/src backend/.venv/bin/python scripts/smoke-app-server.py` forces `HOME`,
`CODEX_HOME`, `TMPDIR`, and its Git repository into an automatically removed temporary directory
below this repository. It never uses the operator's existing Codex home. Against the installed
0.146.0 executable it verified version, initialize/initialized and thread/start. Account and rate
limits were unavailable because the isolated Codex home intentionally had no credentials; the
read-only `never` turn started but could not reach authoritative completion and was reported as
`unavailable_without_repository_local_auth`. An authenticated completed-turn smoke therefore
remains a release-host check using a dedicated test Codex home, not the operator's live home.
