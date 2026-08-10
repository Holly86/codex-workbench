# Contributing

Codex Workbench is preparing its first public release. Keep changes focused, typed, tested, and
consistent with the boundaries in `AGENTS.md` and `docs/security-model.md`.

## Development setup

Use Python 3.12, Node.js 24, and npm 11, then install repository-local dependencies:

```sh
make bootstrap
make verify
```

See [docs/development.md](docs/development.md) for local runtime configuration. Do not use real
Codex authentication or production state in tests. Protocol changes require fake-child coverage;
generated Codex schemas must be updated only with `make schemas` during an intentional pinned CLI
upgrade.

## Quality and pull requests

- Preserve strict Python and TypeScript typing, security boundaries, accessibility, responsive
  behavior, and the one-worker/one-App-Server-child runtime model.
- Add tests for externally visible behavior and security-sensitive changes.
- Run `./scripts/verify.sh` and `git diff --check` before opening a pull request.
- Describe behavior, risks, tests, and any operator-facing migration. Keep pull requests small enough
  to review and never combine generated schema changes with unrelated work.
- Do not commit generated build output, local state, credentials, or real `.env` files.

Fork pull requests must not require repository secrets. Never ask maintainers to run untrusted pull
request code with privileged tokens.

## Security findings

Do not open a public issue containing vulnerability details or proof-of-concept secrets. Follow
[SECURITY.md](SECURITY.md). If no private reporting route is available, wait for the maintainer to
publish one rather than disclosing sensitive material publicly.
