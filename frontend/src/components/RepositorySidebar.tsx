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
      <div className="panel-heading"><h2>Repositories</h2></div>
      {repositories.length === 0 && !loading ? <p className="phase-note">Keine direkten Repositories gefunden.</p> : null}
      <ul className="repository-list">
        {repositories.map((repository) => (
          <li key={repository.id}>
            <button
              type="button"
              aria-current={repository.id === selectedRepositoryId ? "page" : undefined}
              onClick={() => onSelectRepository(repository.id)}
            >
              <span>{repository.name}</span>
            </button>
          </li>
        ))}
      </ul>
      {selectedRepositoryId !== null ? (
        <section className="thread-navigation" aria-labelledby="threads-heading">
          <div className="panel-heading">
            <h2 id="threads-heading">Threads</h2>
            <button type="button" onClick={onNewThread}>Neu</button>
          </div>
          {loading ? <p className="phase-note" role="status">Threads werden geladen …</p> : null}
          {!loading && threads.length === 0 ? <p className="phase-note">Noch keine Threads.</p> : null}
          <ul className="thread-list">
            {threads.map((thread) => (
              <li key={thread.id}>
                <button
                  type="button"
                  aria-current={thread.id === selectedThreadId ? "page" : undefined}
                  onClick={() => onSelectThread(thread)}
                >
                  <strong>{thread.name ?? (thread.preview || "Unbenannter Thread")}</strong>
                  <span>{thread.status}{thread.fullAccess ? " · Full Access" : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </nav>
  );
}
