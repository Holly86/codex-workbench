import { describe, expect, it } from "vitest";
import { initialRealtimeState, reducer, type EventEnvelope } from "./realtime";
import type { ThreadView } from "./types";

const thread: ThreadView = {
  id: "thread_1", repositoryId: "repo_1", repositoryName: "sample", preview: "",
  name: null, status: "running", sandboxMode: "workspace-write", approvalPolicy: "on-request",
  fullAccess: false, createdAt: 1, updatedAt: 1, activeTurnId: "turn_1",
  turns: [{ id: "turn_1", status: "inProgress", items: [] }], usage: null,
  activity: { state: "responding", label: "Antwortet", last_activity: null, started_at: null,
    observed_at: "2026-01-01T00:00:00Z", elapsed_ms: 0, current_tool: null,
    current_command: null, pending_approval_id: null, error_code: null, error_message: null },
  latestDiff: null,
};

function delta(sequence: number, value: string): EventEnvelope {
  return { sequence, occurredAt: "2026-01-01T00:00:00Z", type: "assistant.delta",
    payload: { delta: value }, threadId: thread.id, turnId: "turn_1", itemId: "item_1" };
}

describe("realtime streaming reducer", () => {
  it("applies a frame batch in sequence without losing text", () => {
    const state = { ...initialRealtimeState, threads: [thread] };
    const next = reducer(state, { type: "events", events: [delta(1, "A"), delta(2, "B"), delta(3, "C")] });
    expect(next.lastSequence).toBe(3);
    expect(next.threads[0]?.turns[0]?.items[0]?.text).toBe("ABC");
  });

  it("ignores replayed events", () => {
    const state = reducer({ ...initialRealtimeState, threads: [thread] }, { type: "event", event: delta(1, "A") });
    expect(reducer(state, { type: "event", event: delta(1, "B") })).toBe(state);
  });
});
