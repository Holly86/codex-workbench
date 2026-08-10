import { useEffect, useRef, useState } from "react";
import type { Repository, ThreadView } from "../workbench/types";

export type RepositoryAction = "open" | "new-thread" | "status" | "changes" | "fetch" | "pull" | "push" | "commit" | "branches" | "remote" | "publish" | "github" | "rename" | "settings" | "create" | "clone";

interface RepositorySidebarProps {
  repositories: Repository[];
  selectedRepositoryId: string | null;
  threads: ThreadView[];
  selectedThreadId: string | null;
  loading: boolean;
  onSelectRepository: (repositoryId: string) => void;
  onSelectThread: (thread: ThreadView) => void;
  onNewThread: () => void;
  onRepositoryAction: (action: RepositoryAction, repository: Repository | null) => void;
}

export function RepositorySidebar({
  repositories,
  selectedRepositoryId,
  threads,
  selectedThreadId,
  loading,
  onSelectRepository,
  onSelectThread,
  onNewThread,
  onRepositoryAction,
}: RepositorySidebarProps) {
  const [query, setQuery] = useState("");
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("workbench.sidebar.collapsed") === "true");
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); searchRef.current?.focus();
      }
      if (event.key === "Escape") setOpenMenu(null);
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  const normalized = query.trim().toLocaleLowerCase();
  const filteredRepositories = repositories.filter((repository) => !normalized || repository.name.toLocaleLowerCase().includes(normalized));
  const filteredThreads = threads.filter((thread) => !normalized || (thread.name ?? thread.preview).toLocaleLowerCase().includes(normalized));
  return (
    <nav className={`repository-panel ${collapsed ? "is-collapsed" : ""}`} aria-label="Repositories">
      <button type="button" className="sidebar-collapse" aria-label={collapsed ? "Sidebar erweitern" : "Sidebar einklappen"} aria-expanded={!collapsed} onClick={() => { const next = !collapsed; setCollapsed(next); localStorage.setItem("workbench.sidebar.collapsed", String(next)); }}>☰</button>
      <button className="new-thread-action" type="button" disabled={selectedRepositoryId === null} onClick={onNewThread}><span>＋</span> Neuer Thread</button>
      <div className="repository-create-actions"><button type="button" onClick={() => onRepositoryAction("create", null)}>New repository</button><button type="button" onClick={() => onRepositoryAction("clone", null)}>Clone</button></div>
      <label className="sidebar-search"><span className="sr-only">Repositories und Threads suchen</span><input ref={searchRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Suchen  Ctrl K" /></label>
      <div className="panel-heading"><h2>Projekte</h2></div>
      {repositories.length === 0 && !loading ? <p className="phase-note">Keine direkten Repositories gefunden.</p> : null}
      <ul className="repository-list">
        {filteredRepositories.map((repository) => (
          <li key={repository.id} className="repository-row">
            <button
              type="button"
              aria-current={repository.id === selectedRepositoryId ? "page" : undefined}
              onClick={() => onSelectRepository(repository.id)}
            >
              <span className="repository-glyph" aria-hidden="true">◇</span><span className="repository-title"><strong>{repository.name}</strong><small>{repository.status.detached ? "Detached HEAD" : repository.status.branch ?? "No branch"}{repository.status.clean ? " · Clean" : ` · ${String(repository.status.staged + repository.status.modified + repository.status.untracked)} changes`}</small></span>
            </button>
            <button type="button" className="repository-menu-trigger" aria-label={`${repository.name} Menü`} aria-haspopup="menu" aria-expanded={openMenu === repository.id} onClick={() => setOpenMenu(openMenu === repository.id ? null : repository.id)}>…</button>
            {openMenu === repository.id ? <div className="repository-context-menu" role="menu">{[
              ["open","Open"],["new-thread","New Thread"],["status","Git Status"],["changes","Changes"],["fetch","Fetch"],["pull","Pull --ff-only"],["push","Push"],["commit","Commit…"],["branches","Branches…"],["remote","Clone / Remote"],["publish","Publish to GitHub"],["github","Open on GitHub"],["rename","Rename display name"],["settings","Settings"],
            ].map(([action,label]) => <button type="button" role="menuitem" key={action} disabled={action === "github" && !repository.status.upstream} onClick={() => { onRepositoryAction(action as RepositoryAction, repository); setOpenMenu(null); }}>{label}</button>)}</div> : null}
          </li>
        ))}
      </ul>
      {selectedRepositoryId !== null ? (
        <section className="thread-navigation" aria-labelledby="threads-heading">
          <div className="panel-heading">
            <h2 id="threads-heading">Threads</h2>
            <span>{threads.length}</span>
          </div>
          {loading ? <div className="thread-skeleton" role="status" aria-label="Threads werden geladen"><span /><span /><span /></div> : null}
          {!loading && threads.length === 0 ? <p className="phase-note">Noch keine Threads.</p> : null}
          <ul className="thread-list">
            {filteredThreads.map((thread) => (
              <li key={thread.id}>
                <button
                  type="button"
                  aria-current={thread.id === selectedThreadId ? "page" : undefined}
                  onClick={() => onSelectThread(thread)}
                >
                  <strong>{thread.name ?? (thread.preview || "Neuer Thread")}</strong>
                  <span className={`thread-state is-${thread.activity.state}`}><i aria-hidden="true" />{activityLabel(thread.activity.state)} · {formatActivityTime(thread.updatedAt)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </nav>
  );
}

function activityLabel(state: ThreadView["activity"]["state"]): string {
  if (["starting", "thinking", "planning", "reading", "searching", "executing", "editing", "testing", "responding", "reconnecting"].includes(state)) return "Working";
  if (state === "awaiting_approval") return "Waiting for approval";
  if (state === "failed") return "Failed";
  if (state === "cancelled") return "Cancelled";
  return state === "completed" ? "Completed" : "";
}

function formatActivityTime(timestamp: number): string {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(timestamp * 1000));
}
