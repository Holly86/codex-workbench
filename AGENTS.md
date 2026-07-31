# Repository guidance

## Scope

Codex Workbench is a Python 3.12 / FastAPI backend and a strict TypeScript / React frontend.
Changes must remain inside this repository. Do not deploy, restart an existing Codex container,
commit, or push unless a later user request explicitly asks for it.

## Architectural boundaries

- Browser code talks only to the Workbench backend through same-origin HTTP or WebSocket routes.
- Only the backend may access Codex authentication state or communicate with codex app-server.
- Codex app-server is a managed direct child process over stdio JSONL. Never invoke it through a
  shell and never expose its stdio or authentication files to the browser.
- Keep JSON-RPC transport, protocol correlation, domain state, HTTP/WebSocket delivery, and
  persistence as separate modules.
- SQLite is for Workbench metadata only. Codex remains the authority for Codex threads.
- Every path originating at an API boundary is relative to a configured root, canonicalized, and
  checked after symlink resolution.
- New privileged endpoints require the authenticated-session and CSRF/Origin boundary described in
  docs/security-model.md. Until that boundary exists, remain fail-closed.
- Shell or tool actions always require explicit protocol methods and visible approval state. Never
  add a generic shell HTTP endpoint.

## Generated protocol artifacts

Everything below schemas/codex-app-server/typescript and schemas/codex-app-server/json is generated.
Do not edit it manually. The pinned generator version is in schemas/codex-app-server/VERSION.
Regenerate with make schemas and review the entire schema diff on every Codex upgrade.

## Local commands

Bootstrap project-local dependencies:

    make bootstrap

Run all locally available checks:

    make verify

Individual checks:

    make backend-test
    make backend-lint
    make backend-typecheck
    make frontend-test
    make frontend-lint
    make frontend-typecheck
    make frontend-build

Docker Compose validation, when Docker is installed:

    docker compose --env-file .env.example config --quiet

## Change quality

- Add or update tests for security boundaries and externally observable behavior.
- Keep Python typing strict and TypeScript strict; do not silence errors with broad Any types.
- Preserve accessibility landmarks, labels, focus behavior, keyboard operation, reduced motion, and
  responsive layouts.
- Do not log prompts, uploaded file contents, authentication material, cookies, CSRF tokens, or raw
  app-server environment variables.
- Finish with the relevant tests, secret-pattern scan, and git diff --check.
- The repository currently has no selected license. Do not add a LICENSE without an owner decision.
