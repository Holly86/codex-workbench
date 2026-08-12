import type { RecoveryEpisode } from "./recoveryEpisodes";
import type { NativeJob } from "./types";
import type { UiPreferences } from "./uiPreferences";

export interface RecoveryNotice extends RecoveryEpisode {
  category: string;
  title: string;
  description: string;
  actionLabel: string;
}

export type NotificationItem =
  | { type: "job"; id: string; job: NativeJob }
  | { type: "recovery"; id: string; notice: RecoveryNotice };

export interface NotificationSelection {
  allUndismissed: NotificationItem[];
  visibleForCurrentFilter: NotificationItem[];
  activeCount: number;
  unreadTerminalCount: number;
  unreadRecoveryCount: number;
  badgeCount: number;
  completedVisibleIds: string[];
  seenCandidateIds: string[];
}

const TERMINAL = new Set<NativeJob["status"]>([
  "succeeded", "failed", "timed_out", "interrupted",
]);
const FAILED = new Set<NativeJob["status"]>(["failed", "timed_out", "interrupted"]);
const NEW_TERMINAL_SECONDS = 24 * 60 * 60;

export function selectNotifications({
  jobs, episodes, preferences, now = Date.now(),
}: {
  jobs: NativeJob[];
  episodes: RecoveryEpisode[];
  preferences: UiPreferences["notifications"];
  now?: number;
}): NotificationSelection {
  const dismissed = new Set(preferences.dismissed.map((entry) => entry.id));
  const seen = new Set(preferences.seen.map((entry) => entry.id));
  const recentThreshold = Math.floor(now / 1000) - NEW_TERMINAL_SECONDS;
  const allUndismissed: NotificationItem[] = [
    ...jobs.filter((job) => !dismissed.has(job.id)).map((job): NotificationItem => ({
      type: "job", id: job.id, job,
    })),
    ...episodes.map(recoveryNotice).filter((notice) => !dismissed.has(notice.id))
      .map((notice): NotificationItem => ({ type: "recovery", id: notice.id, notice })),
  ];
  const visibleForCurrentFilter = allUndismissed.filter((item) => {
    if (preferences.filter === "all") return true;
    if (item.type === "recovery") {
      return preferences.filter === "active"
        ? item.notice.state === "recovering" : item.notice.state === "failed";
    }
    return preferences.filter === "active"
      ? isActive(item.job) : FAILED.has(item.job.status);
  });
  const activeCount = allUndismissed.filter(
    (item) => item.type === "job" && isActive(item.job),
  ).length;
  const unreadTerminalCount = allUndismissed.filter((item) =>
    item.type === "job"
    && TERMINAL.has(item.job.status)
    && (item.job.endedAt ?? 0) >= recentThreshold
    && !seen.has(item.id),
  ).length;
  const unreadRecoveryCount = allUndismissed.filter(
    (item) => item.type === "recovery" && !seen.has(item.id),
  ).length;
  const seenCandidateIds = visibleForCurrentFilter.filter((item) =>
    item.type === "recovery"
      ? !seen.has(item.id)
      : TERMINAL.has(item.job.status)
        && (item.job.endedAt ?? 0) >= recentThreshold
        && !seen.has(item.id),
  ).map((item) => item.id);
  return {
    allUndismissed,
    visibleForCurrentFilter,
    activeCount,
    unreadTerminalCount,
    unreadRecoveryCount,
    badgeCount: activeCount + unreadTerminalCount + unreadRecoveryCount,
    completedVisibleIds: visibleForCurrentFilter.filter(
      (item) => item.type === "job" && TERMINAL.has(item.job.status),
    ).map((item) => item.id),
    seenCandidateIds,
  };
}

function isActive(job: NativeJob): boolean {
  return job.status === "queued" || job.status === "running";
}

function recoveryNotice(episode: RecoveryEpisode): RecoveryNotice {
  if (episode.kind === "runtime") {
    return {
      ...episode,
      category: "Runtime-Recovery",
      title: episode.state === "recovering"
        ? "Codex Runtime wird wiederhergestellt" : "Codex Runtime ist eingeschränkt",
      description: "Aktive Arbeit wird nicht still verworfen. Im Systemstatus stehen kontrollierte nächste Schritte bereit.",
      actionLabel: "Systemstatus öffnen",
    };
  }
  return {
    ...episode,
    category: "Realtime-Recovery",
    title: episode.state === "recovering"
      ? "Live-Verbindung wird wiederhergestellt" : "Live-Verbindung ist getrennt",
    description: "Die Workbench gleicht nach dem Reconnect einen autoritativen Snapshot ab.",
    actionLabel: "Jetzt neu verbinden",
  };
}
