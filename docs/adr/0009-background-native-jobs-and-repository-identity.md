# ADR 0009: Background native jobs and repository identity

## Status

Accepted.

## Context

Clone, fetch, pull, push, and GitHub publication can outlive an HTTP request. They must not be
implemented as Codex turns, tied to a browser connection, or exposed through a generic command
surface. Repository renaming also has three independent identities: the Workbench display name,
the directory below the configured repository root, and a remote GitHub name.

## Decision

- The backend owns a closed set of typed native job kinds. A job runs independently of HTTP and
  WebSocket subscribers and is projected in snapshots and the authenticated jobs API.
- Every operation resolves a repository ID again on the server, uses fixed argument vectors without
  a shell, observes repository mutation locks, and returns bounded sanitized output.
- Job history is bounded and currently process-local. A Workbench process shutdown is an explicit
  lifecycle boundary; browser disconnect is not.
- Repository display names are stored as Workbench metadata. Directory and remote names are not
  changed by display-name rename.
- Physical directory rename remains unavailable until Codex thread working-directory references can
  be migrated or proven safe atomically. Remote rename is always a separate future operation.
- GitHub publication is capability-gated by a host-authenticated `gh` installation. Private is the
  default; public publication requires the server to validate an exact repository-name confirmation.
- Host sandbox support is practically probed and exposed only as abstract availability. Unsupported
  sandbox modes are rejected server-side and never upgraded to full access.

## Consequences

Native work consumes no model tokens and survives browser reconnects. The API does not become a
general execution facility. Process-local job history is sufficient for browser reconnects but not
for backend restarts; durable job recovery would require a later persistence and idempotency design.
