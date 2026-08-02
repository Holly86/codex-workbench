# ADR 0002: FastAPI and React application stack

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture

## Context

The product needs asynchronous process/stream handling, typed validation, a responsive accessible
browser interface, and later large interactive components such as Monaco Editor and xterm.js. The
target NAS has four x86-64 cores and 12 GB RAM. Foundation choices must remain maintainable without
adopting a heavy UI framework or replacing the core architecture.

## Decision

Use Python 3.12, FastAPI, Pydantic 2/settings, Uvicorn, and asyncio for the backend. Use SQLite only
for Workbench metadata, with explicit migrations and one active backend worker. Use dependency-free
JSON logging initially.

Use React, strict TypeScript, Vite, semantic HTML, and authored CSS for the frontend. Avoid a large
component library in the foundation. Organize features around repositories, conversation/events,
composer/uploads, and approvals. Later Monaco and xterm.js integrations are route/panel-level lazy
chunks behind dedicated adapters.

Use a separate frontend build/container and backend build/container. An unprivileged Nginx gateway
serves static assets and is the only host-published Compose service; production binds it to host
loopback for the Synology HTTPS reverse proxy. It reverse-proxies same-origin API and WebSocket
traffic.

Pin direct dependency and image versions, commit npm package-lock.json, keep Python dependencies
project-local, and validate with tests, lint, strict typing, and production frontend build.

## Consequences

Benefits:

- asyncio matches stdio, WebSocket, and streaming workloads;
- Pydantic and TypeScript provide validation on both sides of the Workbench contract;
- static frontend delivery is lightweight for the NAS;
- same-origin gateway simplifies cookies, CSRF, CSP, and deployment;
- the component strategy allows accessibility to be designed directly;
- future editor/terminal libraries do not dictate the initial shell.

Costs:

- Python protocol types need explicit adapters or JSON Schema-based generation;
- authored accessible components require discipline and testing;
- separate build stages add container build complexity;
- one backend worker limits horizontal throughput but matches single-user scope.

## Alternatives considered

**Node backend:** viable for sharing generated TypeScript but not selected because the requested
stack and Python async ecosystem fit the service. Protocol bindings remain authoritative reference
artifacts.

**Server-rendered frontend:** reduces initial JavaScript but is a weaker fit for streamed
conversation state, approvals, file views, editor, and terminal.

**Large design-system library:** deferred because it would add weight and constrain foundation
semantics before interaction patterns are proven.

**Open WebUI or an OpenAI-compatible facade:** rejected because they do not preserve official
app-server lifecycle and approval semantics and conflict with the product boundary.

## Verification

Backend pytest/ruff/mypy and frontend Vitest/ESLint/TypeScript/Vite build are required. Container
build and Compose config are release gates on a Docker-capable linux/amd64 host.
