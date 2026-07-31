export function Conversation() {
  return (
    <section className="conversation" aria-labelledby="conversation-heading">
      <div className="empty-conversation">
        <span className="empty-icon" aria-hidden="true">
          &gt;_
        </span>
        <h2 id="conversation-heading">Bereit für einen neuen Thread</h2>
        <p>
          Nach der Authentifizierungs- und App-Server-Phase erscheinen hier gestreamte Antworten,
          Tool-Aktivitäten und nachvollziehbare Approval-Anfragen.
        </p>
      </div>
    </section>
  );
}
