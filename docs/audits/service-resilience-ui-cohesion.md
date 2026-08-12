# Service resilience and UI cohesion audit

Date: 2026-08-11
Baseline: `042c94b35011ca9bffe0e3e1f53e5917092d6090` (`main`, `v0.1.0`)
Scope: application-owned recovery, authenticated operator status, realtime and job resilience, storage checks, and the complete Workbench dialog surface.

This report deliberately contains no deployment hostnames, host paths, repository paths, session identifiers, credentials, prompts, command output, or other operator data.

## Executive assessment

The released architecture already has strong boundaries: one backend process owns exactly one Codex App Server child, generation fencing prevents stale responses from becoming authoritative, restart attempts are bounded, browser authentication and same-origin mutation protection are centralized, repository paths are resolved server-side, and container permissions are restrictive. The main gaps are observability and recovery control rather than a missing foundation.

The largest product inconsistency is that technical state is surfaced through several unrelated UI patterns. Runtime health is polled by one component, usage has a separate disclosure, Git jobs live in a repository-local details element, and repository dialogs use bespoke form and status layouts. Git status is shown as raw JSON. Sidebar layout and dismissed activity are not durable product preferences. Realtime reconnect is generally sound but lacks jitter and a single, visible recovery narrative.

No second supervisor, Docker control path, container self-restart API, or automatic SQLite repair should be introduced. A conservative extension of the existing supervisor plus authenticated operator status is sufficient.

## Baseline and release evidence

- `origin/main`, the local branch point, and `v0.1.0` resolve to the expected baseline.
- The six public image tags resolve. For each image, `0.1.0`, `0.1`, and `latest` point to one release index.
- Runnable platform: `linux/amd64`. The additional unknown-platform manifest is an OCI attestation, not another runnable platform.
- OCI labels report version `0.1.0`, the baseline revision, Apache-2.0, and the public source repository.
- Both images include an attestation manifest. The release workflow requests BuildKit SBOM and maximum provenance.
- The release workflow uses immutable action SHA pins. Its metadata configuration can emit `latest` through both semver flavoring and an explicit raw tag; future releases should make that behavior singular without touching the published release.
- CI already selects Node 24. Actions that declare an older JavaScript runtime should be upgraded only by immutable SHA, with their generated runtime warning verified in CI.
- The executable repository SSH wrapper remains mode `100755`; no baseline mode drift was found.

## Current service boundaries

| Boundary | Authority | Current behavior | Gap / decision |
| --- | --- | --- | --- |
| Backend HTTP | ASGI application and security middleware | Public minimal liveness/readiness; authenticated workbench APIs | Keep public contracts; add an authenticated safe operator DTO. |
| Codex App Server | `AppServerSupervisor` | Exact version check, one child, generation fencing, bounded exponential restart, stable-window budget reset | Add conservative idle watchdog, jitter, explicit single-flight manual restart, and safe recovery timestamps. |
| Realtime | Session-scoped broker and browser client | Bounded replay, monotonically increasing sequence, snapshot fallback, heartbeat, exponential reconnect | Add jitter, explicit recovery reason/state, manual reconnect, and one authoritative post-gap snapshot path. |
| SQLite / metadata | Database plus typed stores | WAL, transactional migrations, busy connection timeout | Set explicit `busy_timeout`, run startup `quick_check`, expose only safe status, never auto-repair. |
| Repository root | Discovery and secure path boundary | Server-resolved identities, symlink and containment checks | Add a non-sensitive availability/writability/capacity signal. |
| Upload storage | Upload service | Size/type limits, private lifecycle and TTL cleanup | Add status/capacity signal and retain fail-closed symlink behavior. |
| Background jobs | In-memory registry | Bounded history; queued/running/succeeded/failed; process groups terminated on timeout | Represent timeout/interruption safely, hide command output from default UI, and retry only fetch/pull. |
| Plugin discovery | Capability circuit breaker | Disabled by default, lazy and not readiness-critical | Surface disabled/available/degraded without degrading overall status to failed. |

## Existing invariants to preserve

- One backend and one backend-owned Codex App Server child.
- Exact Codex CLI/App Server version `0.146.0`.
- Generation-scoped correlation and projection updates.
- Single-flight, bounded recovery with a stable reset window.
- Capability discovery remains optional, cached, circuit-broken, and excluded from readiness.
- Full access remains explicit and confirmation-bound; no cosmetic or recovery operation elevates permission.
- Private deploy-key material never crosses the backend/browser boundary.
- Native thread deletion and safe clone reservation cleanup remain authoritative.
- Git is invoked without a shell and has bounded local/network/clone timeouts.
- Backend remains private behind the frontend gateway; no Docker socket, host-control API, or extra host port is added.
- Containers remain non-root, read-only, capability-free, and protected by `no-new-privileges`.
- Browser DTOs and logs remain free of raw protocol envelopes, secrets, prompts, private paths, and unbounded command output.

## Recovery inventory

### App Server

The supervisor verifies the executable and exact version before the first generation. Initialization failures and transport disconnects use the same bounded restart scheduler. Concurrent requests are coalesced through the existing restart task and request flag. Stable READY time clears the rolling budget. Permanent version failures become terminal without scheduling a restart.

Missing pieces are an explicit public restart method, shutdown rejection semantics, operator-safe recovery timing, jitter, and a liveness check for a child that remains connected but stops answering. The generated `0.146.0` schema confirms `thread/loaded/list` with only cursor and limit parameters. It is a side-effect-free core operation, performs no repository mutation or turn, and is therefore the watchdog candidate. It should run only after an idle period, be single-flight, time out independently, and require multiple consecutive failures before entering the existing restart path.

Backend fail-fast is intentionally not proposed. A permanent/transient classification is not sufficiently broad to guarantee that exiting cannot create a container crash loop. Operator status plus bounded child recovery is safer.

### Realtime and reconciliation

The broker bounds history and client queues and reports replay misses. The browser rejects duplicate or old sequence numbers and closes on a gap, causing a fresh connection whose initial snapshot is authoritative. Streaming deltas are already batched with animation frames. Online/offline, visibility, and abort handling exist.

Gaps: reconnect delay has no jitter; UI wording can show a stale positive runtime state independently of realtime; recovery controls are distributed; and reconnect testing is too small to prove storm resistance. The hardened path should maintain one reconnect timer/socket, expose recovering distinctly, replace state from a snapshot after any gap, and keep completed items authoritative.

### Jobs

Browser disconnect does not own job lifetime. Git subprocesses use direct argv and process groups. Clone reservations are cleaned in a `finally` path. Deploy keys survive failed clone attempts. History is bounded.

Gaps: cancellation during backend shutdown can leave the last public state as running; timeout collapses into generic failed; response objects expose captured output; and UI offers push retry. Introduce content-free error codes and coherent terminal states only across registry, API, events, and UI. Automatic retries remain prohibited. Manual retry is limited to fetch and fast-forward pull.

## Storage assessment

SQLite uses WAL and transactional migrations. The connection timeout is currently implicit. Startup does not run `quick_check`, and the operator cannot distinguish database failure from app-server failure. State, upload, security, and clone-reservation lifecycle code already uses containment and symlink defenses.

Hardening plan:

- issue explicit `PRAGMA busy_timeout` on every connection;
- run `PRAGMA quick_check` after migrations and fail closed with a stable code if it is not `ok`;
- never attempt repair, deletion, or replacement of a corrupt database;
- check directory availability, write capability, and conservative free-space threshold without returning a path;
- retain TTL cleanup and add observable counts only where they cannot identify user content;
- document backup/restore as the recovery path.

## Container and deployment assessment

Development and release Compose files have matching security controls: read-only roots, bounded tmpfs, non-root users, dropped capabilities, `no-new-privileges`, PID/memory/fd bounds, health checks, stop grace periods, and `unless-stopped`. The backend has no host port; the frontend binds loopback. There is no Docker socket or privileged service.

A Compose health check observes state but does not restart a merely unhealthy container. Application self-healing must remain limited to owned subsystems. Optional NAS task guidance may describe an external, disabled-by-default monitor with lock and cooldown, but the web application must not activate or control it.

## Browser state inventory

| State | Current persistence | Assessment |
| --- | --- | --- |
| Authentication | Secure server session cookie | Correct; never duplicate in web storage. |
| Thread/repository authority | Server snapshot plus realtime | Correct; local persistence would create stale authority. |
| Sidebar whole-collapse | Unversioned local storage | Replace with a versioned bounded preference object. |
| Repository/thread section collapse | Ephemeral / unavailable | Add versioned preferences. |
| Desktop sidebar split | Unavailable | Add clamped versioned preference; mobile uses accordion only. |
| Dismissed Git activity | Component-local set | Move to bounded, pruned notification preferences using opaque IDs and timestamps. |
| Notification filter/open | Ephemeral / unavailable | Persist only filter and optional open preference, with migration/reset. |
| Conversation scroll | Session storage | Appropriate for per-tab restoration; keep bounded. |
| Draft/model/access selections | Component state | Appropriate; do not persist sensitive prompts. |
| Service/realtime status | Polled/runtime state | Must remain live, never treated as durable authority. |

## UI surface inventory

### Dialogs and confirmations

- Setup and login pages: accessible form structure, but not shared with Workbench dialog primitives.
- Approval dialog: projected choices and guarded dismissal; needs shared header/body/footer and documented initial-focus behavior.
- Full-access dialog: explicit acknowledgement and exact scope; keep its strong confirmation boundary.
- Deploy-key dialog: correct public-only material and retry lifecycle; remove broad lint suppression and normalize layout.
- Thread-delete dialog: typed confirmation and clear file-retention language; add title actions beside it.
- Repository create, clone, status, branch, commit, publish, remove, and delete flows are concentrated in one large component with bespoke state and markup.

### Popovers, menus, drawers, and details

- Anchored repository/thread menus implement keyboard navigation and focus return.
- Usage is a native details disclosure in the top bar.
- Health chips are non-interactive and independently poll public endpoints.
- Git activity is a repository-local details element with raw command-centric details and non-persistent dismissal.
- No unified system-status surface or application notification center exists.

### Raw-data exposure

- Git status is rendered as formatted JSON by default.
- Job command output is available directly in the activity surface and is modeled as a browser field.
- Conversation command output is user-visible thread content and is a separate, intentional product surface; it must remain bounded/redacted but is not operator-job metadata.

## CSS and accessibility assessment

The stylesheet has multiple chronological layers, including duplicate root-token blocks and repeated workspace/sidebar/composer selectors at later cascade positions. This makes control sizing and responsive behavior depend on override order. Repository forms use global input rules that also affect radio/checkbox presentation, matching the oversized controls in the supplied screenshot.

`RepositoryTools.tsx` and `DeployKeyDialog.tsx` disable effect-state and autofocus lint rules at file scope. Dialogs independently call native `showModal()`/`close()` and do not consistently restore focus. The required correction is a small shared primitive layer with explicit control selectors, one focus lifecycle, visible focus, semantic labels/descriptions, and no blanket lint suppression.

The dark visual language and compact top-right chips are the positive baseline. Consolidated tokens must give green only to healthy/success, amber to warning/recovery, red to failure/destruction, and blue to focus/selection. Reduced motion, forced colors, 200% zoom, keyboard splitters, mobile sheets, and minimum touch targets need explicit coverage.

## Git workflow assessment

The backend status DTO is already structured (branch, upstream, ahead/behind, counts, changes), but the frontend bypasses that meaning and prints JSON. The redesigned dialog can therefore be implemented without adding a raw command interface. Branch creation/switching is server-validated and shell-free; the frontend lacks a searchable local/remote branch inventory and uses an ambiguous action label. Commit selection, conflict blocking, publish consequences, remove-vs-delete, and deploy-key states should share one form vocabulary.

## Thread title assessment

The generated Codex `0.146.0` contract proves native `thread/name/set`, `thread/name/updated`, `thread/read`, and list/resume flows. The projection consumes native name updates, but metadata does not persist source, generation state, or manual precedence. Reconciliation can therefore reapply an older native name.

The implementation must first preserve native names and add an explicit manual set operation. Manual titles win over all later automatic updates. A Workbench-owned AI title operation is acceptable only if it is isolated, bounded, read-only, never uses full access or tools, never appears in the normal thread list, and cleans up its temporary native thread. If those constraints cannot be proven for a host capability set, title generation must remain unavailable with a safe error and the native/fallback title retained; a prompt substring must never be presented as AI-generated.

## Baseline verification

- Ruff: passed.
- MyPy strict with Linux target: passed for all backend source files.
- Frontend lint and typecheck: passed.
- Frontend unit suite: 57 passed; one pre-existing hydration test exceeded its five-second timeout on the Windows baseline run. It is a timing failure, not an assertion failure, and must be eliminated or explicitly classified before review.
- Windows backend suite: the default temporary directory was not usable and POSIX executable fixtures cannot run natively. A repository-local temporary base is used for the repeat run; remaining Windows-only failures are classified individually. Linux CI remains authoritative.
- No baseline file-level changes or mode changes were introduced by dependency installation; dependency directories are ignored.

## Implementation decisions

1. Extend the existing supervisor; do not add a peer supervisor.
2. Use `thread/loaded/list` as the idle liveness probe, with a bounded limit and no repository or provider parameters.
3. Keep liveness minimal and readiness strict; put diagnostic detail only in an authenticated typed endpoint.
4. Keep backend fail-fast disabled because safe crash-loop exclusion is not proven.
5. Make manual runtime recovery confirmed, rate-limited, origin/CSRF protected by the existing mutation boundary, single-flight, and conflict on active turns.
6. Make one system-status drawer the owner of component health and recovery actions; usage remains visible in the top bar and is included in that coherent surface.
7. Replace Git activity with a bounded notification center and persist only opaque display preferences.
8. Add a desktop keyboard splitter and independent project/thread collapse; use a non-draggable mobile accordion.
9. Replace raw Git status with a semantic view and retain JSON only as collapsed technical detail.
10. Introduce shared dialog/form primitives and remove broad file-level lint exceptions.
11. Keep job output out of default operator UI and disallow push/commit retry.
12. Preserve native naming authority while making title source and manual precedence explicit.
13. Add deterministic recovery/reconnect stress coverage and browser QA with synthetic data only.

## Root causes

1. Health, usage, realtime, and jobs evolved as separate surfaces without one typed component-health model.
2. The supervisor detects process/transport failure but not an idle, connected child that no longer answers.
3. Recovery timing and state are internal and cannot be safely inspected or manually triggered by an authenticated operator.
4. Realtime backoff lacks jitter and the UI does not unify backend, runtime, and socket recovery semantics.
5. Job terminal semantics and browser DTOs are too command/output-centric.
6. SQLite durability settings are partially implicit and corruption is not proactively detected.
7. UI preference persistence is piecemeal, unversioned, or absent.
8. Repository tooling grew into a monolithic component with independent dialog patterns and broad lint suppressions.
9. Structured Git data is available but rendered as raw JSON.
10. Generic form CSS leaks into native choice controls.
11. Thread names have native events but no persisted source/precedence lifecycle.
12. Responsive and accessibility verification is unit-oriented and lacks a deterministic browser matrix.

## Verification gate for completion

The change is reviewable only after backend lint/type/tests/stress, frontend lint/type/unit/build/accessibility/browser tests, dependency and secret audits, generated-schema and deployment-policy checks, Compose validation/builds where locally possible, `git diff --check`, mode verification, clean working tree, and green Linux CI. The release tag and all existing public image tags remain untouched.
