# Uploads and image inputs

## Protocol boundary

Codex 0.146.0 `UserInput` directly supports `{ type: "localImage", path, detail? }` and
`{ type: "image", url, detail? }`. `ImageDetail` is exactly `auto`, `low`, `high`, or `original`.
The app-server provides no browser upload, ownership, MIME validation, storage, progress, or expiry
API. Those are Workbench capabilities.

The stable browser descriptor contains an opaque upload id and optional image detail, never a local
path or URL chosen as filesystem authority. Only the backend resolves an accepted upload to an
absolute path visible to its managed app-server child.

## Composer UX

The future composer accepts one or more screenshots through Ctrl+V/Command+V, drag-and-drop, and a
labelled file picker. Each file gets a local object-URL preview, safe display name, exact bytes-sent
progress, validation/error state, and a remove button. Removing before send invalidates the draft
reference and cancels an active upload where possible. Object URLs are revoked after removal or
unmount.

Keyboard users can reach the picker, each preview, retry, and remove controls. Dragging is not the
only path. Progress is based on transferred bytes; no simulated percentage is shown. A preview is
not evidence that the server accepted the file.

## Authenticated HTTP upload flow

1. The browser sends multipart bytes to a named authenticated route with exact Origin and CSRF.
2. The gateway caps the request and the backend streams to a private bounded temporary file. It
   does not trust `Content-Length` or buffer the complete upload in memory.
3. The backend normalizes the display name, generates storage identity, computes SHA-256, detects
   magic bytes, compares the declared type as advisory data, and safely decodes the image.
4. Width, height, decoded pixels, frames, decompression ratio, per-file size, per-session count, and
   aggregate quotas are enforced. Initial accepted formats are PNG, JPEG, WebP, and GIF. SVG, HTML,
   polyglots, and unreviewed documents are rejected.
5. An accepted mode-0600 file is atomically moved outside the static web root. SQLite stores only
   owner session, display metadata, detected MIME, size, digest, state, and expiry.
6. Turn submission contains opaque upload ids. The backend rechecks session ownership, ready state,
   expiry, repository/thread binding, file metadata, symlink safety, and containment immediately
   before producing `localImage` inputs.

Browser MIME, filename extension, and preview decoder are never authorization inputs. Download or
preview-from-server routes remain authenticated, use `nosniff`, safe `Content-Disposition`, and a
server-selected MIME type.

## Lifecycle and cleanup

The internal `UploadDescriptor` states are `staging`, `validating`, `ready`, `consumed`, `rejected`,
`expired`, and `failed`. `bytes_received` is observable progress. Accepted temporary uploads have a
bounded lifetime; rejected partials, abandoned drafts, expired uploads, and metadata/file
inconsistencies are purged by a reconciler. Cross-session ids return a generic unavailable result.

Consumption semantics and retention after a successful turn remain an owner decision. A cleanup
failure is visible operationally and does not make an expired id usable.

## Deferred concerns

Remote image URLs need a separate SSRF, redirect, DNS rebinding, size, and content policy and are
not enabled by local screenshot support. Documents require an explicit allowlist and model-context
semantics. Malware scanning remains an insertion point before `ready`; its absence does not relax
decode and type validation.

No upload endpoint is implemented in the authentication/capability phase.
