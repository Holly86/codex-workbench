import { beforeEach, expect, test } from "vitest";

import {
  defaultUiPreferences, dismissNotification, readUiPreferences,
  writeNotificationPreferences, writeSidebarPreferences, writeUiPreferences,
} from "./uiPreferences";

beforeEach(() => localStorage.clear());

test("migrates the legacy sidebar flag into a versioned safe preference", () => {
  localStorage.setItem("workbench.sidebar.collapsed", "true");
  expect(readUiPreferences().sidebar.collapsed).toBe(true);
  expect(readUiPreferences().version).toBe(1);
});

test("prunes dismissed notifications by age and a hard 200 item bound", () => {
  const now = 2_000_000_000_000;
  let preferences = defaultUiPreferences;
  for (let index = 0; index < 240; index += 1) {
    preferences = dismissNotification(preferences, `job_${String(index)}`, now - index);
  }
  preferences = {
    ...preferences,
    notifications: {
      ...preferences.notifications,
      dismissed: [...preferences.notifications.dismissed, { id: "expired", at: 0 }],
    },
  };
  writeUiPreferences(preferences);
  const restored = readUiPreferences(now);
  expect(restored.notifications.dismissed).toHaveLength(200);
  expect(restored.notifications.dismissed.some((entry) => entry.id === "expired")).toBe(false);
});

test("resets malformed storage without retaining unknown data", () => {
  localStorage.setItem("codex-workbench.ui.v1", "{not-json");
  expect(readUiPreferences()).toEqual(defaultUiPreferences);
});

test("rejects non-opaque notification identifiers", () => {
  const next = dismissNotification(defaultUiPreferences, "C:/private/repository");
  expect(next).toBe(defaultUiPreferences);
});

test("writes notification and sidebar preferences without overwriting each other", () => {
  writeSidebarPreferences({ ...defaultUiPreferences.sidebar, projectsHeight: 444 });
  writeNotificationPreferences({
    ...defaultUiPreferences.notifications,
    filter: "failed",
    dismissed: [{ id: "job_opaque", at: Date.now() }],
  });
  const restored = readUiPreferences();
  expect(restored.sidebar.projectsHeight).toBe(444);
  expect(restored.notifications.filter).toBe("failed");
  expect(restored.notifications.dismissed[0]?.id).toBe("job_opaque");
});
