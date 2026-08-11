import { useEffect, useReducer, useRef } from "react";

import type {
  Activity,
  Approval,
  NativeJob,
  RateLimits,
  Snapshot,
  ThreadItem,
  ThreadView,
  Turn,
} from "./types";

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected";

export interface TimelineEntry {
  key: string;
  sequence: number;
  type: string;
  label: string;
  occurredAt: string;
}

export interface RealtimeState {
  threads: ThreadView[];
  approvals: Approval[];
  rateLimits: RateLimits;
  connection: ConnectionState;
  processConnection: string;
  generation: number;
  lastSequence: number;
  missedEvents: boolean;
  timeline: Record<string, TimelineEntry[]>;
  jobs: NativeJob[];
}

const unavailableRates: RateLimits = { available: false, observedAt: 0 };

export const initialRealtimeState: RealtimeState = {
  threads: [],
  approvals: [],
  rateLimits: unavailableRates,
  connection: "connecting",
  processConnection: "disconnected",
  generation: 0,
  lastSequence: 0,
  missedEvents: false,
  timeline: {},
  jobs: [],
};

export type Action =
  | { type: "socket"; state: ConnectionState }
  | { type: "snapshot"; snapshot: Snapshot; latest: number; missed: boolean }
  | { type: "threads"; threads: ThreadView[] }
  | { type: "repositoryThreads"; repositoryId: string; threads: ThreadView[] }
  | { type: "thread"; thread: ThreadView }
  | { type: "threadDeleted"; threadId: string }
  | { type: "jobs"; jobs: NativeJob[] }
  | { type: "event"; event: EventEnvelope }
  | { type: "events"; events: EventEnvelope[] };

export interface EventEnvelope {
  sequence: number;
  occurredAt: string;
  type: string;
  payload: Record<string, unknown>;
  threadId: string | null;
  turnId: string | null;
  itemId: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asThread(value: unknown): ThreadView | null {
  if (!isRecord(value) || typeof value.id !== "string" || !Array.isArray(value.turns)) {
    return null;
  }
  return value as unknown as ThreadView;
}

function asActivity(value: unknown): Activity | null {
  if (!isRecord(value) || typeof value.state !== "string" || typeof value.label !== "string") {
    return null;
  }
  return value as unknown as Activity;
}

function asApproval(value: unknown): Approval | null {
  if (!isRecord(value) || typeof value.id !== "string" || !Array.isArray(value.availableDecisions)) {
    return null;
  }
  return value as unknown as Approval;
}

function asJob(value: unknown): NativeJob | null {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.status !== "string") {
    return null;
  }
  return value as unknown as NativeJob;
}

function replaceThread(threads: ThreadView[], next: ThreadView): ThreadView[] {
  const found = threads.some((thread) => thread.id === next.id);
  return found
    ? threads.map((thread) => (thread.id === next.id ? next : thread))
    : [next, ...threads];
}

function updateThread(
  threads: ThreadView[],
  threadId: string | null,
  update: (thread: ThreadView) => ThreadView,
): ThreadView[] {
  if (threadId === null) return threads;
  return threads.map((thread) => (thread.id === threadId ? update(thread) : thread));
}

function withoutTimelineEntry(
  timeline: Record<string, TimelineEntry[]>, threadId: string,
): Record<string, TimelineEntry[]> {
  const next = { ...timeline };
  delete next[threadId];
  return next;
}

function currentTurn(thread: ThreadView, turnId: string | null): Turn | null {
  if (turnId !== null) {
    return thread.turns.find((turn) => turn.id === turnId) ?? null;
  }
  const activeId = thread.activeTurnId;
  return activeId === null ? null : (thread.turns.find((turn) => turn.id === activeId) ?? null);
}

function updateTurn(
  thread: ThreadView,
  turnId: string | null,
  update: (turn: Turn) => Turn,
): ThreadView {
  const selected = currentTurn(thread, turnId);
  if (selected === null) return thread;
  return {
    ...thread,
    turns: thread.turns.map((turn) => (turn.id === selected.id ? update(turn) : turn)),
  };
}

function replaceItem(turn: Turn, item: ThreadItem): Turn {
  const found = turn.items.some((current) => current.id === item.id);
  return {
    ...turn,
    items: found
      ? turn.items.map((current) => (current.id === item.id ? item : current))
      : [...turn.items, item],
  };
}

function appendItemDelta(
  thread: ThreadView,
  turnId: string | null,
  itemId: string | null,
  itemType: string,
  field: "text" | "output",
  delta: string,
): ThreadView {
  if (itemId === null) return thread;
  return updateTurn(thread, turnId, (turn) => {
    const existing = turn.items.find((item) => item.id === itemId);
    const prior = existing?.[field];
    const next = (typeof prior === "string" ? prior : "") + delta;
    const limit = field === "output" ? 262_144 : 500_000;
    const item: ThreadItem = {
      ...(existing ?? { id: itemId, type: itemType }),
      [field]: next.slice(-limit),
    };
    return replaceItem(turn, item);
  });
}

function appendReasoningDelta(
  thread: ThreadView,
  turnId: string | null,
  itemId: string | null,
  delta: string,
): ThreadView {
  if (itemId === null) return thread;
  return updateTurn(thread, turnId, (turn) => {
    const existing = turn.items.find((item) => item.id === itemId);
    const prior = existing?.summary;
    const text = (Array.isArray(prior) ? prior.join("\n") : prior ?? "") + delta;
    return replaceItem(turn, {
      ...(existing ?? { id: itemId, type: "reasoning" }),
      summary: text.slice(-262_144),
    });
  });
}

function eventLabel(event: EventEnvelope): string | null {
  const labels: Record<string, string> = {
    "turn.started": "Turn gestartet",
    "turn.completed": "Turn abgeschlossen",
    "turn.interrupt_requested": "Abbruch angefordert",
    "item.started": "Aktivität gestartet",
    "item.completed": "Aktivität abgeschlossen",
    "plan.updated": "Plan aktualisiert",
    "file_change.updated": "Dateiänderung aktualisiert",
    "context.compacted": "Kontext kompaktiert",
    "approval.requested": "Freigabe angefordert",
    "approval.resolved": "Freigabe beantwortet",
    error: "Codex-Fehler",
    warning: "Codex-Warnung",
    "connection.state": "App-Server-Verbindung geändert",
  };
  return labels[event.type] ?? null;
}

function applyEvent(state: RealtimeState, event: EventEnvelope): RealtimeState {
  if (event.sequence <= state.lastSequence) return state;
  let threads = state.threads;
  let approvals = state.approvals;
  let rateLimits = state.rateLimits;
  let processConnection = state.processConnection;
  let generation = state.generation;
  let jobs = state.jobs;

  if (event.type === "thread.deleted") {
    const threadId = typeof event.payload.threadId === "string" ? event.payload.threadId : event.threadId;
    if (threadId !== null) {
      threads = threads.filter((thread) => thread.id !== threadId);
      approvals = approvals.filter((approval) => approval.threadId !== threadId);
    }
  } else if (["thread.started", "thread.resumed", "thread.reconciled", "thread.snapshot"].includes(event.type)) {
    const thread = asThread(event.payload);
    if (thread !== null) threads = replaceThread(threads, thread);
  } else if (event.type === "activity.updated") {
    const activity = asActivity(event.payload);
    if (activity !== null) threads = updateThread(threads, event.threadId, (thread) => ({ ...thread, activity }));
  } else if (event.type === "approval.requested") {
    const approval = asApproval(event.payload);
    if (approval !== null) approvals = [approval, ...approvals.filter((item) => item.id !== approval.id)];
  } else if (event.type === "approval.resolved") {
    const approvalId = event.payload.approvalId;
    if (typeof approvalId === "string") approvals = approvals.filter((item) => item.id !== approvalId);
  } else if (event.type === "rate_limits.updated") {
    rateLimits = event.payload as unknown as RateLimits;
  } else if (event.type === "connection.state") {
    if (typeof event.payload.state === "string") processConnection = event.payload.state;
    if (typeof event.payload.generation === "number") generation = event.payload.generation;
  } else if (event.type === "job.updated") {
    const job = asJob(event.payload);
    if (job !== null) jobs = [job, ...jobs.filter((item) => item.id !== job.id)];
  } else if (event.type === "turn.started") {
    const turnId = event.payload.turnId;
    if (typeof turnId === "string") {
      threads = updateThread(threads, event.threadId, (thread) => ({
        ...thread,
        status: "running",
        activeTurnId: turnId,
        turns: thread.turns.some((turn) => turn.id === turnId)
          ? thread.turns
          : [...thread.turns, { id: turnId, status: "inProgress", items: [] }],
      }));
    }
  } else if (event.type === "turn.completed") {
    const status = typeof event.payload.status === "string" ? event.payload.status : "failed";
    threads = updateThread(threads, event.threadId, (thread) => ({
      ...thread,
      status,
      activeTurnId: null,
      turns: thread.turns.map((turn) =>
        turn.id === event.turnId ? { ...turn, status } : turn,
      ),
    }));
  } else if (event.type === "item.started" || event.type === "item.completed") {
    const item = event.payload as unknown as ThreadItem;
    if (typeof item.id === "string" && typeof item.type === "string") {
      threads = updateThread(threads, event.threadId, (thread) =>
        updateTurn(thread, event.turnId, (turn) => replaceItem(turn, item)),
      );
    }
  } else if (event.type === "assistant.delta" || event.type === "command.output_delta") {
    const delta = event.payload.delta;
    if (typeof delta === "string") {
      const assistant = event.type === "assistant.delta";
      threads = updateThread(threads, event.threadId, (thread) =>
        appendItemDelta(
          thread,
          event.turnId,
          event.itemId,
          assistant ? "agentMessage" : "commandExecution",
          assistant ? "text" : "output",
          delta,
        ),
      );
    }
  } else if (event.type === "reasoning.summary_delta") {
    const delta = event.payload.delta;
    if (typeof delta === "string") {
      threads = updateThread(threads, event.threadId, (thread) =>
        appendReasoningDelta(thread, event.turnId, event.itemId, delta),
      );
    }
  } else if (event.type === "usage.updated") {
    threads = updateThread(threads, event.threadId, (thread) => ({
      ...thread,
      usage: event.payload as unknown as ThreadView["usage"],
    }));
  } else if (event.type === "plan.updated") {
    const rawPlan = event.payload.plan;
    const plan = Array.isArray(rawPlan)
      ? rawPlan.flatMap((value) =>
          isRecord(value) && typeof value.step === "string" && typeof value.status === "string"
            ? [{ step: value.step, status: value.status }]
            : [],
        )
      : [];
    const planItem: ThreadItem = {
      id: `plan-${event.turnId ?? event.threadId ?? "current"}`,
      type: "planUpdate",
      text: typeof event.payload.explanation === "string" ? event.payload.explanation : undefined,
      plan,
    };
    threads = updateThread(threads, event.threadId, (thread) =>
      updateTurn(thread, event.turnId, (turn) => replaceItem(turn, planItem)),
    );
  } else if (event.type === "file_change.updated") {
    const rawChanges = event.payload.changes;
    if (event.itemId !== null && Array.isArray(rawChanges)) {
      const changes = rawChanges.flatMap((value) => {
        if (!isRecord(value) || typeof value.kind !== "string" || typeof value.diff !== "string") {
          return [];
        }
        return [{
          path: typeof value.path === "string" ? value.path : null,
          kind: value.kind,
          diff: value.diff,
        }];
      });
      const item: ThreadItem = {
        id: event.itemId,
        type: "fileChange",
        status: "inProgress",
        changes,
      };
      threads = updateThread(threads, event.threadId, (thread) =>
        updateTurn(thread, event.turnId, (turn) => replaceItem(turn, item)),
      );
    }
  } else if (event.type === "diff.updated") {
    const diff = event.payload.diff;
    if (typeof diff === "string") {
      threads = updateThread(threads, event.threadId, (thread) => ({ ...thread, latestDiff: diff }));
    }
  } else if (event.type === "thread.status") {
    const status = event.payload.status;
    if (typeof status === "string") {
      threads = updateThread(threads, event.threadId, (thread) => ({ ...thread, status }));
    }
  } else if (event.type === "thread.name") {
    const name = event.payload.name;
    if (typeof name === "string" || name === null) {
      threads = updateThread(threads, event.threadId, (thread) => ({ ...thread, name }));
    }
  }

  let timeline = state.timeline;
  if (event.type === "thread.deleted") {
    const threadId = typeof event.payload.threadId === "string" ? event.payload.threadId : event.threadId;
    if (threadId !== null) {
      timeline = withoutTimelineEntry(timeline, threadId);
    }
  }
  const label = eventLabel(event);
  if (label !== null && event.threadId !== null) {
    const entries = timeline[event.threadId] ?? [];
    timeline = {
      ...timeline,
      [event.threadId]: [
        ...entries,
        { key: `${event.sequence}-${event.type}`, sequence: event.sequence, type: event.type, label, occurredAt: event.occurredAt },
      ].slice(-100),
    };
  }

  return {
    ...state,
    threads,
    approvals,
    rateLimits,
    processConnection,
    generation,
    jobs,
    timeline,
    lastSequence: event.sequence,
  };
}

export function reducer(state: RealtimeState, action: Action): RealtimeState {
  if (action.type === "socket") return { ...state, connection: action.state };
  if (action.type === "threads") return { ...state, threads: action.threads };
  if (action.type === "repositoryThreads") return {
    ...state,
    threads: [
      ...state.threads.filter((thread) => thread.repositoryId !== action.repositoryId),
      ...action.threads,
    ],
  };
  if (action.type === "thread") return { ...state, threads: replaceThread(state.threads, action.thread) };
  if (action.type === "threadDeleted") {
    const timeline = withoutTimelineEntry(state.timeline, action.threadId);
    return {
      ...state,
      threads: state.threads.filter((thread) => thread.id !== action.threadId),
      approvals: state.approvals.filter((approval) => approval.threadId !== action.threadId),
      timeline,
    };
  }
  if (action.type === "jobs") return { ...state, jobs: action.jobs };
  if (action.type === "snapshot") {
    return {
      ...state,
      threads: action.snapshot.threads,
      approvals: action.snapshot.pendingApprovals,
      rateLimits: action.snapshot.rateLimits,
      processConnection: action.snapshot.connection.state,
      generation: action.snapshot.connection.generation,
      connection: "connected",
      lastSequence: action.latest,
      missedEvents: action.missed,
      jobs: action.snapshot.jobs ?? [],
    };
  }
  if (action.type === "events") {
    return action.events.reduce(applyEvent, state);
  }
  return applyEvent(state, action.event);
}

const STREAM_EVENT_TYPES = new Set([
  "assistant.delta",
  "command.output_delta",
  "reasoning.summary_delta",
]);

function parseEvent(value: unknown): EventEnvelope | null {
  if (
    !isRecord(value) ||
    typeof value.sequence !== "number" ||
    typeof value.occurredAt !== "string" ||
    typeof value.type !== "string" ||
    !isRecord(value.payload)
  ) return null;
  return {
    sequence: value.sequence,
    occurredAt: value.occurredAt,
    type: value.type,
    payload: value.payload,
    threadId: typeof value.threadId === "string" ? value.threadId : null,
    turnId: typeof value.turnId === "string" ? value.turnId : null,
    itemId: typeof value.itemId === "string" ? value.itemId : null,
  };
}

function parseSnapshot(value: unknown): { snapshot: Snapshot; latest: number; missed: boolean } | null {
  if (!isRecord(value) || value.type !== "connection.snapshot" || !isRecord(value.snapshot)) return null;
  const snapshot = value.snapshot;
  if (!isRecord(snapshot.connection) || !Array.isArray(snapshot.threads) || !Array.isArray(snapshot.pendingApprovals) || !isRecord(snapshot.rateLimits)) return null;
  return {
    snapshot: snapshot as unknown as Snapshot,
    latest: typeof value.latestSequence === "number" ? value.latestSequence : 0,
    missed: value.missedEvents === true,
  };
}

export function useRealtime(
  csrfToken: string,
  onSessionExpired: () => void,
): [RealtimeState, React.Dispatch<Action>] {
  const [state, dispatch] = useReducer(reducer, initialRealtimeState);
  const lastSequence = useRef(0);

  useEffect(() => {
    lastSequence.current = state.lastSequence;
  }, [state.lastSequence]);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let frame: number | null = null;
    let pending: EventEnvelope[] = [];
    let attempt = 0;

    const flush = () => {
      frame = null;
      if (pending.length === 0) return;
      const events = pending;
      pending = [];
      dispatch({ type: "events", events });
    };

    const enqueue = (event: EventEnvelope) => {
      if (!STREAM_EVENT_TYPES.has(event.type)) {
        flush();
        dispatch({ type: "event", event });
        return;
      }
      pending.push(event);
      frame ??= window.requestAnimationFrame(flush);
    };

    const connect = () => {
      if (stopped) return;
      dispatch({ type: "socket", state: attempt === 0 ? "connecting" : "reconnecting" });
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/events`);
      socket.addEventListener("open", () => {
        attempt = 0;
        socket?.send(JSON.stringify({ type: "subscribe", csrfToken, lastSequence: lastSequence.current }));
      });
      socket.addEventListener("message", (message) => {
        let value: unknown;
        try {
          value = JSON.parse(String(message.data)) as unknown;
        } catch {
          socket?.close(4400, "invalid message");
          return;
        }
        if (isRecord(value) && value.type === "heartbeat") {
          socket?.send(JSON.stringify({ type: "pong" }));
          return;
        }
        const snapshot = parseSnapshot(value);
        if (snapshot !== null) {
          if (frame !== null) window.cancelAnimationFrame(frame);
          frame = null;
          pending = [];
          lastSequence.current = snapshot.latest;
          dispatch({ type: "snapshot", ...snapshot });
          return;
        }
        const event = parseEvent(value);
        if (event === null || event.sequence !== lastSequence.current + 1) {
          socket?.close(4409, "sequence gap");
          return;
        }
        lastSequence.current = event.sequence;
        enqueue(event);
      });
      socket.addEventListener("close", (event) => {
        socket = null;
        if (stopped) return;
        if (event.code === 4401) {
          onSessionExpired();
          return;
        }
        attempt += 1;
        dispatch({ type: "socket", state: "reconnecting" });
        reconnectTimer = window.setTimeout(connect, Math.min(1_000 * 2 ** (attempt - 1), 15_000));
      });
      socket.addEventListener("error", () => socket?.close());
    };
    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (frame !== null) window.cancelAnimationFrame(frame);
      socket?.close(1000, "component unmounted");
    };
  }, [csrfToken, onSessionExpired]);

  return [state, dispatch];
}
