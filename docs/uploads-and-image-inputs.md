# Uploads and image inputs

## Protocol boundary

Codex 0.146.0 `UserInput` supports `{ type: "localImage", path, detail? }` and remote
`{ type: "image", url, detail? }`; `ImageDetail` is exactly `auto`, `low`, `high`, or `original`.
Codex has no browser upload/ownership/progress API, so Workbench owns that lifecycle. Browser DTOs
contain only an opaque upload id and detail, never a server path or URL used as filesystem authority.

## Implemented browser flow

The composer accepts multiple screenshots through Ctrl+V/Command+V, drag-and-drop, and a labelled
multi-file picker. Local object URLs provide previews. Every chip shows safe display name, size,
real XHR transferred-byte percentage, validation/error state, and a labelled removal control.
Removing an active upload aborts it; removing a ready draft requests server deletion. Object URLs
are revoked after removal, successful send, or unmount. All controls remain keyboard accessible.

The composer sends only after all retained uploads are ready. Upload failure does not masquerade as
a completed attachment, and no simulated progress is shown.

## Authenticated HTTP lifecycle

1. `POST /api/uploads` accepts multipart data only after session, exact Origin, Fetch Metadata and
   session-bound CSRF checks. Nginx caps the request at 21 MiB; the backend independently streams
   and caps bytes without trusting `Content-Length`.
2. The backend normalizes a display name, rejects separators/control characters, generates an
   opaque id and independent random 48-hex storage name, then exclusively creates a mode-0600 file
   below the configured private upload root with `O_NOFOLLOW` where available.
3. It computes SHA-256 while streaming and enforces per-file, active-session-count and aggregate
   session-byte limits. Cancellation deletes the partial and records `upload_cancelled`.
4. Pillow magic/decode validation accepts PNG, JPEG, WebP and single-frame GIF only. SVG, HTML,
   unknown, animated and multipart images are rejected. Width, height, pixel count, frame count and
   Pillow decompression-bomb limits apply; full pixel decode is forced after structural verify.
5. Metadata moves through `staging`, `validating`, `ready`, `consumed`, `rejected`, or `expired`.
   SQLite stores owner session, safe display name, random storage name, detected MIME, byte count,
   SHA-256, timestamps and error code—never image bytes.
6. Turn start checks the configured per-turn count, owner session, ready/expiry state and detail.
   Immediately before constructing `localImage`, it re-resolves containment and rechecks storage
   identity, non-symlink regular-file mode, permissions, size and SHA-256 through a no-follow file
   descriptor. Only the backend supplies the resulting absolute path to its child.

Upload responses contain no storage name or local path. There is no static/download route for
accepted files. Cross-session, expired, consumed, missing or tampered ids return a generic
unavailable result. Explicit removal plus startup and pre-upload expiry cleanup delete safe storage
entries; consumed files remain unavailable for reuse and are deleted after their configured TTL.

## Limits and lifecycle configuration

Defaults are 20 MiB per file, eight images per turn, 64 active uploads and 100 MiB per session,
24-hour TTL, 12,000 pixels per dimension, 40 million decoded pixels, and one frame. The upload root
must be a restrictive non-symlink directory strictly below the Workbench data root. Configuration
fails closed when these relationships or quotas are invalid.

## Deferred concerns

Remote image URLs need a separate SSRF/redirect/DNS/content policy and are disabled. Documents need
an explicit format/decoder allowlist and model-context semantics and are not accepted by this
phase. Malware scanning remains an insertion point before `ready`; its absence does not relax
magic-byte, decode, ownership, containment, digest, or quota validation.
