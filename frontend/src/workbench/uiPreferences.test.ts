import { beforeEach, expect, test, vi } from "vitest";

import {
  defaultUiPreferences, dismissNotification, readUiPreferences, resetUiPreferences,
  writeNotificationPreferences, writeSidebarPreferences, writeUiPreferences,
} from "./uiPreferences";
import type { UiPreferences } from "./uiPreferences";

beforeEach(() => localStorage.clear());

test("migrates the legacy sidebar flag into a versioned safe preference", () => {
  localStorage.setItem("workbench.sidebar.collapsed", "true");
  expect(readUiPreferences().sidebar.collapsed).toBe(true);
  expect(readUiPreferences().version).toBe(2);
});

test("migrates version one without losing drawer, filter, or sidebar state", () => {
  localStorage.setItem("codex-workbench.ui.v1", JSON.stringify({
    version: 1,
    notifications: { dismissed: [], filter: "failed", open: true },
    sidebar: { collapsed: false, projectsCollapsed: true, threadsCollapsed: false, projectsHeight: 333 },
  }));
  const migrated = readUiPreferences();
  expect(migrated.version).toBe(2);
  expect(migrated.notifications).toMatchObject({ filter: "failed", open: true });
  expect(migrated.sidebar).toMatchObject({ projectsCollapsed: true, projectsHeight: 333 });
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
  localStorage.setItem("codex-workbench.ui.v2", "{not-json");
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

test("filter and dismissal updates preserve the single open-state authority", () => {
  let preferences: UiPreferences = {
    ...defaultUiPreferences,
    notifications: { ...defaultUiPreferences.notifications, open: true, filter: "failed" },
  };
  preferences = dismissNotification(preferences, "job_opaque");
  writeUiPreferences(preferences);
  const restored = readUiPreferences();
  expect(restored.notifications.open).toBe(true);
  expect(restored.notifications.filter).toBe("failed");
  expect(restored.notifications.dismissed.map((entry) => entry.id)).toContain("job_opaque");
  expect(localStorage.getItem("codex-workbench.ui.v2")).not.toMatch(/repository|prompt|output/i);
});

test("survives denied get, set, and remove storage operations", () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("denied"); });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("quota"); });
  const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("denied"); });
  expect(() => readUiPreferences()).not.toThrow();
  expect(() => writeUiPreferences(defaultUiPreferences)).not.toThrow();
  expect(() => resetUiPreferences()).not.toThrow();
  get.mockRestore(); set.mockRestore(); remove.mockRestore();
});
