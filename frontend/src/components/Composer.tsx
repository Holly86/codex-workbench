import { useId, useState, type FormEvent } from "react";

export function Composer() {
  const inputId = useId();
  const [message, setMessage] = useState("");

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
  };

  return (
    <form className="composer" aria-label="Nachricht verfassen" onSubmit={handleSubmit}>
      <label className="sr-only" htmlFor={inputId}>
        Nachricht an Codex
      </label>
      <textarea
        id={inputId}
        value={message}
        onChange={(event) => {
          setMessage(event.target.value);
        }}
        placeholder="Beschreibe eine Aufgabe …"
        rows={3}
      />
      <div className="composer-actions">
        <button
          type="button"
          className="secondary-action"
          disabled
          title="Anhänge werden in einer späteren Phase aktiviert"
        >
          Datei hinzufügen
        </button>
        <span className="composer-hint">Strg+V und Drag-and-drop sind vorbereitet.</span>
        <button type="submit" className="primary-action" disabled={message.trim().length === 0}>
          Senden
        </button>
      </div>
    </form>
  );
}
