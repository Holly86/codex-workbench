import { useEffect, useMemo, useRef, useState } from "react";

import type { NativeJob } from "../workbench/types";
import {
  defaultUiPreferences,
  dismissNotification,
  dismissNotifications,
  readUiPreferences,
  writeNotificationDrawerOpen,
  writeNotificationPreferences,
} from "../workbench/uiPreferences";
import { EmptyState, StatusBadge } from "./ui";

export function NotificationCenter({
  open, jobs, processConnection = "connected", realtimeConnection = "connected",
  generation = 0, onClose, onRetry, onReconnectRealtime, onOpenSystemStatus,
}: {
  open: boolean; jobs: NativeJob[]; processConnection?: string; realtimeConnection?: string;
  generation?: number; onClose: () => void; onRetry: (job: NativeJob) => Promise<void>;
  onReconnectRealtime?: () => void; onOpenSystemStatus?: () => void;
}) {
  const [preferences, setPreferences] = useState(readUiPreferences);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => writeNotificationPreferences(preferences.notifications), [preferences.notifications]);
  useEffect(() => writeNotificationDrawerOpen(open), [open]);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    window.setTimeout(() => closeRef.current?.focus());
    return () => window.removeEventListener("keydown", escape);
  }, [open, onClose]);
  const dismissed = useMemo(
    () => new Set(preferences.notifications.dismissed.map((entry) => entry.id)),
    [preferences.notifications.dismissed],
  );
  const visible = jobs.filter((job) => !dismissed.has(job.id)).filter((job) => {
    if (preferences.notifications.filter === "active") return job.status === "queued" || job.status === "running";
    if (preferences.notifications.filter === "failed") return ["failed", "timed_out", "interrupted"].includes(job.status);
    return true;
  });
  const recoveryNotices = [
    processConnection === "ready" || processConnection === "connected" ? null : {
      id: `runtime_recovery_${String(generation)}`,
      category: "Runtime-Recovery",
      title: processConnection === "reconnecting" ? "Codex Runtime wird wiederhergestellt" : "Codex Runtime ist eingeschränkt",
      description: "Aktive Arbeit wird nicht still verworfen. Im Systemstatus stehen kontrollierte nächste Schritte bereit.",
      state: processConnection === "reconnecting" ? "recovering" : "failed",
      action: onOpenSystemStatus,
      actionLabel: "Systemstatus öffnen",
    },
    realtimeConnection === "connected" ? null : {
      id: `realtime_recovery_${String(generation)}`,
      category: "Realtime-Recovery",
      title: realtimeConnection === "reconnecting" || realtimeConnection === "connecting" ? "Live-Verbindung wird wiederhergestellt" : "Live-Verbindung ist getrennt",
      description: "Die Workbench gleicht nach dem Reconnect einen autoritativen Snapshot ab.",
      state: realtimeConnection === "disconnected" ? "failed" : "recovering",
      action: onReconnectRealtime,
      actionLabel: "Jetzt neu verbinden",
    },
  ].filter((notice): notice is RecoveryNotice => notice !== null)
    .filter((notice) => !dismissed.has(notice.id))
    .filter((notice) => preferences.notifications.filter !== "failed" || notice.state === "failed");
  const dismiss = (id: string) => setPreferences((current) => dismissNotification(current, id));
  const completedIds = visible
    .filter((job) => !["queued", "running"].includes(job.status))
    .map((job) => job.id);
  return <>
    {open ? <button type="button" className="drawer-scrim" aria-label="Benachrichtigungen schließen" onClick={onClose} /> : null}
    <aside className={`notification-center ${open ? "is-open" : ""}`} aria-hidden={!open} aria-label="Benachrichtigungen">
      <header><div><p className="eyebrow">Aktivität</p><h2>Benachrichtigungen</h2></div><button ref={closeRef} type="button" className="icon-button" aria-label="Benachrichtigungen schließen" onClick={onClose}>×</button></header>
      <div className="notification-filters" role="group" aria-label="Benachrichtigungen filtern">
        {(["all", "active", "failed"] as const).map((filter) => <button type="button" key={filter} aria-pressed={preferences.notifications.filter === filter} onClick={() => setPreferences((current) => ({ ...current, notifications: { ...current.notifications, filter } }))}>{filter === "all" ? "Alle" : filter === "active" ? "Aktiv" : "Fehler"}</button>)}
      </div>
      <div className="notification-bulk-actions">
        <button type="button" disabled={completedIds.length === 0} onClick={() => setPreferences((current) => dismissNotifications(current, completedIds))}>Alle erledigten ausblenden</button>
        <button type="button" onClick={() => setPreferences((current) => ({ ...current, notifications: { ...defaultUiPreferences.notifications, open: current.notifications.open } }))}>Ansicht zurücksetzen</button>
      </div>
      <div className="notification-list">
        {visible.length === 0 && recoveryNotices.length === 0 ? <EmptyState>Keine Benachrichtigungen in diesem Filter.</EmptyState> : null}
        {recoveryNotices.map((notice) => <article key={notice.id}>
          <p className="notification-category">{notice.category}</p>
          <header><strong>{notice.title}</strong><StatusBadge state={notice.state}>{notice.state === "failed" ? "Eingriff möglich" : "Wiederherstellung"}</StatusBadge></header>
          <p>{notice.description}</p>
          <footer>{notice.action ? <button type="button" onClick={notice.action}>{notice.actionLabel}</button> : null}<button type="button" onClick={() => dismiss(notice.id)}>Ausblenden</button></footer>
        </article>)}
        {visible.map((job) => <article key={job.id}>
          <p className="notification-category">Git-Auftrag</p>
          <header><div><strong>{job.operationLabel}</strong><small>{job.repositoryName}</small></div><StatusBadge state={job.status}>{jobStatus(job.status)}</StatusBadge></header>
          <p>{job.error ?? jobDescription(job)}</p>
          <small>{new Date(job.startedAt * 1000).toLocaleString()}{job.durationMs === null ? "" : ` · ${String(job.durationMs)} ms`}</small>
          {job.errorCode ? <details><summary>Technische Details</summary><code>{job.errorCode}</code></details> : null}
          <footer>{job.retryable && job.repositoryId !== null ? <button type="button" onClick={() => void onRetry(job)}>Sicher erneut versuchen</button> : null}{!["queued", "running"].includes(job.status) ? <button type="button" onClick={() => dismiss(job.id)}>Ausblenden</button> : null}</footer>
        </article>)}
      </div>
    </aside>
  </>;
}

interface RecoveryNotice {
  id: string;
  category: string;
  title: string;
  description: string;
  state: "recovering" | "failed";
  action: (() => void) | undefined;
  actionLabel: string;
}

function jobStatus(status: NativeJob["status"]): string { return ({ queued: "Ausstehend", running: "Läuft", succeeded: "Erfolgreich", failed: "Fehlgeschlagen", timed_out: "Zeitüberschreitung", interrupted: "Unterbrochen" })[status]; }
function jobDescription(job: NativeJob): string { return job.status === "succeeded" ? "Der Vorgang wurde abgeschlossen." : job.status === "running" ? "Der Vorgang läuft unabhängig von dieser Browseransicht weiter." : "Der Vorgang wartet auf Ausführung."; }
