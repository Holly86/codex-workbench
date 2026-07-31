import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { HealthStatus } from "./components/HealthStatus";
import { RepositoryNavigation } from "./components/RepositoryNavigation";

export function App() {
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
          <HealthStatus />
        </header>

        <div className="workspace-layout">
          <RepositoryNavigation />
          <main className="conversation-column" id="conversation" tabIndex={-1}>
            <Conversation />
            <Composer />
          </main>
        </div>
      </div>
    </>
  );
}
