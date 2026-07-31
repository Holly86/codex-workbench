# Architecture

## Context and system boundary

Codex Workbench owns browser interaction, single-user authentication, safe workspace selection,
upload staging, projection of app-server events, and Workbench metadata. OpenAI Codex owns agent
behavior, model communication, thread rollouts, tool execution, sandboxing, and its authentication
state. Git repositories and uploaded files remain filesystem resources, not SQLite blobs.

The Workbench never exposes a Chat Completions-compatible endpoint and does not reconstruct Codex
logic. Its privileged integration point is the official version-pinned codex app-server protocol.

| Boundary | Inside | Outside |
| --- | --- | --- |
| Browser trust | Rendering, ephemeral draft state, accessible controls | Secrets, raw paths, Codex auth, authorization decisions |
| Public service | Static frontend and same-origin reverse proxy | Backend and app-server ports |
| Workbench backend | Session authorization, validation, orchestration, event projection | Model/provider implementation |
| Codex | Threads, turns, items, tools, sandbox, account session | Workbench session and UI metadata |
| Allowed storage | /workspace, Workbench data, targeted Codex home | All other host paths |

## Container boundaries

The frontend container is the only externally published service. It serves immutable static assets,
security headers, `/healthz`, and proxies `/api` and `/ws` to an internal network.
The backend is not host-published. It runs as a non-root user with a read-only container root,
temporary /tmp, a read/write /workspace bind mount, a Workbench data mount, a read/write targeted
Codex-home mount, and a read-only Workbench secrets mount.

The read-only secrets mount currently supplies the high-entropy session HMAC key. The Argon2id
password verifier is created by first-start Setup below the writable Workbench state mount at a
configured mode-0600 path. Neither value is an environment secret or SQLite column.

The backend container will run exactly one supervised app-server child. Horizontal backend scaling
is disallowed until process ownership and event fan-out are redesigned; SQLite and one child
process make a single active backend replica the explicit topology.

## Backend module boundaries

| Module | Responsibility | Must not do |
| --- | --- | --- |
| config | Parse and validate non-secret environment configuration | Read browser input or secret contents |
| security | Argon2id setup, sessions, login throttling, Origin/CSRF/WebSocket and path controls | Start tools |
| repositories | Discover and resolve allowed repositories | Traverse arbitrary roots |
| app_server.transport | Move validated JSON-RPC envelopes over stdio | Interpret UI state |
| app_server.client | Initialize, allocate ids, correlate responses and requests | Serve WebSockets directly |
| domain | Thread, turn, item, approval, and connection state machines | Read raw process streams |
| api | HTTP validation and projections | Bypass domain authorization |
| realtime | Authenticated WebSocket delivery and backpressure | Expose raw secrets or process handles |
| persistence | SQLite migrations and digest-only session/metadata transactions | Become authority for Codex threads |

Authentication, its SQLite session migration, the WebSocket handshake boundary, and the disabled
transport contract now exist. App-server client, realtime fan-out, and thread reducers remain
extension points, not placeholder replacements.

## Frontend boundaries

The UI is feature-oriented: authentication gate, application shell, repositories, conversation/event timeline,
composer/attachments, approvals, and later files/diff/terminal. A typed API client and normalized
event reducer will isolate protocol-derived types from presentation. Monaco Editor and xterm.js
will be lazy-loaded into dedicated panels so their weight and lifecycle do not affect the core
conversation route.

The browser receives stable Workbench DTOs. Generated Codex TypeScript bindings are reference and
backend integration inputs; raw protocol objects are not a public browser contract.

The authentication gate owns only in-memory CSRF/session presentation state. It renders loading,
Setup, Login, rate-limit, error, Session Expired, and Logout transitions before mounting the app
shell. Password and CSRF values are never written to browser storage.

## Primary data flows

### First-start setup and session

1. Public `GET /api/auth/status` reports Setup required, unauthenticated, or authenticated. It never
   returns verifier, session key, cookie token, account record, or Codex auth state.
2. Setup and Login accept JSON only and require the exact configured Origin plus compatible Fetch
   Metadata. Setup creates one Argon2id verifier file with exclusive creation; the file's existence
   permanently closes Setup.
3. Successful Setup/Login revokes any presented session, creates a new opaque token, stores only
   its keyed SHA-256 digest and expiry metadata in SQLite, and sends an HttpOnly cookie.
4. Authenticated status returns an HMAC-derived session-bound CSRF token to page memory. Every
   privileged mutation requires it in `X-CSRF-Token` in addition to session and Origin.
5. Logout revokes the database row and clears the cookie. Idle/absolute expiry is checked and
   persisted server-side. A WebSocket is accepted only after the same cookie and exact Origin pass.

### Repository selection

1. An authenticated browser asks for repositories.
2. The backend enumerates only direct non-symlink Git directories below the configured repository
   root and returns opaque id, name, and relative path.
3. Selection resolution canonicalizes the relative path, resolves symlinks, proves containment,
   confirms Git identity, and stores only the relative selection as Workbench metadata.
4. The resolved absolute path is passed server-side as app-server cwd. It is never trusted when
   round-tripped from the browser.

### New thread and turn

1. The browser submits text plus server-issued upload ids and an idempotency key.
2. The backend authenticates the session, checks CSRF/Origin, authorizes the repository and uploads,
   and rejects a concurrent start when the thread state disallows it.
3. For a new conversation, the app-server client sends thread/start and records the returned Codex
   thread id. For an existing conversation it first uses thread/resume when not loaded.
4. It sends turn/start with the thread id, validated cwd, explicit sandbox/approval settings, and
   text/localImage inputs.
5. The response supplies the initial turn. Notifications then drive the timeline until
   turn/completed. SQLite stores only Workbench projections and cross-references.

### Event streaming

One stdout reader decodes newline-delimited JSON and validates envelope shape. Responses are routed
to pending futures by request id. Server-initiated requests go to the approval/request registry.
Notifications go to a per-thread reducer and a bounded event fan-out.

An authenticated same-origin WebSocket carries Workbench event envelopes with a connection-local
monotonic sequence, event type, thread/turn/item ids, and sanitized payload. Slow clients are
disconnected rather than allowing unbounded queues. Item completed events are authoritative;
deltas are append-only display hints. After reconnect the client requests a fresh thread projection
instead of assuming every delta can be replayed.

### Screenshot and upload

1. Paste, picker, or drag-and-drop creates a browser preview from the local File object only.
2. The browser uploads multipart bytes to a size-limited authenticated endpoint with CSRF.
3. The backend streams to a bounded temporary file, ignores the supplied storage name, computes a
   digest, checks declared and detected type, decodes images to enforce pixel limits, and atomically
   moves accepted content under data/uploads using a random server name.
4. SQLite stores owner session, safe display name, detected MIME, size, digest, expiry, and state.
5. The composer sends an opaque upload id. The backend proves ownership/state and converts an image
   to the official localImage input path visible to the app-server child.
6. Rejected, expired, orphaned, or consumed temporary files are purged. Paths and file bytes are
   never returned as credentials or arbitrary filesystem selectors.

## Thread and turn lifecycle

Workbench thread projection states are unloaded, loading, idle, running, waiting_for_approval,
interrupting, completed, failed, and disconnected. Codex notifications remain authoritative.

- thread/start creates and subscribes; thread/resume reloads an existing Codex id.
- turn/start is accepted only from idle/completed/failed projection states.
- item/started creates visible work; deltas update an in-progress item; item/completed replaces it
  with authoritative final state.
- A server request pauses only the associated action and creates an approval record.
- turn/interrupt is idempotently requested from running or waiting_for_approval. Final state changes
  only on turn/completed with interrupted status or a terminal failure.
- thread/status/changed and thread/closed update loaded state. Reconnect uses thread/read or resume.

A backend restart marks in-memory running projections disconnected. It starts a fresh app-server,
initializes once, loads persisted Workbench references, and reconciles each visible thread with
thread/read/list. It never fabricates a successful terminal event.

## App-server process model

The future supervisor uses asyncio.create_subprocess_exec with an argument vector equivalent to
codex app-server --stdio. It never uses a shell. It supplies an allowlisted environment, fixed home,
separate stdin/stdout/stderr pipes, a bounded outbound queue, line and message limits, and graceful
shutdown followed by time-bounded termination.

Startup states are stopped, starting, initializing, ready, draining, failed, and backoff. Ready
requires a live child and successful initialize/initialized handshake. Unexpected exit fails all
pending requests with a typed transport error, invalidates approvals, emits a connection event, and
uses capped exponential backoff with jitter. A crash loop makes readiness fail and requires an
operator-visible reason.

Only one process generation may own the transport. Every pending call and server request carries
that generation so late messages from an old process cannot resolve new state.

## JSON-RPC correlation

The app-server wire format omits the jsonrpc member and uses one JSON object per stdout line.
Workbench allocates monotonically increasing integer ids per process generation. A pending map
records id, method, creation deadline, expected response validator, and owning operation.

- A result or error with an unknown id is logged as protocol anomaly without user data.
- Duplicate responses cannot settle a future twice.
- Timeouts remove the pending entry; late responses are ignored and counted.
- Notifications have method and params but no id.
- Server requests have method, params, and a server-owned id. Workbench preserves that id only
  server-side and responds exactly once after an authenticated approval decision.
- Correlation ids are not browser authority. The browser uses a separate random approval id bound
  to session, process generation, Codex request id, thread, turn, item, expiry, and state.

## Persistence model

Codex-home remains the authority for Codex authentication and thread rollout/history. Workbench
SQLite uses WAL mode and explicit forward migrations. It currently contains schema versions and
server-side sessions; later migrations may contain:

- schema version and instance metadata;
- server-side session rows containing only keyed token digests, timestamps, revocation, and keyed
  client-metadata digests (implemented);
- repository selection and UI preferences;
- Codex thread-id cross-reference and last observed projection metadata;
- approval audit transitions without secrets or full command output;
- upload metadata and expiry;
- idempotency keys and coarse operational audit events.

Password verifier, session HMAC key, Codex credentials, API keys, and uploaded content are not
stored in SQLite. Browser storage contains no secrets; drafts remain in memory unless a later
encrypted server-side design is accepted.

## Failure and recovery

Malformed JSON, oversized lines, schema mismatch, EOF, and child exit are explicit transport
failures. API errors use stable machine codes plus safe messages and request ids. Logs omit prompts,
file contents, cookies, authorization headers, secrets, and full environment dumps.

SQLite transactions protect state transitions. Uploads use temporary files plus atomic rename.
Startup currently runs session migrations, validates restrictive session-key/verifier files, and
revokes expired sessions before security readiness succeeds. Future power-loss reconciliation also
expires approvals/uploads and reconciles Codex threads. Codex requests are not blindly
retried: read-only methods may retry; thread/turn mutations require an idempotency/reconciliation
strategy.

## Implemented boundary and extension points

- Implemented: Auth/session, JSON content-type, exact Origin/Fetch Metadata, CSRF middleware, and
  WebSocket pre-accept authentication.
- Concrete supervised stdio client generated-schema conformance tests.
- Repository API and thread projection service.
- Upload scanner and localImage adapter.
- Git read service, then diff viewer and explicit write operations.
- File service using the same allowed-root resolver.
- Lazy Monaco and xterm.js panels with separately authorized capabilities.
- Reverse proxy trust configuration for HTTPS, without changing application trust boundaries.

Permission, streaming, usage, and upload contracts are specified in
docs/permissions-and-approvals.md, docs/streaming-and-activity-model.md,
docs/usage-and-rate-limits.md, and docs/uploads-and-image-inputs.md.
