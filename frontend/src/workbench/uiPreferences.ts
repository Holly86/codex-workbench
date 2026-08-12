const STORAGE_KEY = "codex-workbench.ui.v1";
const MAX_DISMISSED = 200;
const DISMISSED_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export interface UiPreferences {
  version: 1;
  notifications: {
    dismissed: Array<{ id: string; at: number }>;
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
  version: 1,
  notifications: { dismissed: [], filter: "all", open: false },
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
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as unknown;
    if (!record(value) || value.version !== 1 || !record(value.notifications) || !record(value.sidebar)) {
      return migrateLegacyPreferences();
    }
    const filter = value.notifications.filter;
    const dismissed = Array.isArray(value.notifications.dismissed)
      ? value.notifications.dismissed.flatMap((entry) =>
          record(entry) && typeof entry.id === "string" && isOpaqueId(entry.id)
            && typeof entry.at === "number"
            && now - entry.at <= DISMISSED_TTL_MS
            ? [{ id: entry.id.slice(0, 160), at: entry.at }]
            : [],
        ).slice(-MAX_DISMISSED)
      : [];
    return {
      version: 1,
      notifications: {
        dismissed,
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
  } catch {
    return migrateLegacyPreferences();
  }
}

export function writeUiPreferences(value: UiPreferences): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Preferences are optional; storage denial must never block the Workbench.
  }
}

export function writeNotificationPreferences(value: UiPreferences["notifications"]): void {
  const current = readUiPreferences();
  writeUiPreferences({ ...current, notifications: value });
}

export function writeSidebarPreferences(value: UiPreferences["sidebar"]): void {
  const current = readUiPreferences();
  writeUiPreferences({ ...current, sidebar: value });
}

export function writeNotificationDrawerOpen(open: boolean): void {
  const current = readUiPreferences();
  writeUiPreferences({
    ...current,
    notifications: { ...current.notifications, open },
  });
}

export function dismissNotification(
  preferences: UiPreferences, id: string, now = Date.now(),
): UiPreferences {
  if (!isOpaqueId(id)) return preferences;
  const dismissed = [
    ...preferences.notifications.dismissed.filter((entry) => entry.id !== id),
    { id: id.slice(0, 160), at: now },
  ].filter((entry) => now - entry.at <= DISMISSED_TTL_MS).slice(-MAX_DISMISSED);
  return { ...preferences, notifications: { ...preferences.notifications, dismissed } };
}

export function dismissNotifications(
  preferences: UiPreferences, ids: string[], now = Date.now(),
): UiPreferences {
  return ids.reduce(
    (current, id) => dismissNotification(current, id, now),
    preferences,
  );
}

export function clampHeight(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(140, Math.min(520, Math.round(value)))
    : defaultUiPreferences.sidebar.projectsHeight;
}

function migrateLegacyPreferences(): UiPreferences {
  const collapsed = localStorage.getItem("workbench.sidebar.collapsed") === "true";
  return { ...defaultUiPreferences, sidebar: { ...defaultUiPreferences.sidebar, collapsed } };
}

function isOpaqueId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$/.test(value);
}
