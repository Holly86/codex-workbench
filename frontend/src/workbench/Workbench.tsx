import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../auth/api";
import { ActivityPanel } from "../components/ActivityPanel";
import { ApprovalDialog } from "../components/ApprovalDialog";
import { Composer } from "../components/Composer";
import { Conversation } from "../components/Conversation";
import { FullAccessDialog } from "../components/FullAccessDialog";
import { HealthStatus } from "../components/HealthStatus";
import { RepositorySidebar } from "../components/RepositorySidebar";
import type { RepositoryAction } from "../components/RepositorySidebar";
import { RepositoryTools } from "../components/RepositoryTools";
import { UsagePanel } from "../components/UsagePanel";
import {
  decideApproval,
  interruptTurn,
  listRepositories,
  listThreads,
  prepareFullAccess,
  readRateLimits,
  readCapabilities,
  resumeThread,
  startThread,
  startTurn,
  cloneRepository,
  createRepository,
  gitAction,
  githubCapability,
  listJobs,
  publishGithub,
  updateRepository,
} from "./api";
import { resolveSafeSandboxMode, sandboxAvailable } from "./access";
import { useRealtime } from "./realtime";
import type {
  ApprovalPolicy,
  FullAccessCapability,
  Repository,
  SandboxMode,
  ThreadView,
  UploadedImage,
  WorkbenchCapabilities,
  GitHubCapability,
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

const RUNNING_STATES = new Set([
  "starting", "thinking", "planning", "reading", "searching", "executing", "editing",
  "testing", "awaiting_approval", "responding", "reconnecting",
]);

export function Workbench({ session, performLogout, logoutPending, onSessionExpired }: WorkbenchProps) {
  const stableSessionExpired = useCallback(() => onSessionExpired(), [onSessionExpired]);
  const [realtime, dispatch] = useRealtime(session.csrfToken, stableSessionExpired);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selectedRepositoryId, setSelectedRepositoryId] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [sandboxMode, setSandboxMode] = useState<SandboxMode | null>(null);
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>("on-request");
  const [capabilityProjection, setCapabilityProjection] = useState<{
    repositoryId: string;
    value: WorkbenchCapabilities;
  } | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [effort, setEffort] = useState<string | null>(null);
  const [capability, setCapability] = useState<FullAccessCapability | null>(null);
  const [fullAccessIntent, setFullAccessIntent] = useState<FullAccessIntent | null>(null);
  const [fullAccessPending, setFullAccessPending] = useState(false);
  const [repositoriesLoading, setRepositoriesLoading] = useState(true);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [operationPending, setOperationPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [repositoryAction, setRepositoryAction] = useState<RepositoryAction | null>(null);
  const [actionRepository, setActionRepository] = useState<Repository | null>(null);
  const jobs = realtime.jobs;
  const observedCloneJobs = useRef(new Set<string>());
  const [github, setGithub] = useState<GitHubCapability | null>(null);
  const mainRef = useRef<HTMLElement>(null);

  const capabilities =
    realtime.processConnection === "connected" &&
    capabilityProjection?.repositoryId === selectedRepositoryId
      ? capabilityProjection.value
      : null;
  const selectedRepository = repositories.find((item) => item.id === selectedRepositoryId) ?? null;
  const repositoryThreads = realtime.threads.filter((thread) => thread.repositoryId === selectedRepositoryId);
  const selectedThread = realtime.threads.find((thread) => thread.id === selectedThreadId) ?? null;
  const selectedApproval = realtime.approvals.find((approval) => approval.threadId === selectedThreadId) ?? realtime.approvals[0] ?? null;
  const working = selectedThread !== null && RUNNING_STATES.has(selectedThread.activity.state);
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

  const refreshRepositories = useCallback(async () => {
    const values = await listRepositories();
    setRepositories(values);
    setSelectedRepositoryId((current) => current ?? values[0]?.id ?? null);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void listRepositories(controller.signal)
      .then((values) => {
        setRepositories(values);
        if (values.length > 0) setThreadsLoading(true);
        setSelectedRepositoryId((current) => current ?? values[0]?.id ?? null);
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
    if (selectedRepositoryId === null) return;
    const controller = new AbortController();
    void listThreads(selectedRepositoryId, controller.signal)
      .then((threads) => dispatch({ type: "repositoryThreads", repositoryId: selectedRepositoryId, threads }))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) handleError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setThreadsLoading(false);
      });
    return () => controller.abort();
  }, [selectedRepositoryId, handleError, dispatch]);

  useEffect(() => {
    if (selectedRepositoryId === null || realtime.processConnection !== "connected") return;
    let active = true;
    void readCapabilities(selectedRepositoryId).then((value) => {
      if (!active) return;
      setCapabilityProjection({ repositoryId: selectedRepositoryId, value });
      const defaultModel = value.models.find((item) => item.isDefault) ?? value.models[0];
      setModel((current) => value.models.some((item) => item.id === current) ? current : defaultModel?.id ?? null);
      setEffort((current) => current ?? defaultModel?.defaultReasoningEffort ?? null);
      setSandboxMode((current) => resolveSafeSandboxMode(value, current));
      setCapability(null);
      setFullAccessIntent(null);
      setAccessError(null);
    }).catch((caught: unknown) => {
      if (active) handleError(caught);
    });
    return () => { active = false; };
  }, [selectedRepositoryId, realtime.processConnection, handleError]);

  useEffect(() => {
    if (realtime.connection !== "connected") return;
    void readRateLimits().catch(handleError);
  }, [realtime.connection, handleError]);

  useEffect(() => {
    mainRef.current?.focus();
  }, []);

  const selectRepository = (repositoryId: string) => {
    const repository = repositories.find((item) => item.id === repositoryId);
    setThreadsLoading(true);
    setSelectedRepositoryId(repositoryId);
    setSelectedThreadId(null);
    setCapabilityProjection(null);
    setSandboxMode(null);
    setApprovalPolicy("on-request");
    setModel(repository?.preferences.model ?? null);
    setEffort(repository?.preferences.effort ?? null);
    setCapability(null);
    setFullAccessIntent(null);
    setAccessError(null);
    setError(null);
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
      dispatch({ type: "thread", thread: resumed });
      setSelectedThreadId(resumed.id);
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
    setSelectedThreadId(thread.id);
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
      dispatch({ type: "thread", thread });
      setSelectedThreadId(thread.id);
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

  return (
    <>
      <a className="skip-link" href="#conversation">Zum Gespräch springen</a>
      <div className="app-shell">
        <header className="topbar compact-topbar">
          <div className="brand"><span className="brand-mark" aria-hidden="true">⌘</span><h1>Codex Workbench</h1></div>
          <div className="topbar-actions">
            <HealthStatus
              processConnection={realtime.processConnection}
              realtimeConnection={realtime.connection}
              capabilities={capabilities?.status ?? null}
            />
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
            onSelectRepository={selectRepository}
            onSelectThread={selectThread}
            onNewThread={requestNewThread}
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
              if (repository) void githubCapability(repository.id).then(setGithub).catch(handleError);
            }}
          />
          <main className="conversation-column" id="conversation" tabIndex={-1} ref={mainRef}>
            <header className="thread-header">
              <div className="thread-heading">
                <p className="repository-crumb">{selectedRepository?.name ?? "Kein Repository gewählt"}{selectedRepository ? ` · ${selectedRepository.status.detached ? "detached" : selectedRepository.status.branch ?? "no branch"}` : ""}{selectedRepository && !selectedRepository.status.clean ? ` · ${String(selectedRepository.status.staged + selectedRepository.status.modified + selectedRepository.status.untracked)} Änderungen${selectedRepository.status.ahead ? ` · ↑${String(selectedRepository.status.ahead)}` : ""}` : ""}</p>
                <h2>{selectedThread?.name ?? (selectedThread?.preview || "Codex Conversation")}</h2>
              </div>
              <div className="thread-meta"><span>{capabilities?.models.find((item) => item.id === model)?.displayName ?? "Model"}</span><span className={`thread-runtime is-${working ? "working" : "ready"}`}><i aria-hidden="true" />{working ? "Läuft" : "Bereit"}</span></div>
              {selectedThread?.fullAccess === true ? (
                <strong className="full-access-badge">Full Access aktiv</strong>
              ) : sandboxMode === "danger-full-access" ? (
                <strong className="full-access-badge">Full Access ausgewählt</strong>
              ) : null}
            </header>
            {realtime.missedEvents ? <p className="reconnect-notice" role="status">Verpasste Events wurden durch einen vollständigen Snapshot ersetzt.</p> : null}
            {error !== null ? <div className="error-banner" role="alert"><span>{error}</span><button type="button" aria-label="Fehler schließen" onClick={() => setError(null)}>×</button></div> : null}
            <ActivityPanel thread={selectedThread} timeline={selectedThreadId === null ? [] : realtime.timeline[selectedThreadId] ?? []} />
            <Conversation thread={selectedThread} />
            <div className="compact-usage"><UsagePanel thread={selectedThread} rateLimits={realtime.rateLimits} /></div>
            <Composer
              csrfToken={session.csrfToken}
              disabled={selectedThread === null || realtime.connection !== "connected" || operationPending}
              working={working}
              capabilities={capabilities}
              model={model}
              effort={effort}
              sandboxMode={sandboxMode}
              approvalPolicy={approvalPolicy}
              accessMessage={accessError ?? (capabilities === null
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
              onSend={sendMessage}
              onStop={async () => {
                if (selectedThread === null) return;
                try { await interruptTurn(session.csrfToken, selectedThread.id); }
                catch (caught: unknown) { handleError(caught); }
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
      <RepositoryTools action={repositoryAction} repository={actionRepository} jobs={jobs} github={github} pending={operationPending} onClose={() => setRepositoryAction(null)} onSubmit={async (values) => {
        setOperationPending(true); setError(null);
        try {
          if (repositoryAction === "create") await createRepository(session.csrfToken, String(values.name), Boolean(values.readme));
          else if (repositoryAction === "clone") await cloneRepository(session.csrfToken, String(values.name), String(values.url));
          else if (repositoryAction === "rename" && actionRepository) {
            const updated = await updateRepository(session.csrfToken, actionRepository.id, { displayName: String(values.name) });
            setRepositories((current) => current.map((item) => item.id === updated.id ? updated : item));
          } else if (repositoryAction === "commit" && actionRepository) {
            await gitAction(session.csrfToken, actionRepository.id, "add", { files: values.selected as string[] });
            await gitAction(session.csrfToken, actionRepository.id, "commit", { message: String(values.message) });
          } else if (repositoryAction === "branches" && actionRepository) await gitAction(session.csrfToken, actionRepository.id, values.branchOperation === "switch" ? "branch-switch" : "branch-create", { branch: String(values.branch) });
          else if (repositoryAction === "publish" && actionRepository && github?.account) await publishGithub(session.csrfToken, actionRepository.id, { owner: github.account, name: String(values.name), visibility: values.visibility as "private" | "public", initialPush: true, publicConfirmation: values.visibility === "public" ? String(values.confirmation) : null });
          await refreshRepositories();
          dispatch({ type: "jobs", jobs: await listJobs() });
          setRepositoryAction(null);
        } catch (caught) { handleError(caught); } finally { setOperationPending(false); }
      }} />
    </>
  );
}
