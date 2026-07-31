# ADR 0004: Generation-scoped bounded app-server supervision

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture

## Context

The stdio transport decision requires one owner for startup, compatibility, crash recovery and
shutdown. Automatic retries can otherwise replay authority, correlate old responses with new calls,
or create an endless NAS restart loop.

## Decision

One backend runtime directly resolves an executable regular file, runs its `--version`, and accepts
exactly 0.146.0 before spawning `codex app-server --stdio` without a shell. Each child receives a
monotonic generation. Calls and approvals carry that generation; a replacement invalidates old
authority. Initialization must finish before readiness.

Unexpected protocol failure, EOF or exit triggers capped exponential backoff within a restart
window. Exceeding the configured count leaves the supervisor failed until process/operator restart.
Shutdown closes stdin, waits, sends TERM to the child process group, then KILL only after another
bounded wait. Stderr content is never logged.

## Consequences

There must be exactly one Uvicorn worker and backend replica. Mutable calls are not blindly retried;
post-generation reconciliation reads Codex-authoritative thread state. Liveness remains available
while readiness reflects child failure.
