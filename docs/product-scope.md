# Product scope

## Product statement and trust

Codex Workbench is a self-hosted, browser-based, single-user client for official Codex app-server
operation against direct Git repositories below `/workspace/repositories`. It is neither an
OpenAI-compatible API facade nor a replacement for Codex.

Single-user describes account cardinality, not a trusted network. Every browser requires a valid
Workbench session. Repository content, uploads, Codex authentication, raw app-server traffic, and
approval authority remain server-side.

## Implemented phase

The completed “Managed App Server, Streaming and Permissions” vertical slice includes:

- one directly spawned, version-pinned Codex 0.146.0 app-server with bounded supervision;
- authenticated repository/thread/turn APIs and restart reconciliation;
- normalized realtime snapshot/replay streaming with sequence-gap and slow-client handling;
- explicit sandbox and approval policy controls, including confirmed Full Access plus `never`;
- replay-safe command, file-change, legacy, and permission approvals;
- deterministic Working/activity state, interruption, usage, context, rate-limit and credit views;
- private, validated multi-image uploads and official `localImage` turn inputs;
- native model/reasoning discovery and turn overrides, path-free skills/plugin discovery;
- bounded native Git operations and validated local repository creation/clone without Codex turns;
- a responsive conversation-first React UI with all authentication, empty, loading, failure,
  reconnect, approval, streaming, upload, and session-expired states;
- non-root Compose artifacts, read-only image roots, persistent targeted mounts, limits, and probes;
- deterministic fake-child tests and complete local static/test/build/audit verification.

Codex remains the thread authority. Workbench SQLite contains session digests, opaque thread to
repository references, upload metadata, and secret-free audit metadata only.

## Explicit exclusions

- generic `command/exec`, `thread/shellCommand`, or arbitrary filesystem browser routes;
- terminal, Monaco editor, file browser, force push, reset, clean, or arbitrary Git commands;
- remote-image URL ingestion and general document uploads;
- granular approval-policy objects not enabled by the reviewed UI;
- multi-user roles, password recovery/change, public SaaS operation, or deployment automation;
- raw JSON-RPC, local absolute paths, Codex credentials, or secrets in browser state.

## Success criteria

An authenticated user can select a repository, create/resume a thread, send text and screenshots,
observe normalized streaming/activity/usage, answer approvals, choose permissions including
confirmed Full Access, interrupt a turn, and recover state after client or child reconnect. All
authorization and path checks fail closed, terminal state comes only from authoritative events,
and all local verification passes.

## Open product decisions

- password change/recovery and operator account recovery;
- final retention policies for consumed uploads, audit metadata, and expired sessions;
- nested repository discovery;
- runtime enablement of granular approvals or optional network policy controls;
- reverse-proxy trust configuration, immutable image digests, SBOM and NAS soak testing;
- final visual identity and license.
