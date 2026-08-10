import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import type {
  FullAccessCapability,
  Repository,
  SandboxMode,
  ThreadView,
  WorkbenchCapabilities,
} from "./types";

const mocks = vi.hoisted(() => ({
  listRepositories: vi.fn(),
  listThreads: vi.fn(),
  readCapabilities: vi.fn(),
  readRateLimits: vi.fn(),
  startThread: vi.fn(),
  prepareFullAccess: vi.fn(),
  readThread: vi.fn(),
  dispatch: vi.fn(),
}));

vi.mock("./api", () => ({
  decideApproval: vi.fn(),
  interruptTurn: vi.fn(),
  listRepositories: mocks.listRepositories,
  listThreads: mocks.listThreads,
  prepareFullAccess: mocks.prepareFullAccess,
  readRateLimits: mocks.readRateLimits,
  readCapabilities: mocks.readCapabilities,
  readThread: mocks.readThread,
  resumeThread: vi.fn(),
  startThread: mocks.startThread,
  startTurn: vi.fn(),
  cloneRepository: vi.fn(),
  cloneRepositoryPreflight: vi.fn(),
  createRepository: vi.fn(),
  gitAction: vi.fn(),
  githubCapability: vi.fn(),
  listJobs: vi.fn(),
  publishGithub: vi.fn(),
  removeRepository: vi.fn(),
  repositoryRemovalPreview: vi.fn(),
  updateRepository: vi.fn(),
}));

vi.mock("./realtime", () => ({
  useRealtime: () => [{
    threads: [],
    approvals: [],
    rateLimits: { available: false, observedAt: 0 },
    connection: "connected",
    processConnection: "connected",
    generation: 1,
    lastSequence: 0,
    missedEvents: false,
    timeline: {},
    jobs: [],
  }, mocks.dispatch],
}));

import { Workbench } from "./Workbench";

const repository: Repository = {
  id: "0123456789abcdef",
  name: "workbench",
  directoryName: "workbench",
  status: {
    branch: "main", detached: false, upstream: null, upstreamMissing: true,
    ahead: 0, behind: 0, staged: 0, modified: 0, untracked: 0,
    conflicts: 0, clean: true, changes: [], observedAt: 1,
  },
  preferences: { model: null, effort: null },
  renameCapabilities: { displayName: true, directory: false, remote: false },
};

function capabilities(workspaceWrite: boolean, readOnly: boolean, fullAccess: boolean): WorkbenchCapabilities {
  return {
    models: [{
      id: "model", displayName: "Model", description: "Test", isDefault: true,
      defaultReasoningEffort: "medium", reasoningEfforts: [{ value: "medium", description: "Test" }],
      inputModalities: ["text"],
    }],
    skills: [],
    plugins: [],
    native: { models: true, skills: true, plugins: true, apps: false },
    status: {
      overall: "ready", generation: 1, cached: false, stale: false,
      models: { available: true, reason: null },
      skills: { available: true, reason: null },
      plugins: { available: true, reason: null },
    },
    host: { sandbox: {
      workspaceWrite: { available: workspaceWrite, reason: workspaceWrite ? null : "nicht verfügbar" },
      readOnly: { available: readOnly, reason: readOnly ? null : "nicht verfügbar" },
      fullAccess: { available: fullAccess, reason: fullAccess ? null : "nicht verfügbar" },
    } },
  };
}

function thread(mode: SandboxMode): ThreadView {
  return {
    id: "thread-1", repositoryId: repository.id, repositoryName: repository.name,
    preview: "", name: null, status: "idle", sandboxMode: mode,
    approvalPolicy: "on-request", fullAccess: mode === "danger-full-access",
    createdAt: 1, updatedAt: 1, activeTurnId: null, turns: [], usage: null,
    activity: {
      state: "idle", label: "Bereit", last_activity: null, started_at: null,
      observed_at: "2030-01-01T00:00:00Z", elapsed_ms: null, current_tool: null,
      current_command: null, pending_approval_id: null, error_code: null, error_message: null,
    },
    latestDiff: null,
  };
}

const fullAccessCapability: FullAccessCapability = {
  confirmationId: "confirmation-1",
  scopeId: "scope-1",
  expiresAt: "2030-01-01T00:00:00Z",
  repository: "workbench",
  sandboxMode: "danger-full-access",
  approvalPolicy: "on-request",
};

function renderWorkbench(): void {
  render(<Workbench
    session={{ csrfToken: "csrf", expiresAt: 1 }}
    performLogout={() => Promise.resolve()}
    logoutPending={false}
    onSessionExpired={() => undefined}
  />);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  mocks.listRepositories.mockResolvedValue([repository]);
  mocks.listThreads.mockResolvedValue([]);
  mocks.readRateLimits.mockResolvedValue({ available: false, observedAt: 0 });
  mocks.startThread.mockImplementation((
    _csrf: string,
    _repositoryId: string,
    mode: SandboxMode,
  ) => Promise.resolve(thread(mode)));
  mocks.prepareFullAccess.mockImplementation((
    _csrf: string,
    _repositoryId: string,
    policy: "untrusted" | "on-request" | "never",
  ) => Promise.resolve({ ...fullAccessCapability, approvalPolicy: policy }));
  mocks.readThread.mockResolvedValue(thread("workspace-write"));
});

test("does not POST New Thread while host capabilities are loading", async () => {
  mocks.readCapabilities.mockReturnValue(new Promise(() => undefined));
  renderWorkbench();

  const newThread = await screen.findByRole("button", { name: "Neuer Thread" });
  await waitFor(() => expect(newThread).toBeEnabled());
  fireEvent.click(newThread);

  expect(mocks.startThread).not.toHaveBeenCalled();
  expect(await screen.findByText("Access-Optionen werden noch geladen. Bitte kurz warten.")).toBeInTheDocument();
});

test("defaults a Full-only host to Full Access and Never before confirmation", async () => {
  mocks.readCapabilities.mockResolvedValue(capabilities(false, false, true));
  renderWorkbench();

  await screen.findByRole("button", { name: "Access: Full" });
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  expect(await screen.findByText("never")).toBeInTheDocument();
  expect(mocks.startThread).not.toHaveBeenCalled();
  expect(mocks.prepareFullAccess).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Full Access aktivieren" }));

  await waitFor(() => expect(mocks.prepareFullAccess).toHaveBeenCalledWith(
    "csrf", repository.id, "never", null,
  ));
  await waitFor(() => expect(mocks.startThread).toHaveBeenCalledWith(
    "csrf",
    repository.id,
    "danger-full-access",
    "never",
    { ...fullAccessCapability, approvalPolicy: "never" },
  ));
});

test("uses Workspace without Full Access preparation when the host supports it", async () => {
  mocks.readCapabilities.mockResolvedValue(capabilities(true, true, true));
  renderWorkbench();

  await screen.findByRole("button", { name: "Access: Workspace" });
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  await waitFor(() => expect(mocks.startThread).toHaveBeenCalledWith(
    "csrf", repository.id, "workspace-write", "on-request", null,
  ));
  expect(mocks.prepareFullAccess).not.toHaveBeenCalled();
});

test("uses Read only instead of Full Access when Read only is available", async () => {
  mocks.readCapabilities.mockResolvedValue(capabilities(false, true, true));
  renderWorkbench();

  await screen.findByRole("button", { name: "Access: Read only" });
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  await waitFor(() => expect(mocks.startThread).toHaveBeenCalledWith(
    "csrf", repository.id, "read-only", "on-request", null,
  ));
  expect(mocks.prepareFullAccess).not.toHaveBeenCalled();
});

test("creates Full Access only after explicit selection and confirmation", async () => {
  mocks.readCapabilities.mockResolvedValue(capabilities(true, true, true));
  renderWorkbench();

  fireEvent.click(await screen.findByRole("button", { name: "Access: Workspace" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Full access Uneingeschränkter/ }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Full Access aktivieren" }));

  await waitFor(() => expect(mocks.prepareFullAccess).toHaveBeenCalledTimes(1));
  expect(mocks.startThread).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));
  await waitFor(() => expect(mocks.startThread).toHaveBeenCalledWith(
    "csrf", repository.id, "danger-full-access", "on-request", fullAccessCapability,
  ));
});

test("clears the Full Access capability when switching back to Workspace", async () => {
  mocks.readCapabilities.mockResolvedValue(capabilities(true, true, true));
  renderWorkbench();

  fireEvent.click(await screen.findByRole("button", { name: "Access: Workspace" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Full access Uneingeschränkter/ }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Full Access aktivieren" }));
  await screen.findByRole("button", { name: "Access: Full" });

  fireEvent.click(screen.getByRole("button", { name: "Access: Full" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Workspace Änderungen/ }));
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  await waitFor(() => expect(mocks.startThread).toHaveBeenCalledWith(
    "csrf", repository.id, "workspace-write", "on-request", null,
  ));
});

test("clears a Full Access capability when switching repositories", async () => {
  const secondRepository: Repository = {
    ...repository,
    id: "fedcba9876543210",
    name: "second-repository",
    directoryName: "second-repository",
  };
  mocks.listRepositories.mockResolvedValue([repository, secondRepository]);
  mocks.readCapabilities.mockResolvedValue(capabilities(true, true, true));
  renderWorkbench();

  fireEvent.click(await screen.findByRole("button", { name: "Access: Workspace" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Full access Uneingeschränkter/ }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Full Access aktivieren" }));
  await screen.findByRole("button", { name: "Access: Full" });

  fireEvent.click(screen.getByRole("button", { name: /second-repository main Clean/ }));
  await screen.findByRole("button", { name: "Access: Workspace" });
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  await waitFor(() => expect(mocks.startThread).toHaveBeenCalledWith(
    "csrf", secondRepository.id, "workspace-write", "on-request", null,
  ));
});

test("applies Full Access and Never when a repository switch reveals a Full-only host", async () => {
  const secondRepository: Repository = {
    ...repository,
    id: "fedcba9876543210",
    name: "second-repository",
    directoryName: "second-repository",
  };
  mocks.listRepositories.mockResolvedValue([repository, secondRepository]);
  mocks.readCapabilities.mockImplementation((repositoryId: string) => Promise.resolve(
    repositoryId === secondRepository.id
      ? capabilities(false, false, true)
      : capabilities(true, true, true),
  ));
  renderWorkbench();

  await screen.findByRole("button", { name: "Access: Workspace" });
  fireEvent.click(screen.getByRole("button", { name: /second-repository main Clean/ }));
  await screen.findByRole("button", { name: "Access: Full" });
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  expect(await screen.findByText("never")).toBeInTheDocument();
  expect(mocks.startThread).not.toHaveBeenCalled();
});
