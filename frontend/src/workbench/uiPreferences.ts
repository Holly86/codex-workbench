const STORAGE_KEY = "codex-workbench.ui.v2";
const LEGACY_STORAGE_KEY = "codex-workbench.ui.v1";
const MAX_NOTIFICATION_RECORDS = 200;
const NOTIFICATION_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export const safeStorage = {
  getItem(key: string): string | null {
    try { return typeof localStorage === "undefined" ? null : localStorage.getItem(key); }
    catch { return null; }
  },
  setItem(key: string, value: string): void {
    try { if (typeof localStorage !== "undefined") localStorage.setItem(key, value); }
    catch { /* Preferences are optional. */ }
  },
  removeItem(key: string): void {
    try { if (typeof localStorage !== "undefined") localStorage.removeItem(key); }
    catch { /* Preferences are optional. */ }
  },
};

export interface NotificationRecord { id: string; at: number }

export interface UiPreferences {
  version: 2;
  notifications: {
    dismissed: NotificationRecord[];
    seen: NotificationRecord[];
    filter: "all" | "active" | "failed";
    open: boolean;
  };
  sidebar: {
    collapsed: boolean;
    projectsCollapsed: boolean;
    threadsCollapsed: boolean;
    projectsHeight: number;
  };
}

export const defaultUiPreferences: UiPreferences = {
  version: 2,
  notifications: { dismissed: [], seen: [], filter: "all", open: false },
  sidebar: {
    collapsed: false,
    projectsCollapsed: false,
    threadsCollapsed: false,
    projectsHeight: 280,
  },
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function readUiPreferences(now = Date.now()): UiPreferences {
  const current = parsePreferences(safeStorage.getItem(STORAGE_KEY), now, 2);
  if (current !== null) return current;
  const legacy = parsePreferences(safeStorage.getItem(LEGACY_STORAGE_KEY), now, 1);
  if (legacy !== null) return legacy;
  return {
    ...defaultUiPreferences,
    notifications: { ...defaultUiPreferences.notifications },
    sidebar: {
      ...defaultUiPreferences.sidebar,
      collapsed: safeStorage.getItem("workbench.sidebar.collapsed") === "true",
    },
  };
}

export function writeUiPreferences(value: UiPreferences): void {
  safeStorage.setItem(STORAGE_KEY, JSON.stringify(value));
}

export function resetUiPreferences(): UiPreferences {
  safeStorage.removeItem(STORAGE_KEY);
  safeStorage.removeItem(LEGACY_STORAGE_KEY);
  safeStorage.removeItem("workbench.sidebar.collapsed");
  return defaultUiPreferences;
}

export function writeNotificationPreferences(value: UiPreferences["notifications"]): void {
  const current = readUiPreferences();
  writeUiPreferences({ ...current, notifications: value });
}

export function writeSidebarPreferences(value: UiPreferences["sidebar"]): void {
  const current = readUiPreferences();
  writeUiPreferences({ ...current, sidebar: value });
}

export function dismissNotification(
  preferences: UiPreferences, id: string, now = Date.now(),
): UiPreferences {
  if (!isOpaqueId(id)) return preferences;
  return {
    ...preferences,
    notifications: {
      ...preferences.notifications,
      dismissed: appendRecord(preferences.notifications.dismissed, id, now),
    },
  };
}

export function dismissNotifications(
  preferences: UiPreferences, ids: string[], now = Date.now(),
): UiPreferences {
  return ids.reduce((current, id) => dismissNotification(current, id, now), preferences);
}

export function markNotificationsSeen(
  preferences: UiPreferences, ids: string[], now = Date.now(),
): UiPreferences {
  const seen = ids.reduce((current, id) => appendRecord(current, id, now), preferences.notifications.seen);
  return { ...preferences, notifications: { ...preferences.notifications, seen } };
}

export function clampHeight(value: unknown, maximum = 520): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(140, Math.min(Math.max(140, maximum), Math.round(value)))
    : defaultUiPreferences.sidebar.projectsHeight;
}

function parsePreferences(raw: string | null, now: number, version: 1 | 2): UiPreferences | null {
  try {
    const value = JSON.parse(raw ?? "null") as unknown;
    if (!record(value) || value.version !== version || !record(value.notifications) || !record(value.sidebar)) return null;
    const filter = value.notifications.filter;
    return {
      version: 2,
      notifications: {
        dismissed: parseRecords(value.notifications.dismissed, now),
        seen: version === 2 ? parseRecords(value.notifications.seen, now) : [],
        filter: filter === "active" || filter === "failed" ? filter : "all",
        open: value.notifications.open === true,
      },
      sidebar: {
        collapsed: value.sidebar.collapsed === true,
        projectsCollapsed: value.sidebar.projectsCollapsed === true,
        threadsCollapsed: value.sidebar.threadsCollapsed === true,
        projectsHeight: clampHeight(value.sidebar.projectsHeight),
      },
    };
  } catch { return null; }
}

function parseRecords(value: unknown, now: number): NotificationRecord[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) =>
    record(entry) && typeof entry.id === "string" && isOpaqueId(entry.id)
      && typeof entry.at === "number" && entry.at <= now + 5 * 60 * 1000
      && now - entry.at <= NOTIFICATION_TTL_MS
      ? [{ id: entry.id.slice(0, 160), at: entry.at }]
      : [],
  ).slice(-MAX_NOTIFICATION_RECORDS);
}

function appendRecord(current: NotificationRecord[], id: string, now: number): NotificationRecord[] {
  if (!isOpaqueId(id)) return current;
  return [...current.filter((entry) => entry.id !== id), { id: id.slice(0, 160), at: now }]
    .filter((entry) => entry.at <= now + 5 * 60 * 1000 && now - entry.at <= NOTIFICATION_TTL_MS)
    .slice(-MAX_NOTIFICATION_RECORDS);
}

function isOpaqueId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$/.test(value);
}
