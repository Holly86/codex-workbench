# Codex Workbench v0.2.2

Codex Workbench v0.2.2 completes the current self-hosted reliability cycle and makes active Codex work visible directly in the project list.

## What’s new

- Repositories now show a compact **Läuft** indicator whenever at least one thread in that repository has an active Codex turn.
- Activity is visible for selected and non-selected repositories, updates in real time, survives reconnects and reloads, and correctly aggregates multiple simultaneous turns.
- The indicator uses the existing Workbench status styling and includes an explicit accessible label rather than relying on color alone.

## Self-hosted and NAS compatibility

This release includes the v0.2.1 runtime identity hotfix for arbitrary numeric non-root UID/GID combinations. The backend uses `libnss-wrapper` to provide process-local NSS identity data without modifying `/etc/passwd` or requiring root startup. Python identity lookup, OpenSSH, Ed25519 `ssh-keygen`, Git Deploy Keys, and private repository clones therefore work with NAS identities such as the verified Synology `1026:100` deployment.

## Security boundaries retained

- non-root backend and frontend execution
- read-only root filesystems
- all Linux capabilities dropped
- `no-new-privileges` enabled
- no Docker socket and no privileged containers
- backend remains private; frontend publishes only `127.0.0.1:8085`
- private Deploy Keys remain isolated from browser responses and logs

## Verification

The release is covered by the full backend suite, Ruff, strict MyPy, ESLint, strict TypeScript, Vitest, Chromium Playwright, real-network WebSocket tests on Python 3.12/3.13/3.14, secret scans, dependency audits, deployment-policy checks, Compose invariants, Codex 0.146.0 schema verification, and the runtime identity matrix for `1000:1000`, `1026:100`, and `12345:23456`.

## Container images

- `holly86/codex-workbench-backend:0.2.2` — digest added after publication
- `holly86/codex-workbench-frontend:0.2.2` — digest added after publication

Both images target `linux/amd64` and are published with OCI metadata, provenance, and SBOM attestations.
