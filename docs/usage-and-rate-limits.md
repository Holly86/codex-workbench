# Usage, context, and account limits

## Three separate product concepts

The UI must render three independent sections. Missing values say “not available”; one section is
never used as a substitute for another.

### Current thread context

`thread/tokenUsage/updated` carries `ThreadTokenUsage` with `modelContextWindow`, `last`, and
`total`. The context limit is official when `modelContextWindow` is non-null. The schema does not
provide a field named current-context-used.

Workbench may show a **derived context indicator** only when the adapter has a latest model usage
snapshot: `last.totalTokens / modelContextWindow * 100`. It must label both used tokens and the
percentage “derived from latest model usage”; it is not an account quota and may change after
compaction. If either operand is absent or the runtime probe contradicts this interpretation, used
context and percentage remain unavailable. The value is not silently clamped.

Compaction is independent: an in-progress or completed `contextCompaction` item is authoritative.
The deprecated `thread/compacted` notification may be accepted for compatibility but is not the
primary model. No compaction percentage exists.

### Token usage

`TokenUsageBreakdown` provides all of these official counters:

- `totalTokens`;
- `inputTokens`;
- `cachedInputTokens`;
- `cacheWriteInputTokens`;
- `outputTokens`;
- `reasoningOutputTokens`.

`ThreadTokenUsage.total` is retained as the thread total supplied by Codex. `last` is labelled
latest model usage, not a guaranteed turn total, because 0.146.0 does not document it as a complete
turn aggregation. Although the notification includes a `turnId`, Workbench does not sum repeated
`last` snapshots: that could double count replacements. A turn aggregate remains unavailable until
a runtime fixture proves a correct rule.

`account/usage/read` returns lifetime and daily account token analytics. Those values are shown, if
ever enabled, under token analytics—not as remaining ChatGPT-Codex quota.

### ChatGPT-Codex account limits

`account/rateLimits/read` is the authoritative source. It returns a backward-compatible snapshot,
optional snapshots keyed by `limitId`, and optional rate-limit reset credits. Each snapshot can
contain:

- primary and secondary `usedPercent`, window duration, and reset timestamp;
- credit availability, unlimited flag, and backend-supplied balance string;
- an individual spend-control limit, used amount, remaining percent, and reset;
- spend-control reached state, plan type, and a typed exhaustion reason.

Unix reset timestamps are converted to localized time by the presentation layer without changing
their value. `account/rateLimits/updated` is documented as sparse: the adapter merges non-null data
into the last full read or refetches. A notification's null account metadata does not erase a known
value.

The schema supplies used percent. If the UI displays remaining percent for a rate window, it is an
explicit arithmetic derivation `100 - usedPercent` labelled as derived; official used percent stays
visible and takes precedence. Credit balances are opaque strings and are never converted or
estimated.

## Availability and access

Every normalized snapshot receives a Workbench `observed_at` time so users can see staleness; this
timestamp is not claimed to be the provider's measurement time. Exhaustion can also appear as
`usageLimitExceeded` in `CodexErrorInfo`, but an error is not a substitute for the account snapshot.

Rate-limit data is not authentication material, but it is account metadata. Future read routes and
WebSocket events require the Workbench session boundary. They are `Cache-Control: no-store`, are not
included in health endpoints or logs, and are never sent to unauthenticated clients.

## Explicit non-goals

- Do not translate token counts into hours, turns, currency, or remaining quota.
- Do not estimate reset times when `resetsAt` is null.
- Do not combine primary and secondary windows into one percentage.
- Do not infer credits from plan type.
- Do not treat a context-window percentage as billing or ChatGPT allowance.
