import { expect, test } from "vitest";

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

function select(jobs: NativeJob[], notifications = defaultUiPreferences.notifications, process = "connected") {
  return selectNotifications({ jobs, preferences: notifications, processConnection: process,
    realtimeConnection: "connected", generation: 3, now });
}

test("dismissed historical jobs never create a badge", () => {
  const jobs = Array.from({ length: 10 }, (_, index) => ({
    ...terminal, id: `job_${String(index)}`, endedAt: now / 1000 - 100_000,
  }));
  const notifications = {
    ...defaultUiPreferences.notifications,
    dismissed: jobs.map((job) => ({ id: job.id, at: now })),
  };
  expect(select(jobs, notifications).badgeCount).toBe(0);
});

test("counts one active job and one current recovery notice", () => {
  const running = { ...terminal, id: "job_running", status: "running" as const, endedAt: null };
  expect(select([running]).badgeCount).toBe(1);
  expect(select([], defaultUiPreferences.notifications, "reconnecting").badgeCount).toBe(1);
});

test("a recent terminal transition is counted exactly once until seen", () => {
  const fresh = select([terminal]);
  expect(fresh.badgeCount).toBe(1);
  expect(fresh.seenCandidateIds).toEqual([terminal.id]);
  const seen = {
    ...defaultUiPreferences.notifications,
    seen: [{ id: terminal.id, at: now }],
  };
  expect(select([terminal], seen).badgeCount).toBe(0);
});
