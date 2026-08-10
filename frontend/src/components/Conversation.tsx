import { memo, useEffect, useRef, useState } from "react";

import type { ThreadItem, ThreadView } from "../workbench/types";
import { CopyButton } from "./CopyButton";

interface ConversationProps {
  thread: ThreadView | null;
  loading?: boolean;
}

export function Conversation({ thread, loading = false }: ConversationProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const threadId = thread?.id ?? null;
  const lastItemId = thread?.turns.at(-1)?.items.at(-1)?.id ?? null;
  const lastItemText = thread?.turns.at(-1)?.items.at(-1)?.text;

  useEffect(() => {
    const element = scrollRef.current;
    if (element === null || threadId === null) return;
    const saved = readAnchor(threadId);
    const frame = window.requestAnimationFrame(() => {
      if (saved !== null) {
        const anchor = element.querySelector<HTMLElement>(`[data-item-id="${CSS.escape(saved.id)}"]`);
        if (anchor !== null) {
          element.scrollTop = anchor.offsetTop + saved.offset;
          return;
        }
      }
      element.scrollTop = element.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [threadId]);

  useEffect(() => {
    if (!following) return;
    const frame = window.requestAnimationFrame(() => {
      const element = scrollRef.current;
      element?.scrollTo({ top: element.scrollHeight });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [lastItemId, lastItemText, following]);

  useEffect(() => () => {
    if (threadId !== null && scrollRef.current !== null) saveAnchor(threadId, scrollRef.current);
  }, [threadId]);

  if (thread === null) {
    return (
      <section className="conversation empty-conversation" aria-labelledby="conversation-heading">
        <span className="empty-icon" aria-hidden="true">&gt;_</span>
        <h2 id="conversation-heading">{loading ? "Thread wird geladen …" : "Repository wählen und Thread öffnen"}</h2>
        <p>{loading ? "Der vollständige Verlauf und der aktuelle Laufstatus werden abgeglichen." : "Antworten, Aktivitäten, Freigaben und Nutzung erscheinen hier live."}</p>
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
        const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
        setFollowing(nearBottom);
        if (threadId !== null) saveAnchor(threadId, element);
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

const ConversationItem = memo(function ConversationItem({ item }: { item: ThreadItem }) {
  if (item.type === "userMessage") {
    const text = item.content?.filter((part) => part.type === "text").map((part) => part.text ?? "").join("\n") ?? "";
    const images = item.content?.filter((part) => part.type === "image").length ?? 0;
    return <article className="message user-message" data-item-id={item.id}><p>{text}</p>{images > 0 ? <span>{images} Bildanhang/-anhänge</span> : null}<div className="message-actions"><CopyButton text={text} label="Eigene Nachricht kopieren" /></div></article>;
  }
  if (item.type === "agentMessage") {
    const text = item.text ?? "";
    return <article className="message agent-message" data-item-id={item.id}><RichText text={text} /><div className="message-actions"><CopyButton text={plainText(text)} label="Assistant-Antwort kopieren" /></div></article>;
  }
  if (item.type === "reasoning") {
    const summary = Array.isArray(item.summary) ? item.summary.join("\n") : item.summary;
    return <details className="reasoning-item" data-item-id={item.id}><summary>Reasoning-Zusammenfassung</summary><p>{summary}</p></details>;
  }
  if (item.type === "commandExecution") {
    return (
      <details className="activity-item" data-item-id={item.id} open={item.status === "inProgress"}>
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
      <details className="activity-item" data-item-id={item.id} open={item.status === "inProgress"}>
        <summary>Dateiänderung · {item.status ?? "aktualisiert"}</summary>
        {item.changes?.map((change, index) => <div className="code-shell" key={`${change.path ?? "file"}-${String(index)}`}><CopyButton text={change.diff} label="Datei-Diff kopieren" /><pre>{change.path ?? "Datei"}\n{change.diff}</pre></div>)}
      </details>
    );
  }
  if (item.type === "plan" || item.type === "planUpdate") {
    return <article className="plan-item" data-item-id={item.id}><strong>Plan</strong>{item.text ? <p>{item.text}</p> : null}{item.plan && item.plan.length > 0 ? <ol>{item.plan.map((step, index) => <li key={`${step.step}-${String(index)}`}><span>{step.status}</span> {step.step}</li>)}</ol> : null}</article>;
  }
  return <article className="activity-item" data-item-id={item.id}><span>{item.type}</span>{item.text ? <p>{item.text}</p> : null}</article>;
});

function plainText(value: string): string {
  return value.replace(/```[^\n]*\n([\s\S]*?)```/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
}

const RichText = memo(function RichText({ text }: { text: string }) {
  const parts = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  return <>{parts.map((part, index) => {
    const match = /^```([^\n]*)\n([\s\S]*?)```$/.exec(part);
    if (match === null) return <p key={String(index)}>{part}</p>;
    const code = match[2] ?? "";
    return <div className="code-shell" key={String(index)}>{match[1] ? <span className="code-language">{match[1]}</span> : null}<CopyButton text={code} label="Code kopieren" /><pre><code>{code}</code></pre></div>;
  })}</>;
});

interface ScrollAnchor { id: string; offset: number }

function readAnchor(threadId: string): ScrollAnchor | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(`workbench.scroll.${threadId}`) ?? "null") as unknown;
    if (typeof value === "object" && value !== null && "id" in value && "offset" in value
      && typeof value.id === "string" && typeof value.offset === "number") return value as ScrollAnchor;
  } catch { /* Invalid browser state is ignored. */ }
  return null;
}

function saveAnchor(threadId: string, container: HTMLElement): void {
  const items = Array.from(container.querySelectorAll<HTMLElement>("[data-item-id]"));
  const anchor = items.find((item) => item.offsetTop + item.offsetHeight >= container.scrollTop);
  if (anchor?.dataset.itemId === undefined) return;
  sessionStorage.setItem(`workbench.scroll.${threadId}`, JSON.stringify({
    id: anchor.dataset.itemId,
    offset: container.scrollTop - anchor.offsetTop,
  }));
}
