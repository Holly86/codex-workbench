import { useEffect, useMemo } from "react";
import type { Dispatch, SetStateAction } from "react";

import type { NativeJob } from "../workbench/types";
import { selectNotifications } from "../workbench/notifications";
import type { RecoveryEpisode } from "../workbench/recoveryEpisodes";
import {
  defaultUiPreferences,
  dismissNotification,
  dismissNotifications,
  markNotificationsSeen,
} from "../workbench/uiPreferences";
import type { UiPreferences } from "../workbench/uiPreferences";
import { EmptyState, StatusBadge } from "./ui";
import { useDrawerLifecycle } from "./useDrawerLifecycle";

export function NotificationCenter({
  open, jobs, recoveryEpisodes, preferences, onPreferencesChange, onClose, onRetry,
  onReconnectRealtime, onOpenSystemStatus,
}: {
  open: boolean; jobs: NativeJob[]; recoveryEpisodes: RecoveryEpisode[];
  preferences: UiPreferences; onPreferencesChange: Dispatch<SetStateAction<UiPreferences>>;
  onClose: () => void; onRetry: (job: NativeJob) => Promise<void>;
  onReconnectRealtime?: () => void; onOpenSystemStatus?: () => void;
}) {
  const { mobile, drawerRef, closeRef } = useDrawerLifecycle(open, onClose);
  const selection = useMemo(() => selectNotifications({
    jobs, episodes: recoveryEpisodes, preferences: preferences.notifications,
  }), [jobs, preferences.notifications, recoveryEpisodes]);
  const seenKey = selection.seenCandidateIds.join("\0");
  useEffect(() => {
    if (!open || seenKey.length === 0) return;
    const ids = seenKey.split("\0");
    onPreferencesChange((current) => markNotificationsSeen(current, ids));
  }, [open, onPreferencesChange, seenKey]);
  const dismiss = (id: string) => onPreferencesChange((current) => dismissNotification(current, id));
  if (!open) return null;
  return <>
    {mobile ? <button type="button" className="drawer-scrim" aria-label="Benachrichtigungen schließen" onClick={onClose} /> : null}
    <aside ref={drawerRef} className="notification-center is-open" aria-label="Benachrichtigungen" role={mobile ? "dialog" : undefined} aria-modal={mobile ? true : undefined} tabIndex={-1}>
      <header><div><p className="eyebrow">Aktivität</p><h2>Benachrichtigungen</h2></div><button ref={closeRef} type="button" className="icon-button" aria-label="Benachrichtigungen schließen" onClick={onClose}>×</button></header>
      <div className="notification-filters" role="group" aria-label="Benachrichtigungen filtern">
        {(["all", "active", "failed"] as const).map((filter) => <button type="button" key={filter} aria-pressed={preferences.notifications.filter === filter} onClick={() => onPreferencesChange((current) => ({ ...current, notifications: { ...current.notifications, filter } }))}>{filter === "all" ? "Alle" : filter === "active" ? "Aktiv" : "Fehler"}</button>)}
      </div>
      <div className="notification-bulk-actions">
        <button type="button" disabled={selection.completedVisibleIds.length === 0} onClick={() => onPreferencesChange((current) => dismissNotifications(current, selection.completedVisibleIds))}>Alle erledigten ausblenden</button>
        <button type="button" onClick={() => onPreferencesChange((current) => ({ ...current, notifications: { ...defaultUiPreferences.notifications, open: current.notifications.open } }))}>Ansicht zurücksetzen</button>
      </div>
      <div className="notification-list">
        {selection.visibleForCurrentFilter.length === 0 ? <EmptyState>Keine Benachrichtigungen in diesem Filter.</EmptyState> : null}
        {selection.visibleForCurrentFilter.filter((item) => item.type === "recovery").map((item) => {
          const notice = item.notice;
          return <article key={notice.id}>
          <p className="notification-category">{notice.category}</p>
          <header><strong>{notice.title}</strong><StatusBadge state={notice.state}>{notice.state === "failed" ? "Eingriff möglich" : "Wiederherstellung"}</StatusBadge></header>
          <p>{notice.description}</p>
          <footer><button type="button" onClick={notice.kind === "runtime" ? onOpenSystemStatus : onReconnectRealtime}>{notice.actionLabel}</button><button type="button" onClick={() => dismiss(notice.id)}>Ausblenden</button></footer>
        </article>;
        })}
        {selection.visibleForCurrentFilter.filter((item) => item.type === "job").map((item) => {
          const job = item.job;
          return <article key={job.id}>
          <p className="notification-category">Git-Auftrag</p>
          <header><div><strong>{job.operationLabel}</strong><small>{job.repositoryName}</small></div><StatusBadge state={job.status}>{jobStatus(job.status)}</StatusBadge></header>
          <p>{job.error ?? jobDescription(job)}</p>
          <small>{new Date(job.startedAt * 1000).toLocaleString()}{job.durationMs === null ? "" : ` · ${String(job.durationMs)} ms`}</small>
          {job.errorCode ? <details><summary>Technische Details</summary><code>{job.errorCode}</code></details> : null}
          <footer>{job.retryable && job.repositoryId !== null ? <button type="button" onClick={() => void onRetry(job)}>Sicher erneut versuchen</button> : null}{!["queued", "running"].includes(job.status) ? <button type="button" onClick={() => dismiss(job.id)}>Ausblenden</button> : null}</footer>
        </article>;
        })}
      </div>
    </aside>
  </>;
}

function jobStatus(status: NativeJob["status"]): string { return ({ queued: "Ausstehend", running: "Läuft", succeeded: "Erfolgreich", failed: "Fehlgeschlagen", timed_out: "Zeitüberschreitung", interrupted: "Unterbrochen" })[status]; }
function jobDescription(job: NativeJob): string { return job.status === "succeeded" ? "Der Vorgang wurde abgeschlossen." : job.status === "running" ? "Der Vorgang läuft unabhängig von dieser Browseransicht weiter." : "Der Vorgang wartet auf Ausführung."; }
