# Implementation roadmap

## Delivery rules

Every phase preserves same-origin access, one backend process owner, generated protocol authority,
safe configured roots, fail-closed sessions, normalized browser DTOs, explicit authority, strict
typing, tests, dependency/secret scans, migration notes, and operator documentation.

## Phase 0 — Architecture and foundation

Status: complete at baseline commit `51b8b86a6ff7f3e8e6ea7541cf17eb9e3b07694a`.

- Codex 0.146.0 generated schemas, application stack, safe paths, health and deployment foundation.
- Disabled transport contract and responsive protected-shell skeleton.

## Phase 1 — Authentication and protocol capabilities

Status: complete in baseline commit `59391991ee341d60d342b37abbad50b16779e68b`.

- Argon2id one-time setup, digest-only rotating sessions, expiry/revocation and Logout.
- Exact Origin, CSRF, content type, Fetch Metadata and WebSocket handshake boundaries.
- Capability matrix and domain contracts for permissions, events, usage and uploads.

## Phase 2 — Managed App Server, Streaming and Permissions

Status: implemented in the current uncommitted worktree.

- Direct `codex app-server --stdio`, exact version check, handshake, correlation, bounded restart,
  health/readiness, shutdown and deterministic fake child.
- Repository, thread and turn control with validated cwd and post-generation reconciliation.
- Normalized WebSocket snapshots, replay, monotonic sequence, heartbeat and backpressure.
- Separate permission selectors, short-lived one-use Full Access capability, schema-backed approval
  registry, safe unsupported-request denial, interruption and audit metadata.
- Assistant/reasoning/plan/item/command/file/usage/rate/error/terminal projection and deterministic
  activity aggregation.
- Authenticated bounded image uploads with real browser progress/cancel and `localImage` conversion.
- Conversation-first frontend with repository/thread navigation, streaming chat, activity timeline,
  approvals, usage/quota detail, reconnect and session-expired behavior.
- DS918+ deployment hardening: strict reason-coded Codex version gate, ready-only health,
  linux/amd64/1026:100 Compose invariants, CPU-CFS removal, LF-safe installer parsing, protected
  port/network topology and secrets-free diagnostics.

Exit gate: 83 backend and 9 frontend tests, strict checks, production build, audits, secret scan,
schema unchanged check, deployment/parser policy tests and diff hygiene pass locally. Docker
validation and an authenticated real model turn remain environment-dependent release gates.

## Recommended next phase — Read-only developer workspace and operational hardening

- Safe paginated repository tree, bounded text-file read/search, and Git status/diff APIs.
- Lazy read-only Monaco viewer; no write endpoint and no terminal.
- Reconnect/crash/upload soak tests, structured operational metrics and log-retention guidance.
- Synology Compose/install/runbook/test artifacts are hardened; execute and record the full
  linux/amd64 DS918+ acceptance plan before production release.
- Dedicated authenticated Codex-home smoke environment, backup/restore rehearsal, image digests,
  SBOM and vulnerability scanning.
- HTTPS reverse-proxy and trusted-forwarded-header test matrix.

## Later — Explicit write and terminal capabilities

- Named file edits and individually reviewed Git operations.
- Separately authorized PTY lifecycle with bounded output; never a generic shell HTTP route.
- Commit workflow only after an owner decision; push remains separately out of scope.

## Cross-cutting owner decisions

Password recovery/change, retention, nested repositories, multi-user scope, granular approval
objects, license, and final TLS/domain deployment remain explicit decisions rather than inferred
features.
