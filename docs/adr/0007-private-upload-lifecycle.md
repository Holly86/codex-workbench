# ADR 0007: Private owned image-upload lifecycle

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture

## Context

Codex accepts local image paths, while browsers supply untrusted bytes and cannot safely select a
server path. Uploads also outlive an individual HTTP connection long enough to join a turn.

## Decision

Workbench streams authenticated multipart image data to random exclusive mode-0600 files below its
private data root. It records session ownership and lifecycle metadata in SQLite, computes SHA-256,
and uses Pillow to verify and fully decode an allowlisted single-frame raster format under byte,
dimension, pixel, count and aggregate quotas.

The browser receives only an opaque upload id. Immediately before `turn/start`, the backend checks
owner, state, expiry, storage identity, containment, symlink/regular-file mode, size and digest, then
creates the absolute `localImage` path strictly inside the backend protocol adapter. Removal,
cancellation and expiry delete safe storage entries. There is no static/download route.

## Consequences

Browser filenames, MIME types and paths carry no authority. Remote URLs and documents require
separate designs. Private storage and SQLite must be backed up/expired together.
