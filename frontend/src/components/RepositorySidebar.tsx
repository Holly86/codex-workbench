import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, Dispatch, PointerEvent as ReactPointerEvent, SetStateAction } from "react";
import type { Repository, ThreadView } from "../workbench/types";
import { clampHeight } from "../workbench/uiPreferences";
import type { UiPreferences } from "../workbench/uiPreferences";
import { AnchoredMenu } from "./AnchoredMenu";

export type RepositoryAction = "open" | "new-thread" | "status" | "changes" | "fetch" | "pull" | "push" | "commit" | "branches" | "remote" | "deploy-key" | "publish" | "github" | "rename" | "settings" | "remove" | "delete" | "create" | "clone" | "connect-private";

interface RepositorySidebarProps {
  repositories: Repository[];
  repositoryActivity?: Record<string, number>;
  selectedRepositoryId: string | null;
  threads: ThreadView[];
  selectedThreadId: string | null;
  loading: boolean;
  preferences: UiPreferences;
  onPreferencesChange: Dispatch<SetStateAction<UiPreferences>>;
  onSelectRepository: (repositoryId: string) => void;
  onSelectThread: (thread: ThreadView) => void;
  onNewThread: () => void;
  onDeleteThread: (thread: ThreadView) => void;
  onEditThread?: (thread: ThreadView) => void;
  onRegenerateThread?: (thread: ThreadView) => void;
  onRepositoryAction: (action: RepositoryAction, repository: Repository | null) => void;
}

const REPOSITORY_ACTION_GROUPS: Array<Array<{ action: RepositoryAction; label: string }>> = [
  [
    { action: "open", label: "Öffnen" },
    { action: "new-thread", label: "Neuer Thread" },
  ],
  [
    { action: "status", label: "Git-Status" },
    { action: "changes", label: "Änderungen" },
    { action: "commit", label: "Commit…" },
    { action: "branches", label: "Branches…" },
  ],
  [
    { action: "fetch", label: "Fetch" },
    { action: "pull", label: "Pull – nur Fast-forward" },
    { action: "push", label: "Push" },
    { action: "remote", label: "Remote & Clone" },
    { action: "deploy-key", label: "SSH / Deploy Key" },
  ],
  [
    { action: "publish", label: "Auf GitHub veröffentlichen" },
    { action: "github", label: "Auf GitHub öffnen" },
    { action: "rename", label: "Anzeigenamen ändern" },
    { action: "settings", label: "Repository-Einstellungen" },
  ],
  [
    { action: "remove", label: "Aus Workbench entfernen…" },
    { action: "delete", label: "Lokales Repository löschen…" },
  ],
];

export function RepositorySidebar({
  repositories,
  repositoryActivity = {},
  selectedRepositoryId,
  threads,
  selectedThreadId,
  loading,
  preferences,
  onPreferencesChange: setPreferences,
  onSelectRepository,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  onEditThread,
  onRegenerateThread,
  onRepositoryAction,
}: RepositorySidebarProps) {
  const [query, setQuery] = useState("");
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [openThreadMenu, setOpenThreadMenu] = useState<string | null>(null);
  const collapsed = preferences.sidebar.collapsed;
  const [maximumProjectsHeight, setMaximumProjectsHeight] = useState(520);
  const searchRef = useRef<HTMLInputElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuAnchorRef = useRef<HTMLButtonElement>(null);
  const threadMenuAnchorRef = useRef<HTMLButtonElement>(null);
  const dragRef = useRef<{ pointerId: number; startY: number; startHeight: number } | null>(null);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (collapsed) setPreferences((current) => ({ ...current, sidebar: { ...current.sidebar, collapsed: false } }));
        window.setTimeout(() => searchRef.current?.focus());
      }
      if (event.key === "Escape") { setOpenMenu(null); setOpenThreadMenu(null); }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [collapsed, setPreferences]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const narrow = window.matchMedia("(max-width: 760px)");
    const closeForNarrowViewport = (event: MediaQueryListEvent | MediaQueryList) => {
      if (event.matches) setPreferences((current) => ({ ...current, sidebar: { ...current.sidebar, collapsed: true } }));
    };
    closeForNarrowViewport(narrow);
    narrow.addEventListener("change", closeForNarrowViewport);
    return () => narrow.removeEventListener("change", closeForNarrowViewport);
  }, [setPreferences]);

  const setSidebarCollapsed = (next: boolean) => {
    setPreferences((current) => ({ ...current, sidebar: { ...current.sidebar, collapsed: next } }));
  };
  const updateSidebar = useCallback((values: Partial<UiPreferences["sidebar"]>) => {
    setPreferences((current) => ({ ...current, sidebar: { ...current.sidebar, ...values } }));
  }, [setPreferences]);
  const availableProjectsHeight = useCallback(() => {
    const panel = sidebarRef.current;
    if (panel === null || panel.clientHeight <= 0) return 520;
    const fixed = [...panel.children].reduce((sum, child) => {
      if (!(child instanceof HTMLElement)) return sum;
      if (child.classList.contains("repository-navigation")
        || child.classList.contains("thread-navigation")
        || child.classList.contains("sidebar-splitter")) return sum;
      return sum + child.getBoundingClientRect().height;
    }, 0);
    return Math.max(140, panel.clientHeight - fixed - 190);
  }, []);
  const resize = useCallback((value: number) => {
    const maximum = availableProjectsHeight();
    setMaximumProjectsHeight(maximum);
    updateSidebar({ projectsHeight: clampHeight(value, maximum) });
  }, [availableProjectsHeight, updateSidebar]);
  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (typeof window.matchMedia === "function" && window.matchMedia("(max-width: 760px)").matches) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight: preferences.sidebar.projectsHeight,
    };
  };
  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    resize(drag.startHeight + event.clientY - drag.startY);
  };
  const stopResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  const closeOnNarrow = () => {
    if (typeof window.matchMedia === "function" && window.matchMedia("(max-width: 760px)").matches) {
      setSidebarCollapsed(true);
    }
  };
  const normalized = query.trim().toLocaleLowerCase();
  const filteredRepositories = repositories.filter((repository) =>
    !normalized || `${repository.name} ${repository.status.branch ?? ""}`.toLocaleLowerCase().includes(normalized));
  const filteredThreads = threads.filter((thread) =>
    !normalized || (thread.name ?? thread.preview).toLocaleLowerCase().includes(normalized));
  const menuRepository = repositories.find((repository) => repository.id === openMenu) ?? null;
  const menuThread = threads.find((thread) => thread.id === openThreadMenu) ?? null;
  const revealProjects = normalized.length > 0 || !preferences.sidebar.projectsCollapsed;
  const revealThreads = normalized.length > 0 || !preferences.sidebar.threadsCollapsed;
  const sidebarStyle = { "--projects-height": `${String(preferences.sidebar.projectsHeight)}px` } as CSSProperties;

  useEffect(() => {
    const fit = () => {
      const maximum = availableProjectsHeight();
      setMaximumProjectsHeight(maximum);
      if (preferences.sidebar.projectsHeight > maximum) resize(preferences.sidebar.projectsHeight);
    };
    window.addEventListener("resize", fit);
    fit();
    return () => window.removeEventListener("resize", fit);
  }, [availableProjectsHeight, preferences.sidebar.projectsHeight, resize]);

  return (
    <>
      <button
        type="button"
        className="mobile-sidebar-trigger"
        aria-label={collapsed ? "Navigation öffnen" : "Navigation schließen"}
        aria-controls="repository-sidebar"
        aria-expanded={!collapsed}
        onClick={() => setSidebarCollapsed(!collapsed)}
      >
        <span aria-hidden="true">{collapsed ? "☰" : "×"}</span>
      </button>
      {!collapsed ? <button type="button" className="sidebar-scrim" aria-label="Navigation schließen" onClick={() => setSidebarCollapsed(true)} /> : null}
      <nav ref={sidebarRef} id="repository-sidebar" className={`repository-panel ${collapsed ? "is-collapsed" : ""} sections-${revealProjects ? "projects-open" : "projects-closed"}-${revealThreads ? "threads-open" : "threads-closed"}`} aria-label="Repositories" style={sidebarStyle}>
        <div className="sidebar-header">
          <div className="sidebar-heading"><span className="sidebar-heading-icon" aria-hidden="true">⌁</span><strong>Workspace</strong></div>
          <button type="button" className="sidebar-collapse" aria-label={collapsed ? "Sidebar erweitern" : "Sidebar einklappen"} aria-expanded={!collapsed} onClick={() => setSidebarCollapsed(!collapsed)}>
            <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
          </button>
        </div>
        <button className="new-thread-action" type="button" disabled={selectedRepositoryId === null} onClick={() => { onNewThread(); closeOnNarrow(); }}>
          <span aria-hidden="true">＋</span><span className="new-thread-label">Neuer Thread</span>
        </button>
        <div className="repository-create-actions" aria-label="Repository-Aktionen">
          <button type="button" onClick={() => onRepositoryAction("create", null)}><span aria-hidden="true">□＋</span> Neu</button>
          <button type="button" onClick={() => onRepositoryAction("clone", null)}><span aria-hidden="true">↓</span> Klonen</button>
          <button type="button" onClick={() => onRepositoryAction("connect-private", null)}><span aria-hidden="true">⌁</span> Privat</button>
        </div>
        <label className="sidebar-search">
          <span className="search-icon" aria-hidden="true">⌕</span>
          <span className="sr-only">Repositories und Threads suchen</span>
          <input ref={searchRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Suchen" />
          <kbd>Ctrl K</kbd>
        </label>

        <section className={`repository-navigation ${revealProjects ? "" : "is-section-collapsed"}`} aria-labelledby="projects-heading">
          <div className="panel-heading"><h2 id="projects-heading"><button type="button" aria-expanded={revealProjects} aria-controls="project-list" onClick={() => updateSidebar({ projectsCollapsed: !preferences.sidebar.projectsCollapsed })}><span aria-hidden="true">{revealProjects ? "▾" : "▸"}</span><span>Projekte</span></button></h2><span>{filteredRepositories.length}</span></div>
          <div id="project-list" hidden={!revealProjects}>
          {repositories.length === 0 && !loading ? <p className="sidebar-empty">Keine Repositories gefunden.</p> : null}
          {repositories.length > 0 && filteredRepositories.length === 0 ? <p className="sidebar-empty">Keine passenden Projekte.</p> : null}
          <ul className="repository-list">
            {filteredRepositories.map((repository) => {
              const changeCount = repository.status.staged + repository.status.modified + repository.status.untracked;
              const activeTurnCount = repositoryActivity[repository.id]
                ?? repository.activeTurnCount
                ?? 0;
              const hasActiveTurn = activeTurnCount > 0;
              return (
                <li key={repository.id} className="repository-row">
                  <button
                    type="button"
                    className="repository-select"
                    title={collapsed ? `${repository.name}${hasActiveTurn ? " · Mindestens ein Turn läuft" : ""}` : undefined}
                    aria-label={hasActiveTurn ? `${repository.name}, mindestens ein Turn läuft` : undefined}
                    aria-current={repository.id === selectedRepositoryId ? "page" : undefined}
                    onClick={() => { onSelectRepository(repository.id); closeOnNarrow(); }}
                  >
                    <span className={`repository-glyph ${hasActiveTurn ? "is-active" : ""}`} aria-hidden="true">◇</span>
                    <span className="repository-title">
                      <strong>{repository.name}</strong>
                      <small><span>{repository.status.detached ? "Detached HEAD" : repository.status.branch ?? "Kein Branch"}</span><span className={repository.status.clean ? "repo-clean" : "repo-changed"}>{repository.status.clean ? "Clean" : `${String(changeCount)} geändert`}</span>{hasActiveTurn ? <span className="repository-active-turn"><i aria-hidden="true" />Läuft</span> : null}</small>
                    </span>
                  </button>
                  <button
                    ref={openMenu === repository.id ? menuAnchorRef : undefined}
                    type="button"
                    className="repository-menu-trigger"
                    aria-label={`${repository.name} Menü`}
                    aria-haspopup="menu"
                    aria-expanded={openMenu === repository.id}
                    onClick={(event) => {
                      menuAnchorRef.current = event.currentTarget;
                      setOpenMenu(openMenu === repository.id ? null : repository.id);
                    }}
                  >
                    <span aria-hidden="true">•••</span>
                  </button>
                </li>
              );
            })}
          </ul>
          </div>
        </section>

        {selectedRepositoryId !== null ? (
          <>{/* The ARIA separator is intentionally keyboard-interactive; the lint rule does not model this APG pattern. */}
          {/* eslint-disable jsx-a11y-x/no-noninteractive-element-interactions, jsx-a11y-x/no-noninteractive-tabindex */}
          {revealProjects && revealThreads ? <div
            className="sidebar-splitter"
            role="separator"
            aria-label="Höhe der Projektliste"
            aria-orientation="horizontal"
            aria-valuemin={140}
            aria-valuemax={maximumProjectsHeight}
            aria-valuenow={preferences.sidebar.projectsHeight}
            tabIndex={0}
            onPointerDown={startResize}
            onPointerMove={moveResize}
            onPointerUp={stopResize}
            onPointerCancel={stopResize}
            onLostPointerCapture={() => { dragRef.current = null; }}
            onDoubleClick={() => resize(280)}
            onKeyDown={(event) => {
              if (event.key === "ArrowUp") { event.preventDefault(); resize(preferences.sidebar.projectsHeight - 16); }
              if (event.key === "ArrowDown") { event.preventDefault(); resize(preferences.sidebar.projectsHeight + 16); }
              if (event.key === "Home") { event.preventDefault(); resize(140); }
              if (event.key === "End") { event.preventDefault(); resize(maximumProjectsHeight); }
            }}
          /> : null}
          {/* eslint-enable jsx-a11y-x/no-noninteractive-element-interactions, jsx-a11y-x/no-noninteractive-tabindex */}
          <section className={`thread-navigation ${revealThreads ? "" : "is-section-collapsed"}`} aria-labelledby="threads-heading">
            <div className="panel-heading"><h2 id="threads-heading"><button type="button" aria-expanded={revealThreads} aria-controls="thread-list" onClick={() => updateSidebar({ threadsCollapsed: !preferences.sidebar.threadsCollapsed })}><span aria-hidden="true">{revealThreads ? "▾" : "▸"}</span><span>Threads</span></button></h2><span>{threads.length}</span></div>
            <div id="thread-list" hidden={!revealThreads}>
            {loading ? <div className="thread-skeleton" role="status" aria-label="Threads werden geladen"><span /><span /><span /></div> : null}
            {!loading && threads.length === 0 ? <p className="sidebar-empty">Noch keine Threads.</p> : null}
            {!loading && threads.length > 0 && filteredThreads.length === 0 ? <p className="sidebar-empty">Keine passenden Threads.</p> : null}
            <ul className="thread-list">
              {filteredThreads.map((thread) => {
                const label = activityLabel(thread.activity.state, thread.activeTurnId);
                return (
                  <li key={thread.id} className={`thread-row is-${thread.activeTurnId != null ? "running" : thread.activity.state}`}>
                    <button
                      type="button"
                      aria-current={thread.id === selectedThreadId ? "page" : undefined}
                      onClick={() => { onSelectThread(thread); closeOnNarrow(); }}
                    >
                      <span className="thread-title">{thread.name ?? (thread.preview || "Neuer Thread")}</span>
                      <span className="thread-meta-row"><span className="thread-state"><i aria-hidden="true" />{label}</span><time>{formatActivityTime(thread.updatedAt)}</time></span>
                    </button>
                    <button
                      ref={openThreadMenu === thread.id ? threadMenuAnchorRef : undefined}
                      type="button"
                      className="thread-menu-trigger"
                      aria-label="Thread-Aktionen"
                      title={thread.name ?? (thread.preview || "Thread")}
                      aria-haspopup="menu"
                      aria-expanded={openThreadMenu === thread.id}
                      onClick={(event) => {
                        threadMenuAnchorRef.current = event.currentTarget;
                        setOpenThreadMenu(openThreadMenu === thread.id ? null : thread.id);
                      }}
                    ><span aria-hidden="true">•••</span></button>
                  </li>
                );
              })}
            </ul>
            </div>
          </section></>
        ) : null}
      </nav>
      <AnchoredMenu anchorRef={menuAnchorRef} open={menuRepository !== null} onClose={() => setOpenMenu(null)} ariaLabel={menuRepository === null ? "Repository-Menü" : `${menuRepository.name} Aktionen`} width={272} align="end">
        {menuRepository === null ? null : REPOSITORY_ACTION_GROUPS.map((group, groupIndex) => (
          <Fragment key={group[0]?.action ?? String(groupIndex)}>
            {groupIndex > 0 ? <div className="menu-separator" role="separator" /> : null}
            {group.map(({ action, label }) => (
              <button
                type="button"
                role="menuitem"
                key={action}
                disabled={action === "github" && !menuRepository.status.upstream}
                onClick={() => {
                  onRepositoryAction(action, menuRepository);
                  setOpenMenu(null);
                  closeOnNarrow();
                }}
              >
                <span>{label}</span>
              </button>
            ))}
          </Fragment>
        ))}
      </AnchoredMenu>
      <AnchoredMenu anchorRef={threadMenuAnchorRef} open={menuThread !== null} onClose={() => setOpenThreadMenu(null)} ariaLabel="Thread-Aktionen" width={210} align="end">
        {menuThread === null ? null : <><button type="button" role="menuitem" onClick={() => { onEditThread?.(menuThread); setOpenThreadMenu(null); }}>Titel bearbeiten …</button><button type="button" role="menuitem" onClick={() => { onRegenerateThread?.(menuThread); setOpenThreadMenu(null); }}>Titelstatus anzeigen …</button><div className="menu-separator" role="separator" /><button type="button" role="menuitem" onClick={() => {
          onDeleteThread(menuThread);
          setOpenThreadMenu(null);
        }}>Thread löschen …</button></>}
      </AnchoredMenu>
    </>
  );
}

function activityLabel(state: ThreadView["activity"]["state"], activeTurnId?: string | null): string {
  if (activeTurnId != null) return "Läuft";
  if (state === "awaiting_approval") return "Freigabe nötig";
  if (state === "failed") return "Fehlgeschlagen";
  if (state === "cancelled") return "Abgebrochen";
  return state === "completed" ? "Abgeschlossen" : "Bereit";
}

function formatActivityTime(timestamp: number): string {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(timestamp * 1000));
}
