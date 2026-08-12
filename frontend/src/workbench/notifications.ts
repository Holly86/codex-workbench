import type { NativeJob } from "./types";
import type { UiPreferences } from "./uiPreferences";

export interface RecoveryNotice {
  id: string;
  kind: "runtime" | "realtime";
  category: string;
  title: string;
  description: string;
  state: "recovering" | "failed";
  actionLabel: string;
}

export interface NotificationSelection {
  jobs: NativeJob[];
  recoveryNotices: RecoveryNotice[];
  badgeCount: number;
  seenCandidateIds: string[];
  completedIds: string[];
}

const TERMINAL = new Set<NativeJob["status"]>([
  "succeeded", "failed", "timed_out", "interrupted",
]);
const FAILED = new Set<NativeJob["status"]>(["failed", "timed_out", "interrupted"]);
const NEW_TERMINAL_SECONDS = 24 * 60 * 60;

export function selectNotifications({
  jobs, preferences, processConnection, realtimeConnection, generation, now = Date.now(),
}: {
  jobs: NativeJob[];
  preferences: UiPreferences["notifications"];
  processConnection: string;
  realtimeConnection: string;
  generation: number;
  now?: number;
}): NotificationSelection {
  const dismissed = new Set(preferences.dismissed.map((entry) => entry.id));
  const seen = new Set(preferences.seen.map((entry) => entry.id));
  const filteredJobs = jobs.filter((job) => !dismissed.has(job.id)).filter((job) => {
    if (preferences.filter === "active") return job.status === "queued" || job.status === "running";
    if (preferences.filter === "failed") return FAILED.has(job.status);
    return true;
  });
  const recoveryNotices = recoveryCandidates(
    processConnection, realtimeConnection, generation,
  ).filter((notice) => !dismissed.has(notice.id)).filter((notice) => {
    if (preferences.filter === "active") return notice.state === "recovering";
    if (preferences.filter === "failed") return notice.state === "failed";
    return true;
  });
  const recentThreshold = Math.floor(now / 1000) - NEW_TERMINAL_SECONDS;
  const badgeJobs = filteredJobs.filter((job) =>
    job.status === "queued" || job.status === "running"
    || (TERMINAL.has(job.status) && (job.endedAt ?? 0) >= recentThreshold && !seen.has(job.id)),
  );
  const unreadRecovery = recoveryNotices.filter((notice) => !seen.has(notice.id));
  return {
    jobs: filteredJobs,
    recoveryNotices,
    badgeCount: badgeJobs.length + unreadRecovery.length,
    seenCandidateIds: [
      ...badgeJobs.filter((job) => TERMINAL.has(job.status)).map((job) => job.id),
      ...unreadRecovery.map((notice) => notice.id),
    ],
    completedIds: filteredJobs.filter((job) => TERMINAL.has(job.status)).map((job) => job.id),
  };
}

function recoveryCandidates(
  processConnection: string, realtimeConnection: string, generation: number,
): RecoveryNotice[] {
  const notices: RecoveryNotice[] = [];
  if (processConnection !== "ready" && processConnection !== "connected") {
    notices.push({
      id: `runtime_recovery_${String(generation)}`,
      kind: "runtime",
      category: "Runtime-Recovery",
      title: processConnection === "reconnecting"
        ? "Codex Runtime wird wiederhergestellt" : "Codex Runtime ist eingeschränkt",
      description: "Aktive Arbeit wird nicht still verworfen. Im Systemstatus stehen kontrollierte nächste Schritte bereit.",
      state: processConnection === "reconnecting" ? "recovering" : "failed",
      actionLabel: "Systemstatus öffnen",
    });
  }
  if (realtimeConnection !== "connected") {
    notices.push({
      id: `realtime_recovery_${String(generation)}`,
      kind: "realtime",
      category: "Realtime-Recovery",
      title: realtimeConnection === "reconnecting" || realtimeConnection === "connecting"
        ? "Live-Verbindung wird wiederhergestellt" : "Live-Verbindung ist getrennt",
      description: "Die Workbench gleicht nach dem Reconnect einen autoritativen Snapshot ab.",
      state: realtimeConnection === "disconnected" ? "failed" : "recovering",
      actionLabel: "Jetzt neu verbinden",
    });
  }
  return notices;
}
