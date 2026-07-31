# ADR 0005: Per-session snapshot and bounded realtime replay

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture

## Context

Token and activity deltas must be responsive, but WebSockets disconnect and browser clients can be
slow. Raw protocol replay would expose unstable shapes and unbounded content.

## Decision

The domain projection publishes sanitized Workbench envelopes into a per-session broker. Sequence
ids are monotonic per session. The broker retains a bounded history and gives each subscriber a
bounded queue. Queue overflow closes only the slow subscriber.

After cookie/Origin authentication, the first client frame must repeat session CSRF and an optional
last sequence. The server always sends a complete current snapshot, reports whether history was
missed, and replays only retained later envelopes. The client reconnects on sequence gaps. Heartbeat
frames revalidate the session; no token appears in a URL.

## Consequences

History is recovery assistance, not durable audit. A snapshot may supersede provisional deltas.
Raw JSON-RPC never becomes a browser contract, and slow clients cannot block the app-server reader.
