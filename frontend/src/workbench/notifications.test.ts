import { expect, test } from "vitest";

import type { RecoveryEpisode } from "./recoveryEpisodes";
import type { NativeJob } from "./types";
import { selectNotifications } from "./notifications";
import { defaultUiPreferences } from "./uiPreferences";

const now = 2_000_000_000_000;
const terminal: NativeJob = {
  id: "job_done", repositoryId: "0123456789abcdef", repositoryName: "Repository",
  type: "git_fetch", status: "succeeded", startedAt: now / 1000 - 3,
  endedAt: now / 1000 - 1, exitCode: 0, error: null, errorCode: null,
  retryable: false, operationLabel: "Remote-Stand abrufen", durationMs: 2,
};
const running: NativeJob = {
  ...terminal, id: "job_running", status: "running", endedAt: null,
};
const failed: NativeJob = {
  ...terminal, id: "job_failed", status: "failed",
};
const recovery: RecoveryEpisode = {
  id: "recovery_000001", kind: "realtime", state: "failed",
};

function select(
  jobs: NativeJob[],
  episodes: RecoveryEpisode[] = [],
  notifications = defaultUiPreferences.notifications,
) {
  return selectNotifications({ jobs, episodes, preferences: notifications, now });
}

test("dismissed historical jobs never create a badge", () => {
  const jobs = Array.from({ length: 10 }, (_, index) => ({
    ...terminal, id: `job_${String(index)}`, endedAt: now / 1000 - 100_000,
  }));
  const notifications = {
    ...defaultUiPreferences.notifications,
    dismissed: jobs.map((job) => ({ id: job.id, at: now })),
  };
  expect(select(jobs, [], notifications).badgeCount).toBe(0);
});

test("failed filter does not hide a running job from the global badge", () => {
  const notifications = { ...defaultUiPreferences.notifications, filter: "failed" as const };
  const selection = select([running], [], notifications);
  expect(selection.visibleForCurrentFilter).toEqual([]);
  expect(selection.activeCount).toBe(1);
  expect(selection.badgeCount).toBe(1);
});

test("active filter does not hide a new failure from the global badge", () => {
  const notifications = { ...defaultUiPreferences.notifications, filter: "active" as const };
  const selection = select([failed], [], notifications);
  expect(selection.visibleForCurrentFilter).toEqual([]);
  expect(selection.unreadTerminalCount).toBe(1);
  expect(selection.badgeCount).toBe(1);
});

test("active jobs, terminal jobs, and recovery episodes are counted once each", () => {
  const selection = select([running, terminal], [recovery]);
  expect(selection.activeCount).toBe(1);
  expect(selection.unreadTerminalCount).toBe(1);
  expect(selection.unreadRecoveryCount).toBe(1);
  expect(selection.badgeCount).toBe(3);
  expect(selection.allUndismissed).toHaveLength(3);
});

test("seen terminal notifications and dismissed recovery episodes leave the badge", () => {
  const notifications = {
    ...defaultUiPreferences.notifications,
    seen: [{ id: terminal.id, at: now }],
    dismissed: [{ id: recovery.id, at: now }],
  };
  const selection = select([terminal], [recovery], notifications);
  expect(selection.unreadTerminalCount).toBe(0);
  expect(selection.unreadRecoveryCount).toBe(0);
  expect(selection.badgeCount).toBe(0);
});

test("completed visible ids remove the terminal badge contribution", () => {
  const initial = select([terminal]);
  expect(initial.completedVisibleIds).toEqual([terminal.id]);
  const dismissed = {
    ...defaultUiPreferences.notifications,
    dismissed: [{ id: terminal.id, at: now }],
  };
  expect(select([terminal], [], dismissed).badgeCount).toBe(0);
});

test("a later recovery episode is unread after the previous episode was dismissed", () => {
  const notifications = {
    ...defaultUiPreferences.notifications,
    dismissed: [{ id: recovery.id, at: now }],
  };
  const later = { ...recovery, id: "recovery_000002" };
  const selection = select([], [later], notifications);
  expect(selection.unreadRecoveryCount).toBe(1);
  expect(selection.badgeCount).toBe(1);
});
