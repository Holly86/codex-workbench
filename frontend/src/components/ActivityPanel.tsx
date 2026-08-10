import { memo, useEffect, useState } from "react";

import type { TimelineEntry } from "../workbench/realtime";
import type { ThreadView } from "../workbench/types";

export function ActivityPanel({ thread, timeline, runtimeState }: { thread: ThreadView | null; timeline: TimelineEntry[]; runtimeState: string }) {
  const activity = thread?.activity ?? null;
  const working = thread?.activeTurnId !== null && thread?.activeTurnId !== undefined;
  const planItems = thread?.turns.flatMap((turn) => turn.items).filter(
    (item) => (item.type === "plan" || item.type === "planUpdate") && (item.plan?.length ?? 0) > 0,
  ) ?? [];
  const plan = planItems.at(-1)?.plan;

  if (activity === null || thread === null) return null;
  return (
    <section className={`activity-banner state-${activity.state}`} aria-labelledby="activity-heading">
      <div className="activity-summary">
        <span className={working ? "working-pulse" : "status-dot"} aria-hidden="true" />
        <strong id="activity-heading" aria-live="polite">{working ? activity.label : terminalLabel(thread)}</strong>
        {working ? <Elapsed startedAt={activity.started_at} fallback={activity.elapsed_ms} /> : null}
        {plan !== undefined ? <span className="plan-progress">{plan.filter((step) => step.status === "completed").length}/{plan.length} Schritte</span> : null}
        {runtimeState !== "connected" ? <span className="runtime-note">App Server: {runtimeLabel(runtimeState)}</span> : null}
      </div>
      <details className="activity-details">
        <summary>{activity.last_activity ?? (working ? "Aktivität läuft" : "Letzte Aktivität")}</summary>
        {activity.current_tool !== null ? <span>Tool: {activity.current_tool}</span> : null}
        {activity.current_command !== null ? <code>{activity.current_command}</code> : null}
        {activity.error_message !== null ? <p role="alert">{activity.error_message}</p> : null}
        <h3>Aktivitätsverlauf ({timeline.length})</h3>
        {timeline.length === 0 ? <p>Noch keine Aktivität.</p> : (
          <ol>{timeline.map((entry) => <li key={entry.key}><time>{new Date(entry.occurredAt).toLocaleTimeString()}</time> {entry.label}</li>)}</ol>
        )}
      </details>
    </section>
  );
}

const Elapsed = memo(function Elapsed({ startedAt, fallback }: { startedAt: string | null; fallback: number | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  const started = startedAt === null ? null : Date.parse(startedAt);
  const elapsed = started === null || !Number.isFinite(started) ? fallback : now - started;
  return elapsed === null ? null : <time>{formatElapsed(elapsed)}</time>;
});

function terminalLabel(thread: ThreadView): string {
  if (thread.activity.state === "failed") return "Fehlgeschlagen";
  if (thread.activity.state === "cancelled") return "Abgebrochen";
  return "Bereit";
}

function runtimeLabel(state: string): string {
  if (state === "reconnecting") return "Verbindung wird wiederhergestellt";
  if (state === "failed") return "nicht verfügbar";
  return "getrennt";
}

function formatElapsed(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
