import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../auth/api";
import { ActivityPanel } from "../components/ActivityPanel";
import { ApprovalDialog } from "../components/ApprovalDialog";
import { Composer } from "../components/Composer";
import { Conversation } from "../components/Conversation";
import { FullAccessDialog } from "../components/FullAccessDialog";
import { HealthStatus } from "../components/HealthStatus";
import { RepositorySidebar } from "../components/RepositorySidebar";
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
} from "./api";
import { useRealtime } from "./realtime";
import type {
  ApprovalPolicy,
  FullAccessCapability,
  Repository,
  SandboxMode,
  ThreadView,
  UploadedImage,
  WorkbenchCapabilities,
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
  const [sandboxMode, setSandboxMode] = useState<SandboxMode>("workspace-write");
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>("on-request");
  const [capabilities, setCapabilities] = useState<WorkbenchCapabilities | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [effort, setEffort] = useState<string | null>(null);
  const [capability, setCapability] = useState<FullAccessCapability | null>(null);
  const [fullAccessIntent, setFullAccessIntent] = useState<FullAccessIntent | null>(null);
  const [fullAccessPending, setFullAccessPending] = useState(false);
  const [repositoriesLoading, setRepositoriesLoading] = useState(true);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [operationPending, setOperationPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mainRef = useRef<HTMLElement>(null);

  const selectedRepository = repositories.find((item) => item.id === selectedRepositoryId) ?? null;
  const repositoryThreads = realtime.threads.filter((thread) => thread.repositoryId === selectedRepositoryId);
  const selectedThread = realtime.threads.find((thread) => thread.id === selectedThreadId) ?? null;
  const selectedApproval = realtime.approvals.find((approval) => approval.threadId === selectedThreadId) ?? realtime.approvals[0] ?? null;
  const working = selectedThread !== null && RUNNING_STATES.has(selectedThread.activity.state);

  const handleError = useCallback((caught: unknown) => {
    if (caught instanceof ApiError && caught.status === 401) {
      onSessionExpired();
      return;
    }
    setError(caught instanceof Error ? caught.message : "Die Aktion ist fehlgeschlagen.");
  }, [onSessionExpired]);

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
    void readCapabilities(selectedRepositoryId).then((value) => {
      setCapabilities(value);
      const defaultModel = value.models.find((item) => item.isDefault) ?? value.models[0];
      setModel((current) => current ?? defaultModel?.id ?? null);
      setEffort((current) => current ?? defaultModel?.defaultReasoningEffort ?? null);
    }).catch(handleError);
  }, [selectedRepositoryId, realtime.processConnection, handleError]);

  useEffect(() => {
    if (realtime.connection !== "connected") return;
    void readRateLimits().catch(handleError);
  }, [realtime.connection, handleError]);

  useEffect(() => {
    mainRef.current?.focus();
  }, []);

  const selectRepository = (repositoryId: string) => {
    setThreadsLoading(true);
    setSelectedRepositoryId(repositoryId);
    setSelectedThreadId(null);
    setSandboxMode("workspace-write");
    setApprovalPolicy("on-request");
    setCapability(null);
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
    setSandboxMode(thread.sandboxMode);
    setApprovalPolicy(thread.approvalPolicy);
    setCapability(null);
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

  const performNewThread = async (suppliedCapability: FullAccessCapability | null) => {
    if (selectedRepositoryId === null) return;
    setOperationPending(true);
    setError(null);
    try {
      const thread = await startThread(
        session.csrfToken,
        selectedRepositoryId,
        sandboxMode,
        approvalPolicy,
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
    if (sandboxMode === "danger-full-access") {
      setFullAccessIntent({
        repositoryId: selectedRepository.id,
        repositoryName: selectedRepository.name,
        threadId: null,
        approvalPolicy,
        after: "new",
        resumeThread: null,
      });
    } else {
      void performNewThread(null);
    }
  };

  const requestFullAccessSelection = (policy: ApprovalPolicy) => {
    if (selectedRepository === null) return;
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
    if (fullAccessIntent === null) return;
    setFullAccessPending(true);
    setError(null);
    try {
      const nextCapability = await prepareFullAccess(
        session.csrfToken,
        fullAccessIntent.repositoryId,
        fullAccessIntent.approvalPolicy,
        fullAccessIntent.threadId,
      );
      const intent = fullAccessIntent;
      setSandboxMode("danger-full-access");
      setApprovalPolicy(intent.approvalPolicy);
      setCapability(nextCapability);
      setFullAccessIntent(null);
      if (intent.after === "new") await performNewThread(nextCapability);
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
    if (selectedThread === null) return false;
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
            <span className={`connection-state is-${realtime.connection}`} role="status" title={connectionLabel(realtime.connection, realtime.processConnection)}>
              <span className="status-dot" aria-hidden="true" />
              <span className="sr-only">{connectionLabel(realtime.connection, realtime.processConnection)}</span>
            </span>
            <HealthStatus />
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
          />
          <main className="conversation-column" id="conversation" tabIndex={-1} ref={mainRef}>
            <header className="thread-header">
              <div>
                <p className="repository-crumb">{selectedRepository?.name ?? "Kein Repository gewählt"} / main</p>
                <h2>{selectedThread?.name ?? (selectedThread?.preview || "Codex Conversation")}</h2>
              </div>
              <div className="thread-meta"><span>{capabilities?.models.find((item) => item.id === model)?.displayName ?? "Model"}</span><span>{working ? "Working" : "Ready"}</span><button type="button" aria-label="Thread-Menü" aria-haspopup="menu">…</button></div>
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
              onModelChange={(nextModel, nextEffort) => { setModel(nextModel); setEffort(nextEffort); }}
              onAccessChange={(nextSandbox, nextPolicy) => {
                if (nextSandbox === "danger-full-access") requestFullAccessSelection(nextPolicy);
                else { setSandboxMode(nextSandbox); setApprovalPolicy(nextPolicy); setCapability(null); }
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
        open={fullAccessIntent !== null}
        repository={fullAccessIntent?.repositoryName ?? ""}
        approvalPolicy={fullAccessIntent?.approvalPolicy ?? approvalPolicy}
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
    </>
  );
}

function connectionLabel(socket: string, process: string): string {
  if (socket === "reconnecting") return "Verbindung wird wiederhergestellt …";
  if (socket !== "connected") return "Realtime wird verbunden …";
  if (process === "connected") return "Codex verbunden";
  if (process === "reconnecting") return "Codex startet neu …";
  return `Codex ${process}`;
}
