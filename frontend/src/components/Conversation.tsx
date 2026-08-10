import { useEffect, useRef, useState } from "react";

import type { ThreadItem, ThreadView } from "../workbench/types";
import { CopyButton } from "./CopyButton";

interface ConversationProps {
  thread: ThreadView | null;
}

export function Conversation({ thread }: ConversationProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);

  useEffect(() => {
    if (following) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [thread, following]);

  if (thread === null) {
    return (
      <section className="conversation empty-conversation" aria-labelledby="conversation-heading">
        <span className="empty-icon" aria-hidden="true">&gt;_</span>
        <h2 id="conversation-heading">Repository wählen und Thread öffnen</h2>
        <p>Antworten, Aktivitäten, Freigaben und Nutzung erscheinen hier live.</p>
      </section>
    );
  }

  return (
    <section
      className="conversation"
      aria-label={`Conversation ${thread.name ?? (thread.preview || thread.id)}`}
      ref={scrollRef}
      onScroll={(event) => {
        const element = event.currentTarget;
        setFollowing(element.scrollHeight - element.scrollTop - element.clientHeight < 80);
      }}
    >
      {thread.turns.length === 0 ? (
        <div className="empty-conversation compact"><h2>Neuer Thread</h2><p>Was soll Codex in diesem Repository tun?</p></div>
      ) : null}
      {thread.turns.map((turn) => (
        <section className="turn" key={turn.id} aria-label={`Turn ${turn.status}`}>
          {turn.items.map((item) => <ConversationItem item={item} key={item.id} />)}
        </section>
      ))}
      {!following ? (
        <button
          type="button"
          className="latest-action"
          onClick={() => {
            setFollowing(true);
            scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
          }}
        >
          Zum neuesten Ereignis
        </button>
      ) : null}
    </section>
  );
}

function ConversationItem({ item }: { item: ThreadItem }) {
  if (item.type === "userMessage") {
    const text = item.content?.filter((part) => part.type === "text").map((part) => part.text ?? "").join("\n") ?? "";
    const images = item.content?.filter((part) => part.type === "image").length ?? 0;
    return <article className="message user-message"><p>{text}</p>{images > 0 ? <span>{images} Bildanhang/-anhänge</span> : null}<div className="message-actions"><CopyButton text={text} label="Eigene Nachricht kopieren" /></div></article>;
  }
  if (item.type === "agentMessage") {
    const text = item.text ?? "";
    return <article className="message agent-message"><RichText text={text} /><div className="message-actions"><CopyButton text={plainText(text)} label="Assistant-Antwort kopieren" /></div></article>;
  }
  if (item.type === "reasoning") {
    const summary = Array.isArray(item.summary) ? item.summary.join("\n") : item.summary;
    return <details className="reasoning-item"><summary>Reasoning-Zusammenfassung</summary><p>{summary}</p></details>;
  }
  if (item.type === "commandExecution") {
    return (
      <details className="activity-item" open={item.status === "inProgress"}>
        <summary>Befehl · {item.status ?? "Status unbekannt"}</summary>
        <code>{item.command ?? "Befehl nicht verfügbar"}</code>
        <CopyButton text={item.command ?? ""} label="Befehl kopieren" />
        {item.cwdDisplay !== null && item.cwdDisplay !== undefined ? <span>Arbeitsbereich: {item.cwdDisplay}</span> : null}
        {item.output ? <div className="code-shell"><CopyButton text={item.output} label="Output kopieren" /><pre>{item.output}</pre></div> : null}
      </details>
    );
  }
  if (item.type === "fileChange") {
    return (
      <details className="activity-item" open>
        <summary>Dateiänderung · {item.status ?? "aktualisiert"}</summary>
        {item.changes?.map((change, index) => <div className="code-shell" key={`${change.path ?? "file"}-${String(index)}`}><CopyButton text={change.diff} label="Datei-Diff kopieren" /><pre>{change.path ?? "Datei"}\n{change.diff}</pre></div>)}
      </details>
    );
  }
  if (item.type === "plan" || item.type === "planUpdate") {
    return <article className="plan-item"><strong>Plan</strong>{item.text ? <p>{item.text}</p> : null}{item.plan && item.plan.length > 0 ? <ol>{item.plan.map((step, index) => <li key={`${step.step}-${String(index)}`}><span>{step.status}</span> {step.step}</li>)}</ol> : null}</article>;
  }
  return <article className="activity-item"><span>{item.type}</span>{item.text ? <p>{item.text}</p> : null}</article>;
}

function plainText(value: string): string {
  return value.replace(/```[^\n]*\n([\s\S]*?)```/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
}

function RichText({ text }: { text: string }) {
  const parts = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  return <>{parts.map((part, index) => {
    const match = /^```([^\n]*)\n([\s\S]*?)```$/.exec(part);
    if (match === null) return <p key={String(index)}>{part}</p>;
    const code = match[2] ?? "";
    return <div className="code-shell" key={String(index)}>{match[1] ? <span className="code-language">{match[1]}</span> : null}<CopyButton text={code} label="Code kopieren" /><pre><code>{code}</code></pre></div>;
  })}</>;
}
