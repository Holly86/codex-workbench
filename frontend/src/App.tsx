import { AuthBoundary } from "./auth/AuthBoundary";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { HealthStatus } from "./components/HealthStatus";
import { RepositoryNavigation } from "./components/RepositoryNavigation";

function focusOnMount(element: HTMLElement | null) {
  element?.focus();
}

export function App() {
  return (
    <AuthBoundary>
      {(_session, performLogout, logoutPending) => (
        <AuthenticatedApp performLogout={performLogout} logoutPending={logoutPending} />
      )}
    </AuthBoundary>
  );
}

function AuthenticatedApp({
  performLogout,
  logoutPending,
}: {
  performLogout: () => Promise<void>;
  logoutPending: boolean;
}) {
  return (
    <>
      <a className="skip-link" href="#conversation">
        Zum Gespräch springen
      </a>
      <div className="app-shell">
        <header className="topbar">
          <div>
            <p className="eyebrow">Local agent workspace</p>
            <h1>Codex Workbench</h1>
          </div>
          <div className="topbar-actions">
            <HealthStatus />
            <button
              type="button"
              className="secondary-action"
              disabled={logoutPending}
              onClick={() => {
                void performLogout();
              }}
            >
              {logoutPending ? "Abmelden …" : "Abmelden"}
            </button>
          </div>
        </header>

        <div className="workspace-layout">
          <RepositoryNavigation />
          <main
            className="conversation-column"
            id="conversation"
            tabIndex={-1}
            ref={focusOnMount}
          >
            <Conversation />
            <Composer />
          </main>
        </div>
      </div>
    </>
  );
}
