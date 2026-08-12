import type { Dispatch } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import type { Action } from "./realtime";
import type { Repository, SandboxMode, ThreadView, WorkbenchCapabilities } from "./types";

const mocks = vi.hoisted(() => ({
  listRepositories: vi.fn(),
  listThreads: vi.fn(),
  readCapabilities: vi.fn(),
  readRateLimits: vi.fn(),
  readThread: vi.fn(),
  resumeThread: vi.fn(),
  startThread: vi.fn(),
  prepareFullAccess: vi.fn(),
  dispatch: vi.fn(),
  dispatchAction: null as Dispatch<Action> | null,
  initialThreads: [] as ThreadView[],
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
  resumeThread: mocks.resumeThread,
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

vi.mock("./realtime", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("./realtime")>();
  return {
    ...actual,
    useRealtime: () => {
      const [state, rawDispatch] = React.useReducer(actual.reducer, {
        ...actual.initialRealtimeState,
        threads: mocks.initialThreads,
        connection: "connected" as const,
        processConnection: "connected",
        generation: 1,
      });
      mocks.dispatchAction = rawDispatch;
      const dispatch = React.useCallback((action: Action) => {
        mocks.dispatch(action);
        rawDispatch(action);
      }, []);
      return [state, dispatch] as const;
    },
  };
});

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

function capabilities(
  workspaceWrite = true,
  readOnly = true,
  fullAccess = true,
): WorkbenchCapabilities {
  return {
    models: [{
      id: "model", displayName: "Model", description: "Test", isDefault: true,
      defaultReasoningEffort: "medium", reasoningEfforts: [], inputModalities: ["text"],
    }],
    skills: [], plugins: [],
    native: { models: true, skills: true, plugins: true, apps: false },
    status: {
      overall: "ready", generation: 1, cached: false, stale: false,
      models: { state: "ready", available: true, reason: null },
      skills: { state: "ready", available: true, reason: null },
      plugins: { state: "ready", available: true, reason: null },
      apps: { state: "disabled", available: false, reason: null },
    },
    host: { sandbox: {
      workspaceWrite: { available: workspaceWrite, reason: workspaceWrite ? null : "unavailable" },
      readOnly: { available: readOnly, reason: readOnly ? null : "unavailable" },
      fullAccess: { available: fullAccess, reason: fullAccess ? null : "unavailable" },
    } },
  };
}

function thread(id: string, name: string, mode: SandboxMode = "workspace-write"): ThreadView {
  return {
    id, repositoryId: repository.id, repositoryName: repository.name,
    preview: name, name, status: "idle", sandboxMode: mode,
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
  mocks.initialThreads = [];
  mocks.dispatchAction = null;
  window.history.replaceState({}, "", "/");
  mocks.listRepositories.mockResolvedValue([repository]);
  mocks.listThreads.mockResolvedValue([]);
  mocks.readCapabilities.mockResolvedValue(capabilities());
  mocks.readRateLimits.mockResolvedValue({ available: false, observedAt: 0 });
  mocks.prepareFullAccess.mockResolvedValue({
    confirmationId: "confirmation-thread",
    scopeId: "thread-known",
    expiresAt: "2030-01-01T00:00:00Z",
    repository: repository.name,
    sandboxMode: "danger-full-access",
    approvalPolicy: "on-request",
  });
  mocks.resumeThread.mockImplementation((
    _csrf: string,
    _repositoryId: string,
    threadId: string,
  ) => Promise.resolve(thread(threadId, "Known thread")));
});

test("uses the authoritative startThread response without reading it again", async () => {
  const created = thread("thread-new", "New thread");
  mocks.startThread.mockResolvedValue(created);
  renderWorkbench();

  await screen.findByRole("button", { name: "Access: Workspace" }, { timeout: 5_000 });
  fireEvent.click(screen.getByRole("button", { name: "Neuer Thread" }));

  await waitFor(() => expect(window.location.pathname).toBe(
    `/repositories/${repository.id}/threads/${created.id}`,
  ));
  expect(await screen.findByRole("heading", { name: "New thread" })).toBeInTheDocument();
  expect(mocks.readThread).not.toHaveBeenCalled();
});

test("selects and resumes a known sidebar thread without hydrating it", async () => {
  const known = thread("thread-known", "Known thread");
  mocks.listThreads.mockResolvedValue([known]);
  renderWorkbench();

  fireEvent.click(await screen.findByRole("button", { name: /Known thread/ }));

  await waitFor(() => expect(mocks.resumeThread).toHaveBeenCalledTimes(1));
  expect(mocks.readThread).not.toHaveBeenCalled();
});

test("preserves an existing Full Access thread's on-request policy on a Full-only host", async () => {
  const known = thread("thread-known", "Known Full thread", "danger-full-access");
  mocks.listThreads.mockResolvedValue([known]);
  mocks.readCapabilities.mockResolvedValue(capabilities(false, false, true));
  renderWorkbench();

  fireEvent.click(await screen.findByRole("button", { name: /Known Full thread/ }));
  expect(await screen.findByText("on-request")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Full Access aktivieren" }));

  await waitFor(() => expect(mocks.prepareFullAccess).toHaveBeenCalledWith(
    "csrf", repository.id, "on-request", known.id,
  ));
  await waitFor(() => expect(mocks.resumeThread).toHaveBeenCalledWith(
    "csrf",
    repository.id,
    known.id,
    "danger-full-access",
    "on-request",
    expect.objectContaining({ confirmationId: "confirmation-thread" }),
  ));
  expect(mocks.readThread).not.toHaveBeenCalled();
});

test("waits for the thread list and uses its deep-link target without readThread", async () => {
  const known = thread("thread-deep", "Deep linked thread");
  let resolveThreads: ((threads: ThreadView[]) => void) | null = null;
  mocks.listThreads.mockReturnValue(new Promise<ThreadView[]>((resolve) => {
    resolveThreads = resolve;
  }));
  window.history.replaceState({}, "", `/repositories/${repository.id}/threads/${known.id}`);
  renderWorkbench();

  await waitFor(() => expect(mocks.listThreads).toHaveBeenCalledTimes(1));
  expect(mocks.readThread).not.toHaveBeenCalled();
  act(() => resolveThreads?.([known]));

  expect(await screen.findByRole("heading", { name: "Deep linked thread" })).toBeInTheDocument();
  expect(mocks.readThread).not.toHaveBeenCalled();
});

test("uses one controlled readThread fallback after the thread list misses the target", async () => {
  const fallback = thread("thread-fallback", "History fallback");
  window.history.replaceState({}, "", `/repositories/${repository.id}/threads/${fallback.id}`);
  mocks.readThread.mockResolvedValue(fallback);
  renderWorkbench();

  expect(await screen.findByRole("heading", { name: "History fallback" })).toBeInTheDocument();
  expect(mocks.listThreads).toHaveBeenCalledTimes(1);
  expect(mocks.readThread).toHaveBeenCalledTimes(1);
  expect(mocks.listThreads.mock.invocationCallOrder[0])
    .toBeLessThan(mocks.readThread.mock.invocationCallOrder[0] ?? 0);
});

test("keeps a concurrently known thread usable when fallback hydration fails", async () => {
  const known = thread("thread-race", "Realtime-known thread");
  let rejectRead: ((reason?: unknown) => void) | null = null;
  mocks.readThread.mockReturnValue(new Promise<ThreadView>((_resolve, reject) => {
    rejectRead = reject;
  }));
  window.history.replaceState({}, "", `/repositories/${repository.id}/threads/${known.id}`);
  renderWorkbench();

  await waitFor(() => expect(mocks.readThread).toHaveBeenCalledTimes(1));
  act(() => mocks.dispatchAction?.({ type: "thread", thread: known }));
  expect(await screen.findByRole("heading", { name: "Realtime-known thread" })).toBeInTheDocument();
  await act(async () => {
    rejectRead?.(new Error("thread/read -32600"));
    await Promise.resolve();
  });

  expect(screen.getByLabelText("Nachricht an Codex")).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  act(() => {
    mocks.dispatchAction?.({ type: "socket", state: "reconnecting" });
    mocks.dispatchAction?.({ type: "socket", state: "connected" });
  });
  await waitFor(() => expect(mocks.readThread).toHaveBeenCalledTimes(1));
});

test("restores known thread routes on popstate without readThread", async () => {
  const known = thread("thread-history", "History thread");
  mocks.listThreads.mockResolvedValue([known]);
  renderWorkbench();
  await screen.findByRole("button", { name: /History thread/ });

  act(() => {
    window.history.pushState({}, "", `/repositories/${repository.id}/threads/${known.id}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(await screen.findByRole("heading", { name: "History thread" })).toBeInTheDocument();

  act(() => {
    window.history.pushState({}, "", `/repositories/${repository.id}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await waitFor(() => expect(screen.getByLabelText("Nachricht an Codex")).toBeDisabled());

  act(() => {
    window.history.pushState({}, "", `/repositories/${repository.id}/threads/${known.id}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(await screen.findByRole("heading", { name: "History thread" })).toBeInTheDocument();
  expect(mocks.readThread).not.toHaveBeenCalled();
});

test("refreshes repositories once after a clone job succeeds", async () => {
  const cloned: Repository = {
    ...repository,
    id: "fedcba9876543210",
    name: "recreated",
    directoryName: "recreated",
  };
  mocks.listRepositories
    .mockResolvedValueOnce([repository])
    .mockResolvedValue([repository, cloned]);
  renderWorkbench();

  await waitFor(() => expect(mocks.listRepositories).toHaveBeenCalledTimes(1));
  act(() => mocks.dispatchAction?.({
    type: "jobs",
    jobs: [{
      id: "job-clone", repositoryId: null, repositoryName: "recreated", type: "git_clone",
      status: "succeeded", startedAt: 1, endedAt: 2, exitCode: 0,
      error: null, errorCode: null, retryable: false, operationLabel: "Repository klonen", durationMs: 1,
    }],
  }));

  await waitFor(() => expect(mocks.listRepositories).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole("button", { name: /recreated main Clean/ })).toBeInTheDocument();

  act(() => mocks.dispatchAction?.({
    type: "jobs",
    jobs: [{
      id: "job-clone", repositoryId: null, repositoryName: "recreated", type: "git_clone",
      status: "succeeded", startedAt: 1, endedAt: 2, exitCode: 0,
      error: null, errorCode: null, retryable: false, operationLabel: "Repository klonen", durationMs: 1,
    }],
  }));
  await Promise.resolve();
  expect(mocks.listRepositories).toHaveBeenCalledTimes(2);
});
