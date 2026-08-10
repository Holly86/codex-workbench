import type { RateLimits, ThreadView, TokenBreakdown } from "../workbench/types";

export function UsagePanel({ thread, rateLimits }: { thread: ThreadView | null; rateLimits: RateLimits }) {
  const usage = thread?.usage ?? null;
  const remaining = remainingPrimaryPercent(rateLimits);
  const remainingText = remaining === null ? "nicht verfügbar" : `${remaining.toFixed(1)} % frei`;
  return (
    <details className="usage-panel">
      <summary
        className="status-chip usage-summary"
        data-state={remaining === null ? "pending" : remaining <= 10 ? "negative" : "positive"}
        title={`Restkontingent im Primärfenster: ${remainingText}`}
      >
        <span className="status-dot" aria-hidden="true" />
        <span className="status-key">Kontingent</span>
        <span className="status-value">{remainingText}</span>
        <span className="status-compact">{remainingText}</span>
      </summary>
      <div className="usage-popover">
        <h2>Nutzung &amp; Kontingent</h2>
        <div className="usage-grid">
          <section><h3>Kontext</h3>{usage === null ? <Unavailable /> : <dl>
            <Metric label="Verbrauch" value={usage.context.usedTokens === null ? "nicht verfügbar" : formatNumber(usage.context.usedTokens)} />
            <Metric label="Limit" value={usage.context.limitTokens === null ? "nicht verfügbar" : formatNumber(usage.context.limitTokens)} />
            <Metric label="Anteil" value={usage.context.usedPercent === null ? "nicht verfügbar" : `${usage.context.usedPercent.toFixed(1)} %`} />
          </dl>}</section>
          <section><h3>Letztes Modell</h3><dl><TokenMetrics usage={usage?.latestModelUsage ?? null} /></dl></section>
          <section><h3>Thread gesamt</h3><dl><TokenMetrics usage={usage?.threadTotal ?? null} /></dl></section>
          <section><h3>ChatGPT-Codex-Kontingent</h3>{rateLimits.available ? <dl>
            <Window label="Primärfenster" used={rateLimits.primary?.usedPercent ?? null} reset={rateLimits.primary?.resetsAt ?? null} />
            <Window label="Sekundärfenster" used={rateLimits.secondary?.usedPercent ?? null} reset={rateLimits.secondary?.resetsAt ?? null} />
            <Metric label="Credits" value={creditText(rateLimits)} />
            <Metric label="Erschöpfung" value={rateLimits.spendControlReached === true || rateLimits.rateLimitReachedType ? rateLimits.rateLimitReachedType ?? "Spend Control erreicht" : "nicht gemeldet"} />
            <Metric label="Aktualisiert" value={formatTime(rateLimits.observedAt)} />
          </dl> : <Unavailable />}</section>
        </div>
        <p className="usage-note">„Nahe Limit“ ist eine Workbench-Schwelle ab 90 % Nutzung, kein offizieller OpenAI-Status.</p>
      </div>
    </details>
  );
}

function TokenMetrics({ usage }: { usage: TokenBreakdown | null }) {
  if (usage === null) return <Unavailable />;
  return <>
    <Metric label="Input" value={formatNumber(usage.inputTokens)} />
    <Metric label="Cached Input" value={formatNumber(usage.cachedInputTokens)} />
    <Metric label="Output" value={formatNumber(usage.outputTokens)} />
    <Metric label="Reasoning" value={formatNumber(usage.reasoningOutputTokens)} />
  </>;
}

function Window({ label, used, reset }: { label: string; used: number | null; reset: number | null }) {
  return <Metric label={label} value={used === null ? "nicht verfügbar" : `${used.toFixed(1)} % verbraucht${used >= 90 ? " · nahe Limit" : ""}${reset === null ? "" : ` · Reset ${formatTime(reset)}`}`} />;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><dt>{label}</dt><dd>{value}</dd></div>;
}

function Unavailable() { return <p className="unavailable">nicht verfügbar</p>; }
function formatNumber(value: number) { return new Intl.NumberFormat().format(value); }
function formatTime(value: number) { return value > 0 ? new Date(value * 1000).toLocaleString() : "nicht verfügbar"; }
function remainingPrimaryPercent(rateLimits: RateLimits): number | null {
  const used = rateLimits.available ? rateLimits.primary?.usedPercent ?? null : null;
  return used === null ? null : Math.max(0, Math.min(100, 100 - used));
}
function creditText(rateLimits: RateLimits) {
  if (rateLimits.credits?.unlimited === true) return "unbegrenzt";
  if (rateLimits.credits?.balance !== null && rateLimits.credits?.balance !== undefined) return rateLimits.credits.balance;
  return rateLimits.credits?.hasCredits === true ? "verfügbar" : "nicht verfügbar";
}
