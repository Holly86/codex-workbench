export function RepositoryNavigation() {
  return (
    <aside className="repository-panel">
      <nav aria-labelledby="repository-heading">
        <div className="panel-heading">
          <h2 id="repository-heading">Repositories</h2>
          <button type="button" disabled aria-describedby="repository-phase-note">
            Aktualisieren
          </button>
        </div>
        <p className="empty-copy">Noch kein Repository ausgewählt.</p>
        <p className="phase-note" id="repository-phase-note">
          Die sichere Repository-Auswahl wird in der nächsten Phase angebunden.
        </p>
      </nav>
    </aside>
  );
}
