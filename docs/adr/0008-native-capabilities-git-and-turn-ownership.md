# ADR 0008: Native capabilities, Git boundary, and backend-owned turns

- Status: Accepted
- Date: 2026-08-10

## Context

The conversation UI needs model/reasoning selection, discoverable skills/plugins, and Git actions
that do not consume a Codex turn. Browser disconnects must never become an implicit stop command.

## Decision

Codex 0.146.0 remains the only authority for model and capability catalogs. Workbench calls
`model/list`, `skills/list`, and `plugin/list`, returns only allowlisted display metadata, and drops
all filesystem paths. The selected model and schema-listed reasoning effort are sent as `model` and
`effort` on `turn/start`; no model names are hardcoded. Experimental Apps remain disabled until a
separate product gate is reviewed.

Direct Git is a separate backend service. Routes accept an opaque repository id and a typed action,
never a command or working directory. The service re-resolves the repository, uses fixed argument
vectors with `create_subprocess_exec`, disables prompts, applies time/output bounds, validates
repository-relative paths and remote URLs, redacts credentials, and serializes mutations per
repository. It never invokes app-server. Force push, reset, clean, arbitrary config/environment and
generic commands are excluded.

The backend and managed app-server own turns. Closing a WebSocket only closes its bounded broker
subscription. Only the authenticated, CSRF-protected interrupt route calls `turn/interrupt`.
Snapshots and reconciliation restore browser state after navigation, reload, or reconnect.

GitHub publication is capability-gated. The browser can see only whether an integration is safely
available; credentials are never accepted by a normal API field. Publication remains unavailable
until `gh` authentication can be probed without exposing its output and public publication receives
a dedicated repository-name confirmation flow.

## Consequences

Model, reasoning, skills, and plugins cannot be shown while app-server is unavailable. Repository
creation and clone can operate without model tokens. Local rename remains blocked until thread
metadata and Codex rollout-path migration have a proven atomic design.
