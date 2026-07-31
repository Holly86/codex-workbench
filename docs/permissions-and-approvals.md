# Permissions and approvals

## Two independent controls

Codex 0.146.0 models sandboxing and approval policy independently. Workbench preserves that
separation in storage, API DTOs, UI controls, audit events, and app-server adapters.

`SandboxMode` has exactly three schema and CLI values:

| Value | Meaning in Workbench | Product treatment |
| --- | --- | --- |
| `read-only` | Codex tools must not write the workspace through the sandbox | Safest mode and always available. Network remains an explicit policy concern. |
| `workspace-write` | Writes are constrained to backend-selected workspace roots | Normal write-capable mode. Roots are derived from the authorized repository, never browser paths. |
| `danger-full-access` | The Codex sandbox is removed | High-risk mode with server-issued confirmation and a permanent thread warning. |

The string approval policies supported by both schema and CLI are `untrusted`, `on-request`, and
`never`. The schema additionally contains a granular object with `sandbox_approval`, `rules`,
`skill_approval`, `request_permissions`, and `mcp_elicitations` booleans. That object is not in the
top-level CLI choice list and must remain unavailable until a 0.146.0 runtime probe proves its
semantics. Workbench never invents an `always`, `auto`, or `on-failure` value.

`turn/start` documents `approvalPolicy` and `sandboxPolicy` as overrides for the current **and
subsequent** turns. The UI must therefore say “from this turn onward”; it must not present a false
one-turn-only promise. A later one-turn UX would need an explicit, reconciled restore operation.

## Full Access UX

Selecting Full Access is a two-stage operation:

1. The normal selector shows Read-only, Workspace Write, and Full Access. Full Access is never a
   hidden default or activated by keyboard movement alone.
2. Selecting it opens a focused warning dialog that names the repository, explains that filesystem
   and command sandboxing is removed, requires a confirmation checkbox, and exposes a final button
   labelled “Full Access for this thread”. Escape and the default button cancel.

After activation, the thread header and composer show a persistent high-contrast “Full Access”
badge. The badge is included in reconnect snapshots and cannot be hidden while the effective mode
is active. A new Full Access thread always requires a new scope-bound confirmation even when the
selector still displays the prior choice; no grant is written to global preferences.

## Server-side confirmation capability

Frontend state is never authority. The implemented backend flow is:

1. An authenticated, Origin- and CSRF-checked request asks to prepare a permission selection.
2. For Full Access only, the backend issues a high-entropy, short-lived confirmation id bound to
   session id (and therefore the single configured user), repository id, exact thread or random
   pending-thread scope, sandbox mode, approval policy, and expiry.
3. The confirmation request repeats the exact choice and consumes that id atomically. Missing,
   expired, reused, cross-session, cross-thread, or altered confirmations fail closed.
4. The backend resolves the repository again, builds the protocol policy itself, records a coarse
   security event, and sends it only through the typed app-server client.

The confirmation id is not globally persistent and does not permit arbitrary sandbox policy JSON.
For Workspace Write, writable roots are canonical server paths under the selected repository and
network access defaults to disabled unless a separately reviewed capability changes it. For Full
Access, no browser-supplied root or config map is accepted.

## Approval request integrity

Command and file requests are directly represented in 0.146.0. Command decisions are `accept`,
`acceptForSession`, `decline`, `cancel`, `acceptWithExecpolicyAmendment`, and
`applyNetworkPolicyAmendment` with their typed payloads. File decisions are `accept`,
`acceptForSession`, `decline`, and `cancel`.

Workbench maps a native server request id to an independent random browser-facing id. The record is
bound to session, process generation, method, thread, turn, item, available decisions, and expiry.
Only one atomic presented-to-deciding transition may respond. Decision labels distinguish
one-time, session, policy-amendment, decline, and cancel semantics. Decline is the safe default.
Turn completion, `serverRequest/resolved`, session expiry/Logout, connection loss, process restart,
and timeout invalidate outstanding authority.

Permission requests use a dedicated permission projection and permit only accept or decline; accept
returns at most the exact server-retained network/filesystem request for turn scope. Experimental
user-input, MCP elicitation, dynamic-tool and unknown variants are not treated as approvals and
receive a typed safe refusal/cancel/error response when defined; they are never implicitly accepted.

## Defaults and open verification

The new-thread default is Workspace Write limited to the selected repository, with network disabled
in the constructed turn sandbox policy, and `on-request`. The backend accepts only the three exact
sandbox strings and three exact approval strings; invalid shapes fail validation. Full Access is
never a fallback. The explicit `danger-full-access` plus `never` combination is supported after
confirmation and correctly produces no per-action Workbench approvals unless Codex nevertheless
sends a server request.
