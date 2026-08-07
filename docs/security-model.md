# Security model

## Security objective

A browser on the local network must not gain repository, tool, shell, upload, or Codex-account
access without an authenticated Workbench session and an explicit authorized action. LAN placement
reduces exposure but is not an authentication mechanism.

## Protected assets

- Codex ChatGPT authentication files and refreshable account state;
- repository contents and Git metadata under allowed workspace roots;
- prompts, model responses, tool output, diffs, and uploaded files;
- approval authority and pending server-request ids;
- Workbench password verifier, session keys, and session records;
- backend process control and app-server stdio.

## Trust boundaries and threats

The browser, request headers, filenames, MIME declarations, relative paths, WebSocket frames, and
all displayed repository content are untrusted. The frontend reverse proxy is an ingress component,
not an authorization service. Codex/app-server output is trusted for protocol shape only and must
still be escaped before rendering.

Primary threats are unauthenticated LAN access, session theft, login guessing, CSRF, cross-site
WebSocket hijacking, path traversal and symlink escape, malicious uploads, stored/rendered script
injection, forged or replayed approvals, accidental shell exposure, secret leakage, denial of
service, and reverse-proxy misconfiguration.

## Authentication design

First-start Setup now bootstraps exactly one account. The password is processed by argon2-cffi as
Argon2id with a random per-hash salt, 64 MiB memory cost, three iterations, four lanes, and a
32-byte hash. The encoded verifier is exclusively created in a mode-0600 file below the writable
Workbench state mount, outside the image and outside SQLite/environment values. Setup has no
default password, requires at least 12 characters, and closes permanently when that file exists.
Validation errors are replaced with a generic response so FastAPI never echoes password input.

Login responses use one generic credential error and Argon2 verification. Per-direct-peer and
process-global sliding-window limits plus bounded increasing failure delays constrain guessing.
Successful Setup/Login rotates any presented session. Password change/recovery is not implemented;
its future design must invalidate all sessions.

The browser receives a high-entropy opaque session cookie. SQLite stores only an HMAC-SHA-256
digest of the token with creation, idle expiry, absolute expiry, last-use, revocation, and keyed
client metadata digests. The HMAC key is a restrictive external file. The cookie is
HttpOnly, SameSite=Strict, Path=/, has no Domain attribute, and uses the __Host- prefix when Secure.
Secure is mandatory behind HTTPS. Configuration rejects HTTPS with an insecure cookie and rejects
HTTP unless both insecure-cookie and degraded-HTTP settings are explicit. Degraded mode emits a
startup warning and is not an internet-facing production setting.

No session, password, CSRF token, Codex token, or upload capability is stored in localStorage or
sessionStorage.

## CSRF and origin policy

All current state-changing HTTP methods except first Setup/Login require:

1. a valid server-side session cookie;
2. exact Origin match against the configured canonical public origin;
3. a high-entropy CSRF token bound to the session and supplied in a custom header;
4. a supported content type and Fetch Metadata checks where available.

An outer ASGI boundary counts actual body bytes independently of `Content-Length`; oversized
declared, chunked, or falsely declared JSON/multipart requests receive a safe 413 response.

The CSRF token is an HMAC derived from the opaque cookie token, is returned only after authenticated
status, and is held in page memory. SameSite is defense in depth, not the sole control. Setup/Login
check exact Origin, same-origin Fetch Metadata when present, and JSON content type.

CORS is disabled because browser access is same-origin. The current authentication boundary does
not use forwarded client-address headers as authority. Narrow proxy trust and tested extraction
must precede any future use of those headers.

In production, every non-health API request additionally requires one `Host`, one
`X-Forwarded-Host`, and one `X-Forwarded-Proto` matching the configured public authority and
scheme. The frontend overwrites those forwarded fields from its narrowly normalized ingress;
duplicates and mismatches fail closed. WebSocket handshakes apply the same proxy boundary before
the exact browser-Origin/session checks. Health remains directly diagnosable on container
loopback and does not grant application authority.

## WebSocket policy

`/ws/events` requires the valid session cookie and exact Origin before acceptance. Its first frame
is a bounded `subscribe` command containing the in-memory session CSRF token and optional last
sequence; tokens never enter the URL. The server sends a normalized snapshot, then safe replay and
incremental events. Heartbeats revalidate session state, frames and per-client queues are bounded,
and expired, malformed, sequence-gapped, or slow clients are closed.

Codex app-server WebSocket transport is not exposed. The backend uses private stdio. The browser
WebSocket carries only sanitized Workbench events.

## Path and repository controls

Only configured absolute roots are trusted. Browser paths must be relative, non-empty, and free of
NUL bytes. Resolution joins to the trusted root, canonicalizes existing symlinks, and proves the
result is a strict descendant. Operations revalidate immediately before use to reduce
time-of-check/time-of-use exposure.

Foundation repository discovery examines direct children only, rejects symlink entries, and
requires a non-symlink .git directory or regular file. The browser receives relative names and
opaque ids, not authority to supply arbitrary absolute paths. Future file and Git APIs reuse the
same resolver and apply
operation-specific allowlists.

Codex receives the selected repository as cwd and a sandbox policy whose writable roots do not
exceed that repository unless the user approves a clearly rendered expansion.

## Upload policy

The reverse proxy caps request bodies at 21 MiB; the application default permits at most 20 MiB per
file and applies per-session count and aggregate quotas. Uploads stream to an exclusively created
private file and never trust Content-Length alone.

Original names are Unicode-normalized and length-limited; separators/control characters are
rejected. Names are retained only as display metadata. Storage names are random. Allowed types are PNG,
JPEG, WebP, and GIF images plus an explicit later document allowlist. Both magic-byte detection and
safe decoding are required; declared MIME is advisory. Image width, height, decoded pixel count,
and frame count are bounded. Pillow's decompression-bomb protection remains active. SVG and HTML
are rejected.

Accepted files are mode 0600, non-executable, outside the static web root, tied to session and
expiry, and are not exposed by any download or static route. Their storage identity, mode,
containment, size and digest are rechecked before the backend creates a `localImage` input. Files
are never interpreted as configuration. Malware scanning can be inserted before `ready`.

## Approval integrity

An approval is a state machine: received, presented, deciding, responded, resolved; terminal
alternatives are expired, invalidated, or failed. The record binds a random browser-facing id to the
authenticated session, app-server process generation, exact server request id, method, thread id,
turn id, item id, normalized action summary, available decisions, and deadline.

The UI renders the sanitized command, repository label, requested filesystem-root labels, reason,
expiry, and exactly the available decisions. It never accepts a free-form response. The backend accepts one
atomic transition from presented to deciding, verifies that the requested decision was offered,
sends exactly one protocol response, and records outcome. Disconnect, turn completion/interruption,
`serverRequest/resolved`, process restart, session expiry/Logout, or timeout invalidates the record.
Replays return a conflict/unavailable error and cannot trigger execution.

Decline is the safe default. The UI exposes only decisions retained from the validated server
request; no Workbench automatic approval exists.

Sandbox and approval policies remain independent. Full Access uses the exact schema value
`danger-full-access`. The backend issues and atomically consumes a one-use, short-lived confirmation
bound to session, repository, new-thread scope or exact thread, mode, and approval policy. The UI
names the exact selection, requires a checkbox, and keeps a persistent thread badge; it is not a
global default. See docs/permissions-and-approvals.md.

## Shell and tool safety

There is no generic HTTP shell endpoint and no use of shell=True. The managed app-server child is
started with a fixed executable and argument vector. Future user shell commands must use a named
app-server method, show the exact action, require authentication/CSRF, and preserve Codex sandbox
and approval semantics. The documented thread/shellCommand method runs outside the sandbox and is
therefore excluded until a dedicated higher-risk design is approved.

Default sandbox configuration is workspace write limited to the selected repository with network
disabled, subject to product testing. Elevated permissions are never a hidden default.

## Secret handling and logging

The Codex-home mount is visible only to the backend container and app-server child. It is never
served, indexed, copied into images, returned by config APIs, or included in diagnostics. The
session HMAC key arrives as a file in `/run/secrets/workbench`; the encoded password verifier is in
the writable state security directory. Secrets are not committed, put in `.env`, SQLite, browser
storage, URLs, command arguments, or logs.

The Synology deployment bind-mounts the existing Codex home read/write into the backend only. The
SSH Codex CLI and Workbench app-server are independent processes with no cross-container lock.
Concurrent token refresh, configuration or thread/index writes are therefore not asserted safe;
operators must avoid overlapping Codex turns and quiesce both writers for backup/restore. A
separately authenticated home is required if true concurrent use is needed.

FastAPI, app-server and app-server-launched commands currently share one non-root container UID.
Consequently, `danger-full-access` code can read every backend mount readable by that UID, including
Codex authentication, the Workbench session key and password verifier. A read-only secret mount
prevents mutation but not disclosure. The browser cannot directly request these paths and logs are
redacted, but prompt/tool compromise remains a privilege-separation risk until secrets are moved
behind a separate process/credential boundary.

Structured logs include timestamps, safe event names, request ids, status, duration, protocol
method, and opaque thread/item ids. They exclude prompt text, raw tool output, uploaded bytes,
cookies, authorization/CSRF headers, password values/hashes, Codex auth, and complete environment
dumps.

The Codex compatibility probe separately captures stdout/stderr and logs only one allowlisted
reason code. It accepts exactly one `codex-cli 0.146.0` line after outer whitespace. Additional
stdout, any non-whitespace stderr, malformed UTF-8, timeout, execution/permission errors and a
different version all fail closed; their contents are never emitted.

## Browser and content security

The gateway sets a restrictive Content Security Policy, frame denial, nosniff, no-referrer, and a
minimal Permissions Policy. React text rendering is used; dangerouslySetInnerHTML is prohibited
without a reviewed sanitizer. ANSI output and Markdown need dedicated parsers with HTML disabled.
Downloads use server-supplied safe types and Content-Disposition.

## Reverse proxy and HTTPS

The Synology deployment publishes the frontend only as `127.0.0.1:8085:8080`; no Workbench service
has a LAN IP or a host port on `0.0.0.0`/the NAS LAN address. The Synology HTTPS reverse proxy is the
only production ingress and targets `http://127.0.0.1:8085`. The canonical external origin is
explicit and contains no credentials, path, query, or fragment. Proxy trust must be configured
narrowly. The edge preserves the external Host and browser Origin, sets Forwarded-Proto to `https`,
forwards HTTP/1.1 WebSocket Upgrade/Connection, avoids caching `/api` or `/ws`, and sets HSTS only
after validation.

## Current posture and residual risks

Health and the narrowly defined authentication status/Setup/Login routes are public. Every other
HTTP API route fails closed through the central session boundary; mutations require Origin and
CSRF. Repository, thread, turn, upload, Full Access, and approval routes are narrow typed
operations, not generic proxies. The managed app-server is reachable only over backend stdio.

The public first-start Setup window allows the first same-origin LAN visitor to claim an
unconfigured instance. Operators must initialize on a trusted network before broader exposure; a
future recovery/bootstrap-token design remains open. Per-address login limits currently see the
direct proxy peer until narrow trusted-forwarded-header support is implemented, while the global
limit still applies. WebSocket heartbeat authentication and every later HTTP request detect expiry;
expiry and Logout invalidate session-bound Full Access confirmations and pending approvals.

The frontend CSP permits only same-origin connections; the server independently enforces exact
WebSocket Origin. HTTP is permitted only as an explicit host-local test at
`http://127.0.0.1:8085`; regular operation requires the narrowly configured HTTPS reverse proxy.
DSM ACLs may broaden POSIX mode bits and require a host-side audit.
Uploads are purged on startup and before later uploads rather than by a periodic timer, so expired
bytes can remain at rest until one of those triggers. Container images are version-tag-pinned but
not digest-pinned, and Docker validation depends on the target host.
The DS918+ kernel cannot enforce Docker CPU-CFS quotas, so Compose does not claim CPU isolation;
memory, PID, nofile, queue, upload, tmpfs, read-only-root, capability and one-worker boundaries
remain active.
