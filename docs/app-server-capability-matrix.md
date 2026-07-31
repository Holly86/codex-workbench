# Codex 0.146.0 app-server capability matrix

## Authority and classification

This matrix was reviewed against the locally installed `codex-cli 0.146.0`, the output of
`codex --help`, `codex app-server --help`, `codex features list`, and the checked-in schemas under
`schemas/codex-app-server`. The schemas were generated without `--experimental`. Presence of a
type does not by itself mean Workbench enables or exposes the capability.

Classifications used below are:

- **schema / directly usable**: present in the non-experimental request, response, or notification
  union and sufficiently specified for a future backend adapter;
- **CLI configuration**: an exact supported value is also exposed by the local CLI;
- **derivable**: no native product value exists, but Workbench can compute or aggregate it from
  named official values;
- **experimental**: the generated declaration explicitly says experimental or runtime enablement
  is feature-gated;
- **not available**: no corresponding value or method exists in the reviewed protocol union;
- **open verification**: the shape exists, but a 0.146.0 runtime probe is required before product
  use.

## Matrix

| Product function | Exact 0.146.0 evidence | Schema | CLI | Classification | Workbench decision |
| --- | --- | --- | --- | --- | --- |
| Codex account status | `account/read` → `GetAccountResponse`; `account/updated`; legacy `getAuthStatus` can return an auth token | yes | `codex login`, `codex doctor` | directly usable | Backend only. Never request or project `authToken`; this is separate from Workbench login. |
| Thread discovery/read | `thread/list`, `thread/loaded/list`, `thread/read`; `ThreadStatus` | yes | resume/archive/delete commands exist | directly usable | Next phase may use list/read. Codex remains authoritative. |
| Thread start/resume | `thread/start`, `thread/resume`, `thread/fork`; start/resume params contain `sandbox` and `approvalPolicy` | yes | `--sandbox`, `--ask-for-approval` | directly usable | Start/resume only through a typed backend operation; no raw proxy. |
| Turn start | `turn/start`; ordered `UserInput[]`; optional `approvalPolicy` and `sandboxPolicy` overrides | yes | n/a as an app-server call | directly usable | Next phase. Schema comments say overrides apply to this and subsequent turns. |
| Sandbox modes | `SandboxMode = read-only \| workspace-write \| danger-full-access`; `SandboxPolicy` discriminated union | yes | the same three values via `--sandbox` | directly usable and CLI configuration | Exact values only. Full access requires a separate Workbench confirmation capability. |
| Approval policy | `AskForApproval = untrusted \| on-request \| granular object \| never` | yes | `untrusted`, `on-request`, `never` via `--ask-for-approval` | strings directly usable; granular open verification | The three strings may be offered. Granular form remains hidden until a runtime probe and UX review. |
| Approval reviewer | `ApprovalsReviewer = user \| auto_review \| guardian_subagent` | yes | not exposed by top-level help | open verification | Initial Workbench routes approvals to `user`; no automatic reviewer UI yet. |
| Command approval | server request `item/commandExecution/requestApproval`; typed response decisions | yes | approval behavior configured by CLI policy | directly usable | Next phase registry; decline/cancel are safe defaults. Policy-amendment decisions need stronger UI. |
| File-change approval | server request `item/fileChange/requestApproval`; exact four decision strings | yes | governed by approval policy | directly usable | Next phase registry; unstable `grantRoot` is displayed but not trusted as authority. |
| Permission escalation | `item/permissions/requestApproval`, `permissionProfile/list`, turn/session grant scope | yes | feature list marks `request_permissions_tool` under development and disabled | experimental/open verification | Must be refused safely until an opt-in probe and dedicated projection exist. |
| User input request | `item/tool/requestUserInput` declaration says `EXPERIMENTAL` | yes | `default_mode_request_user_input` is under development | experimental | Not enabled initially; unsupported server requests receive a safe response where defined. |
| Assistant text streaming | `item/agentMessage/delta`, final `agentMessage` item | yes | n/a | directly usable | Normalize to `assistant_text_delta`; final item is authoritative. |
| Reasoning summaries | `item/reasoning/summaryPartAdded`, `summaryTextDelta`, final reasoning item | yes | reasoning summary configuration exists | directly usable when delivered | Show only officially delivered summaries. Never synthesize hidden reasoning. |
| Raw reasoning text | `item/reasoning/textDelta`, final reasoning `content` | yes | delivery is model/config dependent | directly usable when delivered | Treat as optional; do not claim availability. |
| Structured plan | `turn/plan/updated` with steps and statuses | yes | n/a | directly usable | Normalize full plan replacement. |
| Plan text delta | `item/plan/delta` explicitly says experimental and non-authoritative | yes | no | experimental | Do not depend on it; final plan item or `turn/plan/updated` wins. |
| Item lifecycle | `item/started`, `item/completed`, `ThreadItem` union | yes | n/a | directly usable | Started content is provisional; completed item replaces it. Unknown variants become compatibility events without raw forwarding. |
| Command activity/output | `commandExecution` item and `item/commandExecution/outputDelta` | yes | n/a | directly usable | Normalize command summary/status/output. Output is bounded and sanitized before browser delivery. |
| Standalone command | `command/exec` plus write/resize/terminate and output delta | yes | CLI shell tool exists | directly usable protocol, excluded product capability | No generic HTTP shell API. Requires a separate reviewed product capability. |
| Unsandboxed thread shell | `thread/shellCommand` explicitly runs unsandboxed with full access | yes | shell syntax is supported | directly usable protocol, prohibited initially | Excluded from the next phase. |
| File-change activity | `fileChange` item, `item/fileChange/patchUpdated`, `turn/diff/updated` | yes | n/a | directly usable | Normalize metadata/diff activity; deprecated file output delta is not relied upon. |
| Reading activity | parsed `CommandAction` variants `read` and `listFiles` | yes | n/a | derivable | Workbench activity aggregation; not a native agent state. |
| Search activity | `CommandAction.search`, `webSearch` item/action | yes | `--search` enables native web search | derivable | Workbench activity aggregation. |
| Testing/build activity | no test/build event or item type | no | no dedicated flag | derivable/open verification | Only classify a command using reviewed command/action metadata; otherwise display `executing`. |
| Turn completion/cancel | `turn/completed`; `TurnStatus = completed \| interrupted \| failed \| inProgress` | yes | interactive interrupt exists | directly usable | Map `interrupted` to Workbench `cancelled`. An interrupt response alone is not terminal. |
| Explicit interruption | `turn/interrupt` requires thread and turn ids | yes | n/a | directly usable | Next phase; UI remains interrupting until authoritative completion. |
| Thread token usage | `thread/tokenUsage/updated`; total and last `TokenUsageBreakdown` | yes | no dedicated top-level command | directly usable | Keep latest model usage and thread total separate. No quota inference. |
| Context window | `modelContextWindow` in `ThreadTokenUsage` | yes | config can contain `model_context_window` | limit directly usable; fill derivable | A percentage may be derived only from named token fields and labelled as derived. |
| Compaction | `contextCompaction` item; deprecated `thread/compacted`; `thread/compact/start` | yes | compact behavior is configurable | directly usable | Item lifecycle is authoritative; no invented compaction percentage. |
| Account rate limits | `account/rateLimits/read`; sparse `account/rateLimits/updated` | yes | no top-level display command | directly usable | Official values have precedence; sparse updates are merged or trigger refetch. Authenticated clients only. |
| Primary/secondary windows | `RateLimitSnapshot.primary/secondary`; `usedPercent`, duration, reset | yes | no | directly usable | Display exact used percent/window/reset; unavailable fields remain unavailable. |
| Credits/spend control | `CreditsSnapshot`, `SpendControlLimitSnapshot`, reached type, reset-credit summary | yes | no | directly usable when returned | Display as account quota data, never as token usage. No balance calculations. |
| Account token analytics | `account/usage/read`; lifetime and daily token buckets | yes | no | directly usable but distinct | Analytics are not ChatGPT-Codex remaining quota. |
| Image URL input | `UserInput { type: image, url, detail }` | yes | `--image` is local-file oriented | directly usable protocol | Remote URL policy requires separate SSRF/content review; not initial upload path. |
| Local image input | `UserInput { type: localImage, path, detail }` | yes | `--image FILE...` | directly usable | Backend converts an owned upload id to a contained server path. Browser never supplies path. |
| Browser upload lifecycle | no upload method, ownership, MIME, quota, or progress contract | no | no | not available | Workbench-owned authenticated HTTP capability, specified separately. |
| Error/retry signal | `error` with `TurnError` and `willRetry`; failed turn; JSON-RPC errors | yes | n/a | directly usable | Deduplicate error notification and terminal failed turn in projection. |
| App-server reconnect state | no notification represents Workbench child/stdio reconnect | no | app-server transports exist | derivable | Workbench supervisor emits `reconnecting`; thread environment connect/disconnect is not reused for transport. |
| Process spawn | process output/exit types exist, but `process/spawn` is absent from the non-experimental `ClientRequest` union | incomplete | feature-gated internals | not available in reviewed surface | Do not call or advertise it. |

## Binding conclusions

- Generated raw types remain integration references, not browser DTOs.
- `danger-full-access` and approval policy are orthogonal. Neither implies the other.
- The absence of a field is represented as unavailable, not inferred from token counts or UI time.
- Runtime probes in the next phase must use a fake child first and the local 0.146.0 executable only
  as an explicit smoke test. No productive app-server connection is enabled in this phase.
