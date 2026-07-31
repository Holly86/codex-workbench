# ADR 0006: One-use Full Access confirmation capability

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture

## Context

`danger-full-access` is a valid Codex mode and must remain usable, including with approval policy
`never`, while manipulated frontend state must not activate it silently.

## Decision

The UI keeps sandbox and approval policy separate. Selecting Full Access opens a modal naming the
repository, exact mode and policy and requires an acknowledgement checkbox. An authenticated,
Origin/CSRF-protected prepare request issues a random, short-lived, one-use capability bound to the
session/single user, repository, exact thread or pending-thread scope, mode and policy.

Thread start/resume or a transition into Full Access atomically consumes the capability. Missing,
expired, replayed or altered values fail closed. The backend constructs protocol policy and writes a
secret-free audit event. Effective Full Access remains visibly badged; no global grant is stored.

## Consequences

Full Access is consciously available rather than morally blocked. `danger-full-access` plus `never`
can run without per-action dialogs after confirmation. New threads and policy-changing transitions
need new capabilities.
