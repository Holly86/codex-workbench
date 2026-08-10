# Security Policy

## Supported versions

No public version is supported yet. After the first release, the latest `0.1.x` patch release is
intended to receive security fixes; older prereleases and development snapshots are unsupported.

## Reporting a vulnerability

Do not post vulnerability details, credentials, authentication files, prompts, uploaded content,
or exploit material in a public issue.

A private public-facing security contact has not yet been configured. Before the repository is made
public, the maintainer must enable GitHub private vulnerability reporting or publish a dedicated
security contact and update this section. Until then, do not publish sensitive details.

When a private channel exists, include the affected version, impact, prerequisites, and a minimal
reproduction that contains no real secrets or personal data. Maintainers should acknowledge the
report, coordinate remediation and disclosure, and credit the reporter if requested.

## Operator security responsibilities

Codex Workbench is a single-user self-hosted service. Operators must provide the authentication and
network boundary: initialize on a trusted network, use a narrowly configured HTTPS reverse proxy,
restrict host filesystem permissions, apply updates, and keep backups.

Full Access (`danger-full-access`) deliberately grants far-reaching rights to Codex-launched code.
That code can access resources readable by the backend user, including mounted workspace and Codex
state. Use it only when the repository and requested action are trusted.

The deployment does not need or mount the Docker socket. Do not add it. Do not store API keys,
Codex/ChatGPT authentication, GitHub or Docker Hub tokens, session keys, password verifiers, SSH
keys, cookies, or real `.env` files in the repository. Keep credentials out of build arguments,
image layers, logs, screenshots, issue reports, and support bundles.

The detailed threat model and remaining risks are documented in
[docs/security-model.md](docs/security-model.md).
