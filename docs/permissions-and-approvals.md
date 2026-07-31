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

1. The normal selector shows Read-only, Workspace Write, and Full Access as separate cards. Full
   Access is never preselected, inherited as a global default, or activated by keyboard movement
   alone.
2. Selecting it opens a focused warning dialog that names the repository, explains that filesystem
   and command sandboxing is removed, requires a confirmation checkbox, and exposes a final button
   labelled “Full Access for this thread”. Escape and the default button cancel.

After activation, the thread header and composer show a persistent high-contrast “Full Access”
badge. The badge is included in reconnect snapshots and cannot be hidden while the effective mode
is active. Starting another thread returns to the configured non-full-access default; Full Access
is not written to global preferences.

## Server-side confirmation capability

Frontend state is never authority. The future backend flow is:

1. An authenticated, Origin- and CSRF-checked request asks to prepare a permission selection.
2. For Full Access only, the backend issues a high-entropy, short-lived confirmation id bound to
   session id, repository id, thread id or pending thread start, requested sandbox mode, approval
   policy, and expiry.
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
Only one atomic presented-to-deciding transition may respond. Session grants and policy amendments
are visually separated from one-time acceptance and require an additional confirmation. Decline is
the safe default. Turn completion, interrupt, `serverRequest/resolved`, connection loss, process
restart, and timeout invalidate outstanding authority.

Permission requests and experimental user-input/MCP elicitation variants are never treated as
ordinary command approvals. Unsupported variants receive a typed safe refusal/cancel response when
the protocol defines one; they are never implicitly accepted.

## Defaults and open verification

Until the managed app-server phase ships, no permission selection reaches Codex. The proposed new
thread default is Workspace Write with network disabled and `on-request`, subject to the documented
runtime probe and product testing. If the adapter cannot construct and verify that exact effective
policy, it falls back to Read-only or refuses the turn. Full Access is never a fallback.
