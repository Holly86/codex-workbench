import { expect, test } from "@playwright/test";
import type { Locator, Page, WebSocketRoute } from "@playwright/test";

const repositoryId = "0123456789abcdef";
const threadId = "thread_synthetic";

class SyntheticBackend {
  statusRequests = 0;
  submittedBranchTarget: string | null = null;
  submittedLocalBranch: string | null = null;
  jobs = [job("job_terminal", "failed")];
  private sequence = 0;
  private socket: WebSocketRoute | null = null;

  constructor(private readonly page: Page) {}

  async install(): Promise<void> {
    await this.page.routeWebSocket("**/ws/events", (socket) => {
      this.socket = socket;
      socket.onMessage((message) => {
        const parsed = JSON.parse(String(message)) as { type?: string };
        if (parsed.type === "subscribe") socket.send(JSON.stringify(this.snapshot()));
      });
    });
    await this.page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      let response: unknown = {};
      if (url.pathname === "/api/auth/status") {
        response = { state: "authenticated", csrfToken: "csrf_synthetic", expiresAt: 2_000_000_000, reason: null };
      } else if (url.pathname === "/api/repositories") {
        response = { repositories: [repository] };
      } else if (url.pathname === "/api/threads") {
        response = { threads: [thread] };
      } else if (url.pathname === "/api/capabilities") {
        response = capabilities;
      } else if (url.pathname === "/api/usage/rate-limits") {
        response = rateLimits;
      } else if (url.pathname === "/api/jobs") {
        response = { jobs: this.jobs };
      } else if (url.pathname === "/api/system/status") {
        this.statusRequests += 1;
        response = systemStatus;
      } else if (url.pathname === "/api/system/runtime/restart") {
        response = { result: "started" };
      } else if (url.pathname.endsWith("/git/branches")) {
        response = branches;
      } else if (url.pathname.endsWith("/git") && request.method() === "POST") {
        const body = request.postDataJSON() as { branchTargetId?: unknown; localBranchName?: unknown };
        this.submittedBranchTarget = typeof body.branchTargetId === "string" ? body.branchTargetId : null;
        this.submittedLocalBranch = typeof body.localBranchName === "string" ? body.localBranchName : null;
        response = { exitCode: 0 };
      } else if (url.pathname === "/api/github/capability") {
        response = { available: false, authenticated: false, reason: "not_configured" };
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
    });
  }

  emit(type: string, payload: Record<string, unknown>, eventThreadId: string | null = null): void {
    this.sequence += 1;
    this.socket?.send(JSON.stringify({
      sequence: this.sequence,
      occurredAt: "2030-01-01T00:00:00Z",
      type,
      payload,
      threadId: eventThreadId,
      turnId: null,
      itemId: null,
    }));
  }

  private snapshot(): Record<string, unknown> {
    return {
      type: "connection.snapshot",
      latestSequence: this.sequence,
      missedEvents: false,
      snapshot: {
        connection: { state: "connected", generation: 3, message: null },
        threads: [thread],
        pendingApprovals: [],
        rateLimits,
        jobs: this.jobs,
      },
    };
  }
}

let backend: SyntheticBackend;

test.beforeEach(async ({ page }) => {
  backend = new SyntheticBackend(page);
  await backend.install();
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Repositories" })).toBeVisible();
  await page.getByRole("button", { name: /^Synthetic thread/ }).click();
  await expect(page.getByRole("heading", { name: "Synthetic thread" })).toBeVisible();
});

test("1 system status opens and closes", async ({ page }) => {
  await openSystemStatus(page);
  await expect(page.locator(".system-status-drawer")).toBeVisible();
  await page.getByRole("button", { name: "Systemstatus schließen" }).click();
  await expect(page.locator(".system-status-drawer")).toHaveCount(0);
});

test("2 streaming parent rerenders do not repeat status requests or focus", async ({ page }) => {
  await openSystemStatus(page);
  const close = page.getByRole("button", { name: "Systemstatus schließen" });
  await expect(close).toBeFocused();
  for (let index = 1; index <= 100; index += 1) {
    backend.emit("thread.name", { name: `Synthetic title ${String(index)}` }, threadId);
    await expect(page.getByRole("heading", { name: `Synthetic title ${String(index)}` })).toBeVisible();
  }
  expect(backend.statusRequests).toBe(1);
  await expect(close).toBeFocused();
});

test("3 notification dismissal survives a browser reload", async ({ page }) => {
  await page.getByRole("button", { name: /Benachrichtigungen öffnen/ }).click();
  await page.getByRole("button", { name: "Ausblenden", exact: true }).click();
  await expect(page.getByText("Synthetic fetch")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Benachrichtigungen" })).toBeVisible();
  await expect(page.getByText("Synthetic fetch")).toHaveCount(0);
});

test("4 badge follows a new terminal event and bulk dismissal", async ({ page }) => {
  await page.getByRole("button", { name: /Benachrichtigungen öffnen/ }).click();
  await page.getByRole("button", { name: "Benachrichtigungen schließen" }).click();
  backend.emit("job.updated", job("job_new_terminal", "failed"));
  await expect(page.getByRole("button", { name: /1 neu/ })).toBeVisible();
  await page.getByRole("button", { name: /Benachrichtigungen öffnen/ }).click();
  await page.getByRole("button", { name: "Alle erledigten ausblenden" }).click();
  await expect(page.getByRole("button", { name: /keine neuen Meldungen/ })).toBeVisible();
});

test("5 collapsed projects transfer real layout space to threads", async ({ page }) => {
  const threads = page.locator(".thread-navigation");
  const before = await requiredBox(threads);
  await page.getByRole("button", { name: /Projekte/ }).click();
  await expect(page.getByRole("separator", { name: "Höhe der Projektliste" })).toHaveCount(0);
  await expect.poll(async () => (await requiredBox(threads)).height).toBeGreaterThan(before.height);
});

test("6 collapsed threads transfer real layout space to projects", async ({ page }) => {
  const projects = page.locator(".repository-navigation");
  const before = await requiredBox(projects);
  await page.getByRole("button", { name: /Threads/ }).click();
  await expect(page.getByRole("separator", { name: "Höhe der Projektliste" })).toHaveCount(0);
  await expect.poll(async () => (await requiredBox(projects)).height).toBeGreaterThan(before.height);
});

test("7 sidebar splitter supports keyboard resizing", async ({ page }) => {
  const splitter = page.getByRole("separator", { name: "Höhe der Projektliste" });
  await expect(splitter).toHaveAttribute("aria-valuenow", "280");
  await splitter.press("ArrowDown");
  await expect(splitter).toHaveAttribute("aria-valuenow", "296");
});

test("8 Git status is structured and raw JSON stays collapsed", async ({ page }) => {
  await repositoryAction(page, "Git-Status");
  const dialog = page.getByRole("dialog", { name: "Git-Status" });
  await expect(dialog.getByText("Arbeitsbaum", { exact: true })).toBeVisible();
  await expect(dialog.locator("details.technical-details")).not.toHaveAttribute("open", "");
});

test("9 remote branch selection stays checked and submits its explicit target", async ({ page }) => {
  await repositoryAction(page, "Branches…");
  await page.getByRole("radio", { name: "Zu vorhandenem Branch wechseln" }).click();
  const origin = page.getByRole("radio", { name: /origin\/release/ });
  const upstream = page.getByRole("radio", { name: /upstream\/release/ });
  await origin.click();
  await expect(origin).toBeChecked();
  await expect(upstream).not.toBeChecked();
  await page.getByRole("button", { name: "Branch wechseln" }).click();
  await expect.poll(() => backend.submittedBranchTarget).toBe("refs/remotes/origin/release");
  await expect.poll(() => backend.submittedLocalBranch).toBe("release");
});

test("10 runtime restart confirmation resets after reopen", async ({ page }) => {
  await openSystemStatus(page);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Systemstatus schließen" }).click();
  await openSystemStatus(page);
  await expect(page.getByRole("button", { name: "Codex Runtime neu starten" })).toBeDisabled();
});

test("11 desktop drawer is nonmodal and leaves background operable", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openSystemStatus(page);
  await expect(page.getByRole("dialog", { name: "Systemstatus" })).toHaveCount(0);
  await expect(page.locator(".drawer-scrim")).toHaveCount(0);
  await page.getByRole("button", { name: /Threads/ }).click();
  await expect(page.locator(".system-status-drawer")).toBeVisible();
});

test("12 mobile drawer is a modal bottom sheet with focus trap and inert background", async ({ page }) => {
  await openSystemStatus(page);
  await page.setViewportSize({ width: 390, height: 760 });
  const dialog = page.getByRole("dialog", { name: "Systemstatus" });
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(page.locator(".drawer-scrim")).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveAttribute("inert", "");
  await dialog.getByRole("checkbox").focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Systemstatus schließen" })).toBeFocused();
});

async function openSystemStatus(page: Page): Promise<void> {
  await page.locator(".health-status").click();
  await expect(page.getByText("Codex App Server")).toBeVisible();
}

async function repositoryAction(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "Synthetic Repository Menü" }).click();
  await page.getByRole("menuitem", { name }).click();
}

async function requiredBox(locator: Locator): Promise<{ height: number }> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error("Expected synthetic layout element to have a box");
  return { height: box.height };
}

function job(id: string, status: "failed" | "running"): Record<string, unknown> {
  return {
    id, repositoryId, repositoryName: "Synthetic Repository", type: "git_fetch", status,
    startedAt: 2_000_000_000, endedAt: status === "running" ? null : 2_000_000_001,
    exitCode: status === "running" ? null : 1,
    error: status === "running" ? null : "Synthetic operation failed.",
    errorCode: status === "running" ? null : "native_operation_failed",
    retryable: status !== "running", operationLabel: "Synthetic fetch", durationMs: 1,
  };
}

const repository = {
  id: repositoryId, name: "Synthetic Repository", directoryName: "synthetic-repository",
  status: { branch: "main", detached: false, upstream: "origin/main", upstreamMissing: false,
    ahead: 0, behind: 0, staged: 0, modified: 1, untracked: 0, conflicts: 0,
    clean: false, changes: [{ path: "src/example.ts", staged: false, kind: "modified" }],
    observedAt: 2_000_000_000 },
  preferences: { model: null, effort: null },
  renameCapabilities: { displayName: true, directory: false, remote: false },
};

const thread = {
  id: threadId, repositoryId, repositoryName: "Synthetic Repository", preview: "Synthetic thread",
  name: "Synthetic thread", status: "idle", sandboxMode: "workspace-write",
  approvalPolicy: "on-request", fullAccess: false, createdAt: 2_000_000_000,
  updatedAt: 2_000_000_001, activeTurnId: null, turns: [], usage: null, latestDiff: null,
  activity: { state: "idle", label: "Bereit", last_activity: null, started_at: null,
    observed_at: "2030-01-01T00:00:00Z", elapsed_ms: null, current_tool: null,
    current_command: null, pending_approval_id: null, error_code: null, error_message: null },
};

const rateLimits = { available: false, observedAt: 2_000_000_000 };
const capabilities = {
  models: [{ id: "synthetic", displayName: "Synthetic Model", description: "Synthetic",
    isDefault: true, defaultReasoningEffort: "medium",
    reasoningEfforts: [{ value: "medium", description: "Synthetic" }], inputModalities: ["text"] }],
  skills: [], plugins: [], native: { models: true, skills: true, plugins: true, apps: false },
  status: { overall: "ready", generation: 3, cached: false, stale: false,
    models: { state: "ready", available: true, reason: null },
    skills: { state: "ready", available: true, reason: null },
    plugins: { state: "ready", available: true, reason: null },
    apps: { state: "disabled", available: false, reason: null } },
  host: { sandbox: { workspaceWrite: { available: true, reason: null },
    readOnly: { available: true, reason: null }, fullAccess: { available: true, reason: null } } },
};
const branches = {
  local: [{ id: "refs/heads/main", kind: "local", displayName: "main", fullRef: "refs/heads/main",
    remoteName: null, remoteBranchName: null, proposedLocalName: null,
    current: true, disabledReason: "current_branch" }],
  remote: [
    { id: "refs/remotes/origin/release", kind: "remote", displayName: "origin/release",
      fullRef: "refs/remotes/origin/release", remoteName: "origin", remoteBranchName: "release",
      proposedLocalName: "release", current: false, disabledReason: null },
    { id: "refs/remotes/upstream/release", kind: "remote", displayName: "upstream/release",
      fullRef: "refs/remotes/upstream/release", remoteName: "upstream", remoteBranchName: "release",
      proposedLocalName: "release", current: false, disabledReason: null },
  ],
};
const systemStatus = {
  status: "healthy", workbenchVersion: "0.1.0", codexVersion: "0.146.0", observedAt: 2_000_000_000,
  components: [{ component: "codex_app_server", state: "healthy", observedAt: 2_000_000_000,
    errorCode: null, lastSuccessAt: 2_000_000_000, lastRecoveryAt: null, nextRecoveryAt: null,
    generation: 3, restartAttempts: 0, restartLimit: 5, activeCount: null, queuedCount: null,
    failedCount: null, connectionCount: null, optional: false, enabled: true }],
};
