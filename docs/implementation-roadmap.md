# Implementation roadmap

## Delivery rules

Each phase preserves same-origin access, one backend process owner, generated protocol authority,
safe roots, fail-closed sessions, separate sandbox/approval controls, normalized browser DTOs, and
visible approval state. A phase is complete only with tests, strict typing/linting, threat-model
updates, dependency/secret scans, migration notes, and operator documentation.

## Phase 0 — Architecture and Foundation

Status: complete at baseline commit `51b8b86a6ff7f3e8e6ea7541cf17eb9e3b07694a`.

- Codex 0.146.0 generated schemas and version authority.
- Architecture, transport, safe paths, repository discovery, health, deployment, and UI shell.
- Disabled fail-closed app-server transport and verification foundation.

## Phase 1 — Authentication Boundary and Protocol Capabilities

Status: implemented in the current worktree; not committed.

- One-time Setup with an Argon2id verifier file and no default password.
- Keyed digest-only SQLite sessions with rotation, idle/absolute expiry, revocation, and Logout.
- HttpOnly/SameSite/Secure-cookie configuration and explicit degraded HTTP opt-in.
- Exact Origin, JSON content type, Fetch Metadata, CSRF, and authenticated WebSocket handshake.
- Per-peer/global login throttling, increasing failure delay, generic errors, and safe security
  events.
- Accessible Setup, Login, Logout, loading/error/rate-limit/expired states and protected app shell.
- Codex 0.146.0 capability matrix, permission/approval UX model, normalized streaming/activity
  model, usage/rate-limit model, and upload/localImage specification.
- Strict internal domain models. No app-server child and no productive Codex turn.

Exit gate: all non-health and non-bootstrap API surfaces fail closed; all state mutation is
session/Origin/CSRF protected; local verification passes.

## Next phase — Managed App Server, Streaming and Permissions

Status: next, not implemented.

This is one bounded vertical phase with the following required scope:

- direct-child `codex app-server --stdio` lifecycle, exact 0.146.0 version check, one
  initialize/initialized handshake, shutdown, crash backoff, and generation isolation;
- strict JSONL framing, JSON-RPC request/response/server-request correlation, deadlines, bounded
  queues/stderr, and fake-child compatibility fixtures;
- authenticated normalized WebSocket event stream with backpressure and reconnect snapshot;
- authenticated repository selection plus `thread/start`, `thread/resume`, list/read projections,
  and text `turn/start`;
- separate sandbox and approval selection at thread start and supported turn overrides;
- server-issued, one-use Full Access confirmation and permanent effective-mode warning;
- deterministic `AgentActivity` reducer with direct versus aggregate status labelling;
- command/file approval registry first, safe handling of every other server-request variant, and
  replay/race/invalidation tests;
- official thread usage, context derivation/compaction, account rate-limit read/update, credits,
  staleness, and unavailable-value handling;
- screenshot paste, multi-file picker/drop, authenticated bounded image uploads, previews/progress,
  server type/decode validation, ownership, expiry, and true `localImage` conversion;
- `turn/interrupt`, authoritative cancellation, child/WebSocket failure, reconciliation, and
  reconnect states.

Explicit exclusions: `thread/shellCommand`, generic `command/exec` HTTP exposure, arbitrary
filesystem APIs, config writes, automatic/granular approvals without a runtime decision, terminal,
editor, Git commit/push, and raw protocol delivery.

Exit gate: a fake child deterministically exercises correlation, streaming, approvals, interruption,
and reconnect; an opt-in local 0.146.0 smoke test verifies the reviewed methods; Full Access cannot
be activated from manipulated frontend data; uploads cannot escape ownership/root boundaries; no
terminal state is fabricated after failure.

## Later — Read-only developer workspace

- Git status/diff and bounded repository file tree/read APIs.
- Lazy Monaco viewer, safe search/pagination, binary and large-file handling.
- Same authenticated root resolver and untrusted-content rendering constraints.

## Later — Explicit write and terminal capabilities

- Named file edits and reviewed Git operations.
- Separately authorized PTY lifecycle and bounded output; no silent generic shell route.
- Commit workflow; push remains a separate high-risk owner decision.

## Production readiness

- Docker build/Compose/runtime validation on linux/amd64 Synology-compatible infrastructure.
- Image digests, SBOM, vulnerability scanning, backup/restore, migration rollback, log rotation.
- HTTPS proxy, Secure cookie/HSTS, trusted-forwarded-header, session, CSRF, and WebSocket testing.
- Soak tests for turns, reconnect, uploads, child crash loops, and power loss.
- Password recovery/change runbook, retention decisions, and explicit license decision.
