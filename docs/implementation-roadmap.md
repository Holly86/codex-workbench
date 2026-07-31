# Implementation roadmap

## Delivery rules

Each phase must preserve same-origin access, non-root containers, one backend process owner,
generated protocol types, safe path roots, fail-closed authorization, and visible approval state.
A phase is complete only with tests, typing/linting, updated threat model, migration notes, and
operator documentation.

## Phase 0 — architecture and foundation

Status: implemented in this change.

- Local CLI/toolchain inspection and Codex 0.146.0 schemas.
- Architecture, security, integration, deployment, roadmap, and ADRs.
- Backend config/logging/health/path/repository/transport boundaries.
- Accessible responsive frontend shell and tests.
- Compose/Docker/init/verification foundation.

Exit gate: all available local checks pass; Docker-only checks are recorded for a capable host.

## Phase 1 — authenticated single-user boundary

- One-time password initialization with Argon2id verifier in a secret file.
- Server-side opaque sessions, token digests, expiry/revocation, login rate limits.
- Same-origin CSRF token flow, exact Origin/Fetch Metadata validation.
- Authenticated WebSocket handshake with exact Origin enforcement.
- SQLite migration framework and session tables.
- Login/logout/session UI and security audit tests.

Exit gate: all non-health API and WebSocket access fails closed without session/CSRF; no agent
process starts before this gate.

## Phase 2 — supervised app-server technical probe

- Direct asyncio child supervisor and strict stdio JSONL transport.
- Version check, initialize/initialized handshake, correlation/timeouts/generation isolation.
- Generated-schema validators/adapters and protocol compatibility fixtures.
- Bounded stderr logging, queue backpressure, crash/restart state.
- Read-only model/list probe behind authentication; no turn execution.

Exit gate: deterministic integration tests with a fake child plus an opt-in local Codex smoke test.

## Phase 3 — repository and thread read model

- Authenticated repository list/selection using existing safe discovery.
- thread/list, thread/read, thread/resume, pagination, and projections.
- Workbench thread cross-references in SQLite; Codex remains authority.
- Sanitized event WebSocket, reconnect snapshot, frontend repository/thread navigation.

Exit gate: restart/reconnect never loses authority boundaries or leaks absolute paths/auth state.

## Phase 4 — text turns, streaming, and interruption

- New thread plus turn/start with explicit cwd/sandbox/approval settings.
- Idempotent composer command, agent/item/delta reducers, tool activity timeline.
- turn/interrupt state and UI.
- Request/event size and rate bounds.

Exit gate: text-only turns stream end to end, terminal states reconcile after disconnect/crash, and
no approval-required action can be bypassed.

## Phase 5 — approvals

- Typed command/file/user-input/permission approval registry.
- Accessible modal/non-modal queue, action detail rendering, focus and keyboard handling.
- One-time, session-scoped, decline/cancel, expiry/invalidation, concurrent prompts.
- Atomic audit state and race/replay tests.

Exit gate: every supported server request receives exactly one valid response or a safe
cancel/decline; unsupported variants cannot execute.

## Phase 6 — attachments and true image inputs

- Authenticated streaming upload endpoints and quotas.
- Paste, picker, drag/drop, previews, removal, progress, and retry.
- Magic-byte/decode/pixel validation, random storage names, expiry/purge.
- Ownership-bound upload ids converted to official localImage turn inputs.
- Document attachment policy and file-context semantics specified separately.

Exit gate: traversal, MIME spoofing, decompression bombs, cross-session ids, expired files, and
orphan cleanup are tested.

## Phase 7 — read-only developer workspace

- Git status service and diff viewer.
- Allowed-root file tree/read APIs with binary/size handling.
- Lazy Monaco viewer integration without write capability.
- Search/pagination limits and repository-change refresh.

Exit gate: no filesystem operation escapes the selected repository and untrusted content cannot
execute in the browser.

## Phase 8 — explicit write and terminal capabilities

- Reviewed file edits and Git operations as named capabilities.
- Lazy xterm.js with a dedicated PTY lifecycle and strict authorization.
- No generic silent shell endpoint; exact user intent, session limits, output bounds, and audit.
- Commit workflow; push remains a separate high-risk product decision.

Exit gate: capability-level threat review, approvals, termination, cleanup, and NAS resource tests.

## Production readiness

- Docker build/Compose/runtime validation on linux/amd64 Synology-compatible infrastructure.
- Image digest pinning, SBOM, vulnerability scanning, backup/restore drill, log rotation.
- HTTPS proxy validation, Secure cookie/HSTS checks, session and WebSocket penetration tests.
- Load/soak tests for long turns, reconnect, upload cleanup, app-server crash loops, and power loss.
- Operator runbook and explicit license decision.
