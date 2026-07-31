# ADR 0001: Managed stdio transport for Codex app-server

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture
- Scope: Initial single-instance backend

## Context

Codex CLI 0.146.0 supports app-server over stdio, experimental WebSocket, Unix socket, daemon, and
proxy-related modes. The Workbench needs full official thread, turn, item, event, and approval
semantics while protecting authentication state and avoiding an unauthenticated agent listener.

The target is one Compose deployment on a small NAS. Browser clients already require a Workbench
WebSocket for authenticated, sanitized UI events; they do not need direct protocol access.

## Decision

The backend will directly start one long-lived codex app-server --stdio child with
asyncio.create_subprocess_exec. Stdin/stdout carry newline-delimited JSON. The backend performs the
initialize/initialized handshake, allocates process-generation request ids, correlates responses,
handles server requests, reduces notifications, and exposes only Workbench APIs.

There is no shell invocation, no public app-server socket, and no direct browser-to-Codex channel.
Only one backend worker owns the child. The concrete supervisor is intentionally disabled until the
authenticated session boundary exists.

Generated schemas from the exact installed CLI version are checked into the repository and form the
protocol contract.

## Consequences

Benefits:

- smallest network attack surface and no second externally authenticated server;
- natural child lifecycle ownership with the backend;
- exact official protocol, including bidirectional approvals and streamed events;
- one place for schema validation, backpressure, authorization, and sanitized projection;
- simpler NAS deployment than daemon/socket coordination.

Costs:

- backend must implement robust framing, correlation, timeout, crash, and backpressure behavior;
- one app-server owner prevents multiple Uvicorn workers or replicas;
- backend restarts disconnect in-flight transport state and require reconciliation;
- stderr and stdout handling must never deadlock the child.

## Alternatives considered

**Direct app-server WebSocket from the browser:** rejected because it exposes Codex protocol and
authentication boundary, complicates origin/session authorization, and is experimental in
0.146.0.

**Backend to local app-server WebSocket:** rejected initially because it adds listener credentials,
ports, and an unsupported transport without product benefit.

**Unix socket daemon/proxy:** useful for remote/SSH management but adds external process ownership
and stale daemon coordination. Reconsider only if backend-independent lifecycle becomes required.

**One child per browser, thread, or turn:** rejected for authentication concurrency, memory,
duplicate state, and event ownership complexity.

**Codex SDK or non-interactive exec:** does not provide the rich client lifecycle/approval surface
required by this product.

## Verification

The implementation requires fake-child protocol tests, schema fixtures, concurrent correlation,
server-request races, oversized/malformed stream cases, crash-loop handling, graceful shutdown,
and an opt-in Codex 0.146.0 integration smoke test.
