# ADR 0003: Server-side single-user session boundary

- Status: Accepted
- Date: 2026-07-31
- Deciders: Codex Workbench architecture

## Context

The first deployment is single-user behind a Synology HTTPS reverse proxy, but any browser allowed
to reach that proxy may be untrusted. Agent access can read and change repositories, invoke tools,
and consume a ChatGPT-authenticated Codex account. Relying on a secret URL, source address, reverse
proxy alone, or “single-user” label would expose high-impact authority.

The product must later operate behind HTTPS without moving secrets into browser storage or
rewriting its session model.

## Decision

Implement one bootstrapped account with an Argon2id password verifier stored in a restrictive
secret file outside the image. Issue opaque high-entropy cookies and store only keyed token digests
and session metadata server-side. Cookies are HttpOnly, SameSite=Strict, host-only, and Secure for
HTTPS.

Every privileged HTTP mutation additionally requires exact Origin, session-bound CSRF header token,
supported content type, and authorization. CORS remains disabled. WebSocket upgrades require the
session cookie and exact Origin before acceptance, then reauthorize every subscription/command.

Health probes remain the only unauthenticated backend routes. Codex authentication and app-server
startup remain backend-only. Until the authentication/CSRF boundary is implemented and tested, the
foundation exposes no repository, upload, thread, tool, approval, or shell API and does not start
app-server.

No password, password verifier, session secret, Codex credential, or CSRF token is stored in
SQLite, environment source files, URLs, logs, or browser storage. SQLite may store only session
token digests and non-secret metadata.

## Consequences

Benefits:

- a LAN browser has no implicit agent authority;
- stolen database contents do not contain reusable session tokens or password material;
- same-origin CSRF and WebSocket hijacking have explicit controls;
- TLS termination and proxy changes do not replace the application auth model;
- app-server access can be safely centralized behind one authorization layer.

Costs:

- initialization, recovery, password change, rate limits, expiry, and revocation must be built;
- the explicit host-local HTTP test mode cannot use Secure cookies and is not a production mode;
- exact public-origin and proxy-trust configuration become operational requirements;
- WebSocket session expiry requires connection management.

## Alternatives considered

**No auth on LAN:** rejected because network location is neither user identity nor CSRF protection.

**HTTP Basic authentication:** simple but weak for logout/revocation, CSRF-aware browser flows,
rate limiting, and future WebSocket authorization.

**Password or bearer token in localStorage:** rejected due to script-accessible long-lived secrets.

**Only reverse-proxy authentication:** may be an optional trusted-header integration later, but it
couples the product to one proxy and still needs a carefully authenticated internal boundary.

**Multi-user identity provider:** outside initial scope and unnecessarily expands account,
authorization, and secret-management complexity.

## Verification

Required tests cover password verification and timing behavior, login throttling, token rotation,
digest-only storage, idle/absolute expiry, revocation, cookie attributes, CSRF on every unsafe
method, exact Origin and forwarded-header trust, WebSocket cross-site attempts, and the invariant
that no app-server process starts before authorization is available.
