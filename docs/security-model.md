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

The next implementation phase bootstraps exactly one account. The password is processed with
Argon2id using a maintained password-hashing library and a per-hash salt. The encoded verifier is
stored in a mode-0600 secret file outside the image, not in SQLite or environment variables.
Initialization accepts password input interactively or from a one-shot secret file and never logs
it. There is no default password.

Login responses use uniform errors and timing-safe verification. Per-address and global
token-bucket limits plus increasing delays constrain guessing. Successful login rotates the session
identifier; password change invalidates all sessions.

The browser receives a high-entropy opaque session cookie. SQLite stores only a keyed digest of the
token with creation, idle expiry, absolute expiry, last-use, and revocation state. The cookie is
HttpOnly, SameSite=Strict, Path=/, has no Domain attribute, and uses the __Host- prefix when Secure.
Secure is mandatory behind HTTPS. An explicitly configured LAN-only HTTP development mode may omit
Secure and must emit a prominent startup warning; it is not a production setting.

No session, password, CSRF token, Codex token, or upload capability is stored in localStorage or
sessionStorage.

## CSRF and origin policy

All state-changing HTTP methods require:

1. a valid server-side session cookie;
2. exact Origin match against the configured canonical public origin;
3. a high-entropy CSRF token bound to the session and supplied in a custom header;
4. a supported content type and Fetch Metadata checks where available.

The CSRF token is obtained after login and held only in page memory. SameSite is defense in depth,
not the sole control. Login itself checks Origin and rejects cross-site form content types.

CORS is disabled because browser access is same-origin. Reverse-proxy headers are trusted only from
the known internal proxy hop, never directly from arbitrary clients.

## WebSocket policy

The WebSocket upgrade requires the valid session cookie and an exact Origin match before
acceptance. Missing, null, multiple, malformed, or unexpected origins are rejected. Authorization
is rechecked for subscriptions and every inbound command. Session expiry or revocation closes the
connection. Frame size, message rate, subscription count, and outbound queue are bounded.

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
file and applies per-session count and aggregate quotas. Uploads stream to a private temporary
directory and never trust Content-Length alone.

Original names are Unicode-normalized, length-limited, stripped of separators/control characters,
and retained only as display metadata. Storage names are random. Allowed types begin with PNG,
JPEG, WebP, and GIF images plus an explicit later document allowlist. Both magic-byte detection and
safe decoding are required; declared MIME is advisory. Image width, height, decoded pixel count,
frame count, and decompression ratio are bounded. SVG and HTML are rejected initially.

Accepted files are mode 0600, non-executable, outside the static web root, tied to session and
expiry, and served only through an authenticated download route with nosniff and attachment
headers. Files are never interpreted as configuration. Malware scanning can be inserted before the
accepted state.

## Approval integrity

An approval is a state machine: received, presented, deciding, responded, resolved; terminal
alternatives are expired, invalidated, or failed. The record binds a random browser-facing id to the
authenticated session, app-server process generation, exact server request id, method, thread id,
turn id, item id, normalized action summary, available decisions, and deadline.

The UI renders command, cwd, network destination, requested filesystem roots, reason, and whether a
decision is session-scoped. It never silently upgrades a one-time grant. The backend accepts one
atomic transition from presented to deciding, verifies that the requested decision was offered,
sends exactly one protocol response, and records outcome. Disconnect, turn completion/interruption,
serverRequest/resolved, process restart, or timeout invalidates the record. Replays return the
recorded terminal status and cannot trigger execution.

Decline is the safe default. “Accept for session” and policy amendments require separate explicit
controls. No automatic approval exists in the initial product.

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
served, indexed, copied into images, returned by config APIs, or included in diagnostics. Workbench
secrets arrive as files in /run/secrets/workbench. Secrets are not committed, put in .env, SQLite,
browser storage, URLs, command arguments, or logs.

Structured logs include timestamps, safe event names, request ids, status, duration, protocol
method, and opaque thread/item ids. They exclude prompt text, raw tool output, uploaded bytes,
cookies, authorization/CSRF headers, password values/hashes, Codex auth, and complete environment
dumps.

## Browser and content security

The gateway sets a restrictive Content Security Policy, frame denial, nosniff, no-referrer, and a
minimal Permissions Policy. React text rendering is used; dangerouslySetInnerHTML is prohibited
without a reviewed sanitizer. ANSI output and Markdown need dedicated parsers with HTML disabled.
Downloads use server-supplied safe types and Content-Disposition.

## Reverse proxy and HTTPS

The application is prepared for TLS termination in front of the frontend service. The canonical
external origin is explicit and contains no credentials, path, query, or fragment. Proxy IP ranges
and forwarded-header trust must be configured
narrowly. HTTPS must set HSTS at the edge after validation, preserve WebSocket Origin and Upgrade
headers, and avoid caching /api or /ws traffic.

## Foundation posture and residual risks

Today only static UI assets and minimal health probes are public. App-server startup is disabled,
there are no repository/chat/upload/approval endpoints, and no authentication endpoint exists.
This is fail-closed for agent access. The Compose shell should not be treated as a functional
production release until authentication, CSRF, WebSocket Origin enforcement, session revocation,
and concrete transport tests land.

The frontend CSP currently permits ws and wss for the future same-origin stream; the application
must still enforce exact server-side Origin. Container images are tag-pinned but not digest-pinned,
and Docker validation was unavailable on the inspected host.
