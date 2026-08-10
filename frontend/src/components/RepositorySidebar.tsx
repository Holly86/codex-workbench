import type { Repository, ThreadView } from "../workbench/types";

interface RepositorySidebarProps {
  repositories: Repository[];
  selectedRepositoryId: string | null;
  threads: ThreadView[];
  selectedThreadId: string | null;
  loading: boolean;
  onSelectRepository: (repositoryId: string) => void;
  onSelectThread: (thread: ThreadView) => void;
  onNewThread: () => void;
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
}: RepositorySidebarProps) {
  return (
    <nav className="repository-panel" aria-label="Repositories">
      <button className="new-thread-action" type="button" disabled={selectedRepositoryId === null} onClick={onNewThread}><span>＋</span> Neuer Thread</button>
      <label className="sidebar-search"><span className="sr-only">Threads suchen</span><input type="search" placeholder="Suchen" /></label>
      <div className="panel-heading"><h2>Projekte</h2></div>
      {repositories.length === 0 && !loading ? <p className="phase-note">Keine direkten Repositories gefunden.</p> : null}
      <ul className="repository-list">
        {repositories.map((repository) => (
          <li key={repository.id}>
            <button
              type="button"
              aria-current={repository.id === selectedRepositoryId ? "page" : undefined}
              onClick={() => onSelectRepository(repository.id)}
            >
              <span className="repository-glyph" aria-hidden="true">◇</span><span className="repository-title"><strong>{repository.name}</strong><small>{repository.branch ?? "Git repository"}</small></span><span className="repository-more" aria-hidden="true">…</span>
            </button>
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
            {threads.map((thread) => (
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
