import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../auth/api";
import { ActivityPanel } from "../components/ActivityPanel";
import { ApprovalDialog } from "../components/ApprovalDialog";
import { Composer } from "../components/Composer";
import { Conversation } from "../components/Conversation";
import { CopyButton } from "../components/CopyButton";
import { DeployKeyDialog } from "../components/DeployKeyDialog";
import { FullAccessDialog } from "../components/FullAccessDialog";
import { HealthStatus } from "../components/HealthStatus";
import { NotificationCenter } from "../components/NotificationCenter";
import { RepositorySidebar } from "../components/RepositorySidebar";
import type { RepositoryAction } from "../components/RepositorySidebar";
import { RepositoryTools } from "../components/RepositoryTools";
import { ThreadDeleteDialog } from "../components/ThreadDeleteDialog";
import { ThreadTitleDialog } from "../components/ThreadTitleDialog";
import { SystemStatusDrawer } from "../components/SystemStatusDrawer";
import { UsagePanel } from "../components/UsagePanel";
import {
  decideApproval,
  deleteThread,
  interruptTurn,
  listRepositories,
  listThreads,
  prepareFullAccess,
  readRateLimits,
  readCapabilities,
  readThread,
  resumeThread,
  startThread,
  startTurn,
  cloneRepository,
  cloneRepositoryPreflight,
  createRepository,
  gitAction,
  gitBranches,
  githubCapability,
  listJobs,
  publishGithub,
  removeRepository,
  repositoryRemovalPreview,
  updateRepository,
  updateThreadTitle,
} from "./api";
import {
  isFullAccessOnlyHost,
  resolveDefaultSandboxMode,
  resolveSafeSandboxMode,
  sandboxAvailable,
} from "./access";
import { useRealtime } from "./realtime";
import { selectNotifications } from "./notifications";
import { activeRecoveryEpisodes } from "./recoveryEpisodes";
import { parseWorkbenchRoute, repositoryPath, threadPath, writeWorkbenchRoute } from "./routing";
import { readUiPreferences, writeUiPreferences } from "./uiPreferences";
import type {
  ApprovalPolicy,
  FullAccessCapability,
  Repository,
  SandboxMode,
  ThreadView,
  UploadedImage,
  WorkbenchCapabilities,
  GitHubCapability,
  RepositoryRemovalPreview,
} from "./types";

interface WorkbenchProps {
  session: { csrfToken: string; expiresAt: number };
  performLogout: () => Promise<void>;
  logoutPending: boolean;
  onSessionExpired: () => void;
}

interface FullAccessIntent {
  repositoryId: string;
  repositoryName: string;
  threadId: string | null;
  approvalPolicy: ApprovalPolicy;
  after: "selection" | "new" | "resume";
  resumeThread: ThreadView | null;
}

export function Workbench({ session, performLogout, logoutPending, onSessionExpired }: WorkbenchProps) {
  const stableSessionExpired = useCallback(() => onSessionExpired(), [onSessionExpired]);
  const [realtime, dispatch, reconnectRealtime] = useRealtime(session.csrfToken, stableSessionExpired);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selectedRepositoryId, setSelectedRepositoryId] = useState<string | null>(
    () => parseWorkbenchRoute(window.location.pathname).repositoryId,
  );
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(
    () => parseWorkbenchRoute(window.location.pathname).threadId,
  );
  const [sandboxMode, setSandboxMode] = useState<SandboxMode | null>(null);
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>("on-request");
  const [capabilityProjection, setCapabilityProjection] = useState<{
    repositoryId: string;
    value: WorkbenchCapabilities;
  } | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [effort, setEffort] = useState<string | null>(null);
  const [capability, setCapability] = useState<FullAccessCapability | null>(null);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [fullAccessIntent, setFullAccessIntent] = useState<FullAccessIntent | null>(null);
  const [fullAccessPending, setFullAccessPending] = useState(false);
  const [repositoriesLoading, setRepositoriesLoading] = useState(true);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [deepLinkLoading, setDeepLinkLoading] = useState(
    () => parseWorkbenchRoute(window.location.pathname).threadId !== null,
  );
  const [threadListHydratedRepositoryId, setThreadListHydratedRepositoryId] = useState<string | null>(null);
  const [operationPending, setOperationPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [repositoryAction, setRepositoryAction] = useState<RepositoryAction | null>(null);
  const [actionRepository, setActionRepository] = useState<Repository | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ThreadView | null>(null);
  const [titleTarget, setTitleTarget] = useState<ThreadView | null>(null);
  const [titleMode, setTitleMode] = useState<"edit" | "regenerate" | null>(null);
  const jobs = realtime.jobs;
  const observedCloneJobs = useRef(new Set<string>());
  const [github, setGithub] = useState<GitHubCapability | null>(null);
  const [removalPreview, setRemovalPreview] = useState<RepositoryRemovalPreview | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);
  const [uiPreferences, setUiPreferences] = useState(readUiPreferences);
  const notificationsOpen = uiPreferences.notifications.open;
  const mainRef = useRef<HTMLElement>(null);
  const hydrationAttempts = useRef(new Set<string>());
  const knownThreads = useRef(new Map<string, ThreadView>());
  const selectedThreadIdRef = useRef(selectedThreadId);
  const accessDefaultsRepositoryId = useRef<string | null>(null);
  const hydrationTargetRef = useRef({
    loading: deepLinkLoading,
    repositoryId: selectedRepositoryId,
    threadId: selectedThreadId,
  });

  useEffect(() => writeUiPreferences(uiPreferences), [uiPreferences]);

  const closeSystemStatus = useCallback(() => setStatusOpen(false), []);
  const openSystemStatus = useCallback(() => {
    setUiPreferences((current) => ({
      ...current,
      notifications: { ...current.notifications, open: false },
    }));
    setStatusOpen(true);
  }, []);
  const closeNotifications = useCallback(() => setUiPreferences((current) => ({
    ...current,
    notifications: { ...current.notifications, open: false },
  })), []);
  const openNotifications = useCallback(() => {
    setStatusOpen(false);
    setUiPreferences((current) => ({
      ...current,
      notifications: { ...current.notifications, open: true },
    }));
  }, []);

  const capabilities =
    realtime.processConnection === "connected" &&
    capabilityProjection?.repositoryId === selectedRepositoryId
      ? capabilityProjection.value
      : null;
  const deployKeyMode = repositoryAction === "deploy-key"
    ? "repository"
    : repositoryAction === "connect-private" ? "private-clone" : null;
  const selectedRepository = repositories.find((item) => item.id === selectedRepositoryId) ?? null;
  const repositoryThreads = realtime.threads.filter((thread) => thread.repositoryId === selectedRepositoryId);
  const selectedThread = realtime.threads.find((thread) =>
    thread.id === selectedThreadId
    && (selectedRepositoryId === null || thread.repositoryId === selectedRepositoryId)) ?? null;
  const selectedThreadAccessId = selectedThread?.id ?? null;
  const selectedThreadSandboxMode = selectedThread?.sandboxMode ?? null;
  const selectedThreadApprovalPolicy = selectedThread?.approvalPolicy ?? null;
  const selectedApproval = realtime.approvals.find((approval) => approval.threadId === selectedThreadId) ?? realtime.approvals[0] ?? null;
  const working = selectedThread?.activeTurnId !== null && selectedThread?.activeTurnId !== undefined;
  const recoveryEpisodes = activeRecoveryEpisodes(realtime.recoveryEpisodes);
  const notificationSelection = selectNotifications({
    jobs,
    episodes: recoveryEpisodes,
    preferences: uiPreferences.notifications,
  });
  const activeFullAccessIntent =
    capabilities !== null &&
    fullAccessIntent?.repositoryId === selectedRepositoryId &&
    sandboxAvailable(capabilities, "danger-full-access")
      ? fullAccessIntent
      : null;
  const handleError = useCallback((caught: unknown) => {
    if (caught instanceof ApiError && caught.status === 401) {
      onSessionExpired();
      return;
    }
    setError(caught instanceof Error ? caught.message : "Die Aktion ist fehlgeschlagen.");
  }, [onSessionExpired]);

  useEffect(() => {
    selectedThreadIdRef.current = selectedThreadId;
    hydrationTargetRef.current = {
      loading: deepLinkLoading,
      repositoryId: selectedRepositoryId,
      threadId: selectedThreadId,
    };
    for (const thread of realtime.threads) knownThreads.current.set(thread.id, thread);
  }, [deepLinkLoading, selectedRepositoryId, selectedThreadId, realtime.threads]);

  const refreshRepositories = useCallback(async () => {
    const values = await listRepositories();
    setRepositories(values);
    setSelectedRepositoryId((current) => {
      const next = current !== null && values.some((item) => item.id === current)
        ? current : values[0]?.id ?? null;
      if (next !== null && current !== next) writeWorkbenchRoute(repositoryPath(next), true);
      return next;
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void listRepositories(controller.signal)
      .then((values) => {
        setRepositories(values);
        if (values.length > 0) setThreadsLoading(true);
        setSelectedRepositoryId((current) => {
          if (current !== null && (values.some((item) => item.id === current)
            || parseWorkbenchRoute(window.location.pathname).threadId !== null)) return current;
          const next = values[0]?.id ?? null;
          if (next !== null) writeWorkbenchRoute(repositoryPath(next), true);
          return next;
        });
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) handleError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setRepositoriesLoading(false);
      });
    return () => controller.abort();
  }, [handleError]);

  useEffect(() => {
    const navigate = () => {
      const route = parseWorkbenchRoute(window.location.pathname);
      accessDefaultsRepositoryId.current = null;
      setSelectedRepositoryId(route.repositoryId);
      setSelectedThreadId(route.threadId);
      setDeepLinkLoading(route.threadId !== null);
      setThreadListHydratedRepositoryId((current) =>
        current === route.repositoryId ? current : null);
      setError(null);
    };
    window.addEventListener("popstate", navigate);
    return () => window.removeEventListener("popstate", navigate);
  }, []);

  useEffect(() => {
    if (realtime.connection === "connected") return;
    const refresh = () => void listJobs()
      .then((nextJobs) => dispatch({ type: "jobs", jobs: nextJobs }))
      .catch(handleError);
    void refresh();
    const interval = window.setInterval(refresh, 15_000);
    return () => window.clearInterval(interval);
  }, [realtime.connection, handleError, dispatch]);

  useEffect(() => {
    const completed = jobs.filter((job) => job.type === "git_clone" && job.status === "succeeded" && !observedCloneJobs.current.has(job.id));
    if (completed.length > 0) {
      completed.forEach((job) => observedCloneJobs.current.add(job.id));
      void refreshRepositories().catch(handleError);
    }
  }, [jobs, refreshRepositories, handleError]);

  useEffect(() => {
    if (selectedRepositoryId === null || repositoriesLoading) return;
    if (threadListHydratedRepositoryId === selectedRepositoryId) return;
    if (!repositories.some((item) => item.id === selectedRepositoryId)) {
      const timer = window.setTimeout(() => {
        setThreadsLoading(false);
        setThreadListHydratedRepositoryId(selectedRepositoryId);
      }, 0);
      return () => window.clearTimeout(timer);
    }
    const controller = new AbortController();
    void listThreads(selectedRepositoryId, controller.signal)
      .then((threads) => {
        for (const thread of threads) knownThreads.current.set(thread.id, thread);
        dispatch({ type: "repositoryThreads", repositoryId: selectedRepositoryId, threads });
        const target = hydrationTargetRef.current;
        if (target.loading && target.repositoryId === selectedRepositoryId && target.threadId !== null
            && threads.some((thread) =>
              thread.id === target.threadId && thread.repositoryId === selectedRepositoryId)) {
          setDeepLinkLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) handleError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setThreadListHydratedRepositoryId(selectedRepositoryId);
          setThreadsLoading(false);
        }
      });
    return () => controller.abort();
  }, [
    selectedRepositoryId,
    repositories,
    repositoriesLoading,
    threadListHydratedRepositoryId,
    handleError,
    dispatch,
  ]);

  useEffect(() => {
    if (!deepLinkLoading) return;
    if (selectedThreadId === null) {
      const timer = window.setTimeout(() => setDeepLinkLoading(false), 0);
      return () => window.clearTimeout(timer);
    }
    if (selectedThread !== null) {
      knownThreads.current.set(selectedThread.id, selectedThread);
      const timer = window.setTimeout(() => setDeepLinkLoading(false), 0);
      return () => window.clearTimeout(timer);
    }
    if (repositoriesLoading || threadListHydratedRepositoryId !== selectedRepositoryId) return;
    const hydrationKey = `${selectedRepositoryId ?? "history"}:${selectedThreadId}`;
    if (hydrationAttempts.current.has(hydrationKey)) {
      const timer = window.setTimeout(() => setDeepLinkLoading(false), 0);
      return () => window.clearTimeout(timer);
    }
    hydrationAttempts.current.add(hydrationKey);
    let active = true;
    void readThread(selectedThreadId).then((thread) => {
      if (!active) return;
      if (selectedRepositoryId !== null && thread.repositoryId !== selectedRepositoryId) {
        setError("Dieser Thread gehört zu einem anderen Repository.");
        setSelectedThreadId(null);
        writeWorkbenchRoute(repositoryPath(selectedRepositoryId), true);
        return;
      }
      knownThreads.current.set(thread.id, thread);
      dispatch({ type: "thread", thread });
    }).catch((caught: unknown) => {
      const knownThread = knownThreads.current.get(selectedThreadId);
      if (!active) return;
      if (caught instanceof ApiError && caught.code === "thread_unavailable") {
        setError("Der Thread wurde gelöscht oder ist nicht verfügbar.");
      } else if (knownThread?.repositoryId !== selectedRepositoryId) handleError(caught);
    }).finally(() => {
      if (active) setDeepLinkLoading(false);
    });
    return () => { active = false; };
  }, [
    deepLinkLoading,
    selectedThreadId,
    selectedThread,
    selectedRepositoryId,
    repositoriesLoading,
    threadListHydratedRepositoryId,
    dispatch,
    handleError,
  ]);

  useEffect(() => {
    if (selectedRepositoryId === null || selectedRepository === null || realtime.processConnection !== "connected") return;
    let active = true;
    void readCapabilities(selectedRepositoryId).then((value) => {
      if (!active) return;
      const initializeAccessDefaults = accessDefaultsRepositoryId.current !== selectedRepositoryId;
      accessDefaultsRepositoryId.current = selectedRepositoryId;
      setCapabilityProjection({ repositoryId: selectedRepositoryId, value });
      const defaultModel = value.models.find((item) => item.isDefault) ?? value.models[0];
      setModel((current) => value.models.some((item) => item.id === current) ? current : defaultModel?.id ?? null);
      setEffort((current) => current ?? defaultModel?.defaultReasoningEffort ?? null);
      setSandboxMode((current) => resolveDefaultSandboxMode(value, current));
      if (initializeAccessDefaults && selectedThreadIdRef.current === null) {
        setApprovalPolicy(isFullAccessOnlyHost(value) ? "never" : "on-request");
      }
      setCapability(null);
      setFullAccessIntent(null);
      setAccessError(null);
    }).catch((caught: unknown) => {
      if (active) handleError(caught);
    });
    return () => { active = false; };
  }, [
    selectedRepositoryId,
    selectedRepository,
    realtime.processConnection,
    handleError,
  ]);

  useEffect(() => {
    if (selectedThreadAccessId === null || selectedThreadSandboxMode === null
        || selectedThreadApprovalPolicy === null || capabilities === null) return;
    const timer = window.setTimeout(() => {
      setApprovalPolicy(selectedThreadApprovalPolicy);
      setCapability(null);
      if (sandboxAvailable(capabilities, selectedThreadSandboxMode)) {
        setSandboxMode(selectedThreadSandboxMode);
        setAccessError(null);
      } else {
        setSandboxMode(resolveSafeSandboxMode(capabilities, selectedThreadSandboxMode));
        setAccessError("Der Access-Modus dieses Threads ist auf diesem Host nicht verfügbar.");
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    selectedThreadAccessId,
    selectedThreadSandboxMode,
    selectedThreadApprovalPolicy,
    capabilities,
  ]);

  useEffect(() => {
    if (realtime.connection !== "connected") return;
    void readRateLimits().catch(handleError);
  }, [realtime.connection, handleError]);

  useEffect(() => {
    if (selectedThread?.activeTurnId == null || realtime.processConnection !== "connected") return;
    let stopped = false;
    let timer: number | null = null;
    const reconcile = async () => {
      try {
        const thread = await readThread(selectedThread.id);
        if (!stopped) dispatch({ type: "thread", thread });
      } catch {
        // Realtime remains primary; the watchdog retries after a bounded quiet period.
      } finally {
        if (!stopped) timer = window.setTimeout(() => void reconcile(), 30_000);
      }
    };
    timer = window.setTimeout(() => void reconcile(), 30_000);
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [selectedThread?.id, selectedThread?.activeTurnId, realtime.processConnection, dispatch]);

  useEffect(() => {
    mainRef.current?.focus();
  }, []);

  const selectRepository = (repositoryId: string) => {
    const repository = repositories.find((item) => item.id === repositoryId);
    setThreadsLoading(true);
    setSelectedRepositoryId(repositoryId);
    setSelectedThreadId(null);
    setDeepLinkLoading(false);
    setThreadListHydratedRepositoryId(null);
    accessDefaultsRepositoryId.current = null;
    setCapabilityProjection(null);
    setSandboxMode(null);
    setApprovalPolicy("on-request");
    setModel(repository?.preferences.model ?? null);
    setEffort(repository?.preferences.effort ?? null);
    setCapability(null);
    setFullAccessIntent(null);
    setAccessError(null);
    setError(null);
    writeWorkbenchRoute(repositoryPath(repositoryId));
  };

  const performResume = async (thread: ThreadView, suppliedCapability: FullAccessCapability | null) => {
    setOperationPending(true);
    setError(null);
    try {
      const resumed = await resumeThread(
        session.csrfToken,
        thread.repositoryId,
        thread.id,
        thread.sandboxMode,
        thread.approvalPolicy,
        suppliedCapability,
      );
      knownThreads.current.set(resumed.id, resumed);
      dispatch({ type: "thread", thread: resumed });
      setSelectedThreadId(resumed.id);
      setDeepLinkLoading(false);
      setSandboxMode(resumed.sandboxMode);
      setApprovalPolicy(resumed.approvalPolicy);
      setCapability(null);
    } catch (caught: unknown) {
      handleError(caught);
    } finally {
      setOperationPending(false);
    }
  };

  const selectThread = (thread: ThreadView) => {
    knownThreads.current.set(thread.id, thread);
    writeWorkbenchRoute(threadPath(thread.repositoryId, thread.id));
    setSelectedThreadId(thread.id);
    setDeepLinkLoading(false);
    setApprovalPolicy(thread.approvalPolicy);
    setCapability(null);
    if (capabilities === null || !sandboxAvailable(capabilities, thread.sandboxMode)) {
      setSandboxMode(capabilities === null ? null : resolveSafeSandboxMode(capabilities, thread.sandboxMode));
      setAccessError(capabilities === null
        ? "Access-Optionen werden noch geladen. Bitte kurz warten."
        : "Der Access-Modus dieses Threads ist auf diesem Host nicht verfügbar.");
      return;
    }
    setSandboxMode(thread.sandboxMode);
    setAccessError(null);
    if (thread.sandboxMode === "danger-full-access") {
      setFullAccessIntent({
        repositoryId: thread.repositoryId,
        repositoryName: thread.repositoryName,
        threadId: thread.id,
        approvalPolicy: thread.approvalPolicy,
        after: "resume",
        resumeThread: thread,
      });
    } else {
      void performResume(thread, null);
    }
  };

  const performNewThread = async (
    repositoryId: string,
    mode: SandboxMode,
    policy: ApprovalPolicy,
    suppliedCapability: FullAccessCapability | null,
  ) => {
    setOperationPending(true);
    setError(null);
    try {
      const thread = await startThread(
        session.csrfToken,
        repositoryId,
        mode,
        policy,
        suppliedCapability,
      );
      knownThreads.current.set(thread.id, thread);
      setDeepLinkLoading(false);
      dispatch({ type: "thread", thread });
      setSelectedThreadId(thread.id);
      writeWorkbenchRoute(threadPath(thread.repositoryId, thread.id));
      setCapability(null);
    } catch (caught: unknown) {
      handleError(caught);
    } finally {
      setOperationPending(false);
    }
  };

  const requestNewThread = () => {
    if (selectedRepository === null) return;
    if (capabilities === null) {
      setAccessError("Access-Optionen werden noch geladen. Bitte kurz warten.");
      return;
    }
    if (sandboxMode === null || !sandboxAvailable(capabilities, sandboxMode)) {
      setSandboxMode(resolveSafeSandboxMode(capabilities, sandboxMode));
      setAccessError("Wähle einen auf diesem Host verfügbaren Access-Modus.");
      return;
    }
    setAccessError(null);
    if (sandboxMode === "danger-full-access") {
      if (capability !== null) {
        void performNewThread(selectedRepository.id, sandboxMode, approvalPolicy, capability);
        return;
      }
      setFullAccessIntent({
        repositoryId: selectedRepository.id,
        repositoryName: selectedRepository.name,
        threadId: null,
        approvalPolicy,
        after: "new",
        resumeThread: null,
      });
    } else {
      void performNewThread(selectedRepository.id, sandboxMode, approvalPolicy, null);
    }
  };

  const requestFullAccessSelection = (policy: ApprovalPolicy) => {
    if (selectedRepository === null) return;
    if (!sandboxAvailable(capabilities, "danger-full-access")) {
      setAccessError("Full Access ist auf diesem Host nicht verfügbar.");
      return;
    }
    setFullAccessIntent({
      repositoryId: selectedRepository.id,
      repositoryName: selectedRepository.name,
      threadId: selectedThread?.id ?? null,
      approvalPolicy: policy,
      after: "selection",
      resumeThread: null,
    });
  };

  const confirmFullAccess = async () => {
    if (activeFullAccessIntent === null) return;
    const intent = activeFullAccessIntent;
    setFullAccessPending(true);
    setError(null);
    try {
      const nextCapability = await prepareFullAccess(
        session.csrfToken,
        intent.repositoryId,
        intent.approvalPolicy,
        intent.threadId,
      );
      setSandboxMode("danger-full-access");
      setApprovalPolicy(intent.approvalPolicy);
      setCapability(nextCapability);
      setAccessError(null);
      setFullAccessIntent(null);
      if (intent.after === "new") {
        await performNewThread(
          intent.repositoryId,
          "danger-full-access",
          intent.approvalPolicy,
          nextCapability,
        );
      }
      if (intent.after === "resume" && intent.resumeThread !== null) {
        await performResume(intent.resumeThread, nextCapability);
      }
    } catch (caught: unknown) {
      handleError(caught);
    } finally {
      setFullAccessPending(false);
    }
  };

  const sendMessage = async (message: string, uploads: UploadedImage[]): Promise<boolean> => {
    if (
      selectedThread === null ||
      sandboxMode === null ||
      !sandboxAvailable(capabilities, sandboxMode)
    ) {
      setAccessError("Wähle einen auf diesem Host verfügbaren Access-Modus.");
      return false;
    }
    setOperationPending(true);
    setError(null);
    try {
      await startTurn(
        session.csrfToken,
        selectedThread.id,
        message,
        uploads.map((upload) => ({ uploadId: upload.id, detail: "auto" })),
        sandboxMode,
        approvalPolicy,
        capability?.confirmationId ?? null,
        model,
        effort,
      );
      setCapability(null);
      return true;
    } catch (caught: unknown) {
      handleError(caught);
      return false;
    } finally {
      setOperationPending(false);
    }
  };

  const loadActionBranches = useCallback(() => {
    if (actionRepository === null) return Promise.reject(new Error("Kein Repository gewählt."));
    return gitBranches(actionRepository.id);
  }, [actionRepository]);

  const retryNotification = useCallback(async (job: typeof jobs[number]) => {
    if (!job.retryable || job.repositoryId === null) return;
    try {
      const action = job.type === "git_fetch" ? "fetch" : "pull";
      await gitAction(session.csrfToken, job.repositoryId, action);
      dispatch({ type: "jobs", jobs: await listJobs() });
    } catch (caught: unknown) { handleError(caught); }
  }, [dispatch, handleError, session.csrfToken]);

  return (
    <>
      <a className="skip-link" href="#conversation">Zum Gespräch springen</a>
      <div className="app-shell">
        <header className="topbar compact-topbar">
          <div className="brand">
            <img className="brand-wordmark" src="/brand/codex-workbench-wordmark.png" alt="" />
            <h1 className="sr-only">Codex Workbench</h1>
          </div>
          <div className="topbar-actions">
            <HealthStatus
              processConnection={realtime.processConnection}
              realtimeConnection={realtime.connection}
              onOpen={openSystemStatus}
            />
            <UsagePanel thread={selectedThread} rateLimits={realtime.rateLimits} />
            <button type="button" className="notification-trigger status-chip" aria-label={notificationSelection.badgeCount > 0 ? `Benachrichtigungen öffnen, ${String(notificationSelection.badgeCount)} neu` : "Benachrichtigungen öffnen, keine neuen Meldungen"} onClick={openNotifications}><span aria-hidden="true">◌</span><span>Aktivität</span>{notificationSelection.badgeCount > 0 ? <strong>{notificationSelection.badgeCount > 99 ? "99+" : notificationSelection.badgeCount}</strong> : null}</button>
            <button type="button" className="secondary-action" disabled={logoutPending} onClick={() => void performLogout()}>{logoutPending ? "Abmelden …" : "Abmelden"}</button>
          </div>
        </header>
        <div className="workspace-layout">
          <RepositorySidebar
            repositories={repositories}
            selectedRepositoryId={selectedRepositoryId}
            threads={repositoryThreads}
            selectedThreadId={selectedThreadId}
            loading={repositoriesLoading || threadsLoading}
            preferences={uiPreferences}
            onPreferencesChange={setUiPreferences}
            onSelectRepository={selectRepository}
            onSelectThread={selectThread}
            onNewThread={requestNewThread}
            onDeleteThread={setDeleteTarget}
            onEditThread={(thread) => { setTitleTarget(thread); setTitleMode("edit"); }}
            onRegenerateThread={(thread) => { setTitleTarget(thread); setTitleMode("regenerate"); }}
            onRepositoryAction={(action, repository) => {
              if (action === "open" && repository) { selectRepository(repository.id); return; }
              if (action === "new-thread" && repository) {
                if (repository.id !== selectedRepositoryId) {
                  selectRepository(repository.id);
                  setAccessError("Access-Optionen werden geladen. Starte den Thread anschließend erneut.");
                } else requestNewThread();
                return;
              }
              if (["fetch", "pull", "push"].includes(action) && repository) {
                void gitAction(session.csrfToken, repository.id, action)
                  .then(() => listJobs())
                  .then((nextJobs) => dispatch({ type: "jobs", jobs: nextJobs }))
                  .catch(handleError); return;
              }
              if (action === "github" && repository) {
                void githubCapability(repository.id).then((value) => { if (value.webUrl) window.open(value.webUrl, "_blank", "noopener,noreferrer"); }).catch(handleError); return;
              }
              setActionRepository(repository); setRepositoryAction(action);
              setRemovalPreview(null);
              if (repository && (action === "remove" || action === "delete")) {
                void repositoryRemovalPreview(repository.id).then(setRemovalPreview).catch(handleError);
              } else if (repository && action !== "deploy-key") void githubCapability(repository.id).then(setGithub).catch(handleError);
            }}
          />
          <main className="conversation-column" id="conversation" tabIndex={-1} ref={mainRef}>
            <header className="thread-header">
              <div className="thread-heading">
                <p className="repository-crumb">{selectedRepository?.name ?? selectedThread?.repositoryName ?? "Kein Repository gewählt"}{selectedThread?.repositoryAvailable === false ? " · nicht verfügbar" : ""}{selectedRepository ? ` · ${selectedRepository.status.detached ? "detached" : selectedRepository.status.branch ?? "no branch"}` : ""}{selectedRepository && !selectedRepository.status.clean ? ` · ${String(selectedRepository.status.staged + selectedRepository.status.modified + selectedRepository.status.untracked)} Änderungen${selectedRepository.status.ahead ? ` · ↑${String(selectedRepository.status.ahead)}` : ""}` : ""}</p>
                <h2>{selectedThread?.name ?? (selectedThread?.preview || "Codex Conversation")}</h2>
              </div>
              <div className="thread-meta"><span>{capabilities?.models.find((item) => item.id === model)?.displayName ?? "Model"}</span><span className={`thread-runtime is-${working ? "working" : "ready"}`}><i aria-hidden="true" />{working ? "Läuft" : "Bereit"}</span>{selectedThread ? <CopyButton text={`${window.location.origin}${threadPath(selectedThread.repositoryId, selectedThread.id)}`} label="Thread-Link kopieren" /> : null}</div>
              {selectedThread?.fullAccess === true ? (
                <strong className="full-access-badge">Full Access aktiv</strong>
              ) : sandboxMode === "danger-full-access" ? (
                <strong className="full-access-badge">Full Access ausgewählt</strong>
              ) : null}
            </header>
            {realtime.missedEvents ? <p className="reconnect-notice" role="status">Verpasste Events wurden durch einen vollständigen Snapshot ersetzt.</p> : null}
            {error !== null ? <div className="error-banner" role="alert"><span>{error}</span><button type="button" aria-label="Fehler schließen" onClick={() => setError(null)}>×</button></div> : null}
            <ActivityPanel thread={selectedThread} timeline={selectedThreadId === null ? [] : realtime.timeline[selectedThreadId] ?? []} runtimeState={realtime.processConnection} />
            <Conversation thread={selectedThread} loading={deepLinkLoading} />
            <Composer
              csrfToken={session.csrfToken}
              disabled={selectedThread === null || selectedThread.repositoryAvailable === false || realtime.connection !== "connected" || operationPending}
              working={working}
              capabilities={capabilities}
              model={model}
              effort={effort}
              sandboxMode={sandboxMode}
              approvalPolicy={approvalPolicy}
              accessMessage={selectedThread?.repositoryAvailable === false ? "Dieses Repository ist in der Workbench nicht mehr verfügbar. Der Verlauf bleibt lesbar." : accessError ?? (capabilities === null
                ? "Access-Optionen werden geladen …"
                : sandboxMode === null
                  ? "Access-Auswahl erforderlich. Full Access muss ausdrücklich bestätigt werden."
                  : null)}
              onModelChange={(nextModel, nextEffort) => {
                setModel(nextModel); setEffort(nextEffort);
                if (selectedRepositoryId) void updateRepository(session.csrfToken, selectedRepositoryId, { model: nextModel, effort: nextEffort }).then((updated) => setRepositories((current) => current.map((item) => item.id === updated.id ? updated : item))).catch(handleError);
              }}
              onAccessChange={(nextSandbox, nextPolicy) => {
                if (nextSandbox === "danger-full-access") requestFullAccessSelection(nextPolicy);
                else if (sandboxAvailable(capabilities, nextSandbox)) {
                  setSandboxMode(nextSandbox);
                  setApprovalPolicy(nextPolicy);
                  setCapability(null);
                  setFullAccessIntent(null);
                  setAccessError(null);
                } else setAccessError("Dieser Access-Modus ist auf dem Host nicht verfügbar.");
              }}
              toolsLoading={toolsLoading}
              onToolsOpen={() => {
                if (
                  selectedRepositoryId === null
                  || capabilities === null
                  || capabilities.status.skills.state === "ready"
                  || toolsLoading
                ) return;
                setToolsLoading(true);
                void readCapabilities(selectedRepositoryId, "skills")
                  .then((value) => setCapabilityProjection({
                    repositoryId: selectedRepositoryId,
                    value,
                  }))
                  .catch(handleError)
                  .finally(() => setToolsLoading(false));
              }}
              onSend={sendMessage}
              onStop={async () => {
                if (selectedThread?.activeTurnId == null) return;
                try { await interruptTurn(session.csrfToken, selectedThread.id); }
                catch (caught: unknown) {
                  if (caught instanceof ApiError && caught.status === 409) {
                    void readThread(selectedThread.id).then((thread) => dispatch({ type: "thread", thread })).catch(handleError);
                  } else handleError(caught);
                }
              }}
            />
          </main>
        </div>
      </div>
      <FullAccessDialog
        open={activeFullAccessIntent !== null}
        repository={activeFullAccessIntent?.repositoryName ?? ""}
        approvalPolicy={activeFullAccessIntent?.approvalPolicy ?? approvalPolicy}
        pending={fullAccessPending}
        onConfirm={() => void confirmFullAccess()}
        onCancel={() => setFullAccessIntent(null)}
      />
      <ApprovalDialog
        approval={selectedApproval}
        onDecision={async (decision) => {
          if (selectedApproval === null) return;
          try { await decideApproval(session.csrfToken, selectedApproval.id, decision); }
          catch (caught: unknown) { handleError(caught); }
        }}
      />
      <ThreadDeleteDialog
        thread={deleteTarget}
        pending={operationPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget === null) return;
          setOperationPending(true);
          setError(null);
          void deleteThread(session.csrfToken, deleteTarget.id, deleteTarget.repositoryId)
            .then(() => {
              dispatch({ type: "threadDeleted", threadId: deleteTarget.id });
              knownThreads.current.delete(deleteTarget.id);
              hydrationAttempts.current.delete(`${deleteTarget.repositoryId}:${deleteTarget.id}`);
              if (selectedThreadId === deleteTarget.id) {
                setSelectedThreadId(null);
                setDeepLinkLoading(false);
                writeWorkbenchRoute(repositoryPath(deleteTarget.repositoryId), true);
              }
              setDeleteTarget(null);
            })
            .catch(handleError)
            .finally(() => setOperationPending(false));
        }}
      />
      <ThreadTitleDialog
        thread={titleTarget}
        mode={titleMode}
        pending={operationPending}
        onClose={() => { setTitleTarget(null); setTitleMode(null); }}
        onSave={async (title) => {
          if (titleTarget === null) return;
          setOperationPending(true);
          try {
            const thread = await updateThreadTitle(session.csrfToken, titleTarget.id, title);
            dispatch({ type: "thread", thread });
            knownThreads.current.set(thread.id, thread);
            setTitleTarget(null); setTitleMode(null);
          } catch (caught: unknown) { handleError(caught); }
          finally { setOperationPending(false); }
        }}
      />
      <DeployKeyDialog
        mode={deployKeyMode}
        repository={actionRepository}
        repositoryNames={repositories.map((item) => item.directoryName)}
        csrfToken={session.csrfToken}
        onClose={() => setRepositoryAction(null)}
        onCloneStarted={async () => {
          await refreshRepositories();
          dispatch({ type: "jobs", jobs: await listJobs() });
        }}
      />
      <SystemStatusDrawer
        open={statusOpen}
        csrfToken={session.csrfToken}
        realtimeState={realtime.connection}
        onClose={closeSystemStatus}
        onReconnect={reconnectRealtime}
        onReloadRepositories={refreshRepositories}
      />
      <NotificationCenter
        open={notificationsOpen}
        jobs={jobs}
        recoveryEpisodes={recoveryEpisodes}
        preferences={uiPreferences}
        onPreferencesChange={setUiPreferences}
        onClose={closeNotifications}
        onReconnectRealtime={reconnectRealtime}
        onOpenSystemStatus={openSystemStatus}
        onRetry={retryNotification}
      />
      <RepositoryTools action={deployKeyMode === null ? repositoryAction : null} repository={actionRepository} github={github} pending={operationPending} removalPreview={removalPreview} onClose={() => setRepositoryAction(null)} onLoadBranches={actionRepository ? loadActionBranches : undefined} onSubmit={async (values) => {
        setOperationPending(true); setError(null);
        try {
          if (typeof values.quickAction === "string" && actionRepository) {
            if (values.quickAction === "changes" || values.quickAction === "commit") {
              setRepositoryAction(values.quickAction);
              return;
            }
            if (["fetch", "pull", "push"].includes(values.quickAction)) {
              await gitAction(session.csrfToken, actionRepository.id, values.quickAction);
              dispatch({ type: "jobs", jobs: await listJobs() });
              return;
            }
          }
          if (repositoryAction === "create") await createRepository(session.csrfToken, String(values.name), Boolean(values.readme));
          else if (repositoryAction === "clone") {
            const preflight = await cloneRepositoryPreflight(session.csrfToken, String(values.url));
            if (!preflight.allowed) throw new Error(preflight.message);
            await cloneRepository(session.csrfToken, String(values.name), String(values.url));
          }
          else if (repositoryAction === "rename" && actionRepository) {
            const updated = await updateRepository(session.csrfToken, actionRepository.id, { displayName: String(values.name) });
            setRepositories((current) => current.map((item) => item.id === updated.id ? updated : item));
          } else if (repositoryAction === "commit" && actionRepository) {
            await gitAction(session.csrfToken, actionRepository.id, "add", { files: values.selected as string[] });
            await gitAction(session.csrfToken, actionRepository.id, "commit", { message: String(values.message) });
          } else if (repositoryAction === "branches" && actionRepository) await gitAction(session.csrfToken, actionRepository.id, values.branchOperation === "switch" ? "branch-switch" : "branch-create", values.branchOperation === "switch" ? { branchTargetId: String(values.branchTargetId), localBranchName: String(values.localBranchName) } : { branch: String(values.branch) });
          else if (repositoryAction === "publish" && actionRepository && github?.account) await publishGithub(session.csrfToken, actionRepository.id, { owner: github.account, name: String(values.name), visibility: values.visibility as "private" | "public", initialPush: true, publicConfirmation: values.visibility === "public" ? String(values.confirmation) : null });
          else if ((repositoryAction === "remove" || repositoryAction === "delete") && actionRepository) {
            await removeRepository(session.csrfToken, actionRepository.id, repositoryAction, String(values.confirmation), Boolean(values.deleteThreads));
            setSelectedThreadId(null);
            setSelectedRepositoryId(null);
          }
          await refreshRepositories();
          dispatch({ type: "jobs", jobs: await listJobs() });
          setRepositoryAction(null);
        } catch (caught) { handleError(caught); } finally { setOperationPending(false); }
      }} />
    </>
  );
}
