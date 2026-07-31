import { useEffect, useState } from "react";

import type { TimelineEntry } from "../workbench/realtime";
import type { ThreadView } from "../workbench/types";

const TERMINAL = new Set(["idle", "completed", "failed", "cancelled"]);

export function ActivityPanel({ thread, timeline }: { thread: ThreadView | null; timeline: TimelineEntry[] }) {
  const [now, setNow] = useState(() => Date.now());
  const activity = thread?.activity ?? null;
  const working = activity !== null && !TERMINAL.has(activity.state);

  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [working]);

  if (activity === null) return null;
  const started = activity.started_at === null ? null : Date.parse(activity.started_at);
  const elapsed = started === null || !Number.isFinite(started) ? activity.elapsed_ms : now - started;
  return (
    <section className={`activity-banner state-${activity.state}`} aria-labelledby="activity-heading">
      <div>
        <span className={working ? "working-pulse" : "status-dot"} aria-hidden="true" />
        <strong id="activity-heading" aria-live="polite">{working ? "Working…" : activity.label}</strong>
        <span>{activity.last_activity ?? "Bereit"}</span>
      </div>
      {working && elapsed !== null ? <time>{formatElapsed(elapsed)}</time> : null}
      {activity.current_tool !== null ? <span>Tool: {activity.current_tool}</span> : null}
      {activity.current_command !== null ? <code>{activity.current_command}</code> : null}
      {activity.error_message !== null ? <p role="alert">{activity.error_message}</p> : null}
      <details className="timeline">
        <summary>Activity Timeline ({timeline.length})</summary>
        {timeline.length === 0 ? <p>Noch keine Aktivität.</p> : (
          <ol>{timeline.map((entry) => <li key={entry.key}><time>{new Date(entry.occurredAt).toLocaleTimeString()}</time> {entry.label}</li>)}</ol>
        )}
      </details>
    </section>
  );
}

function formatElapsed(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
