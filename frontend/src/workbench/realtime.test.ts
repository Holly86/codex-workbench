import { describe, expect, it } from "vitest";
import { initialRealtimeState, reconnectDelay, reducer, type EventEnvelope } from "./realtime";
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

  it("removes only the deleted thread and its transient state", () => {
    const keep = { ...thread, id: "thread_2", activeTurnId: null, turns: [] };
    const state = {
      ...initialRealtimeState,
      threads: [thread, keep],
      timeline: { [thread.id]: [], [keep.id]: [] },
    };
    const next = reducer(state, {
      type: "event",
      event: { sequence: 1, occurredAt: "2026-01-01T00:00:00Z", type: "thread.deleted",
        payload: { threadId: thread.id, repositoryId: thread.repositoryId },
        threadId: thread.id, turnId: null, itemId: null },
    });
    expect(next.threads).toEqual([keep]);
    expect(next.timeline).toEqual({ [keep.id]: [] });
  });

  it("keeps 100 reconnect delays jittered and bounded without a retry loop", () => {
    const values = Array.from({ length: 100 }, (_, index) => reconnectDelay(index + 1, index / 99));
    expect(values.every((value) => value >= 850 && value <= 17_250)).toBe(true);
    expect(new Set(values.slice(-20)).size).toBeGreaterThan(1);
  });

  it("reconciles 100 separate reconnect snapshots without duplicating state", () => {
    let state = { ...initialRealtimeState, threads: [thread] };
    for (let cycle = 1; cycle <= 100; cycle += 1) {
      state = reducer(state, { type: "socket", state: "reconnecting" });
      state = reducer(state, {
        type: "snapshot",
        snapshot: {
          connection: { state: "connected", generation: cycle, message: null },
          threads: [thread],
          repositoryActivity: [{ repositoryId: "repo_1", activeTurnCount: 1 }],
          pendingApprovals: [],
          rateLimits: { available: false, observedAt: cycle },
          jobs: [],
        },
        latest: cycle,
        missed: true,
      });
    }
    expect(state.connection).toBe("connected");
    expect(state.generation).toBe(100);
    expect(state.lastSequence).toBe(100);
    expect(state.threads).toEqual([thread]);
    expect(state.jobs).toEqual([]);
    expect(state.repositoryActivity).toEqual({ repo_1: 1 });
  });

  it("tracks authoritative activity for multiple repositories and parallel turns", () => {
    const activityEvent = (
      sequence: number, repositoryId: string, activeTurnCount: number,
    ): EventEnvelope => ({
      sequence,
      occurredAt: "2026-01-01T00:00:00Z",
      type: "repository.activity",
      payload: { repositoryId, activeTurnCount },
      threadId: null,
      turnId: null,
      itemId: null,
    });
    let state = reducer(initialRealtimeState, {
      type: "event", event: activityEvent(1, "repo_1", 2),
    });
    state = reducer(state, {
      type: "event", event: activityEvent(2, "repo_2", 1),
    });
    state = reducer(state, {
      type: "event", event: activityEvent(3, "repo_1", 1),
    });
    expect(state.repositoryActivity).toEqual({ repo_1: 1, repo_2: 1 });

    state = reducer(state, {
      type: "event", event: activityEvent(4, "repo_1", 0),
    });
    expect(state.repositoryActivity).toEqual({ repo_1: 0, repo_2: 1 });
  });

  it("replaces stale repository activity from a reconnect snapshot", () => {
    const state = reducer(
      { ...initialRealtimeState, repositoryActivity: { stale: 3 } },
      {
        type: "snapshot",
        snapshot: {
          connection: { state: "connected", generation: 2, message: null },
          threads: [],
          repositoryActivity: [{ repositoryId: "unopened", activeTurnCount: 1 }],
          pendingApprovals: [],
          rateLimits: { available: false, observedAt: 2 },
          jobs: [],
        },
        latest: 7,
        missed: true,
      },
    );
    expect(state.repositoryActivity).toEqual({ unopened: 1 });
    expect(state.threads).toEqual([]);
  });

  it("creates a new realtime recovery episode after reconnect in the same generation", () => {
    let state = reducer(initialRealtimeState, { type: "socket", state: "connected" });
    state = reducer(state, { type: "socket", state: "disconnected" });
    const first = state.recoveryEpisodes.realtime;
    expect(first?.state).toBe("failed");
    state = reducer(state, { type: "socket", state: "reconnecting" });
    expect(state.recoveryEpisodes.realtime?.id).toBe(first?.id);
    expect(state.recoveryEpisodes.realtime?.state).toBe("recovering");
    state = reducer(state, { type: "socket", state: "connected" });
    expect(state.recoveryEpisodes.realtime).toBeNull();
    state = reducer(state, { type: "socket", state: "disconnected" });
    expect(state.generation).toBe(0);
    expect(state.recoveryEpisodes.realtime?.id).not.toBe(first?.id);
  });

  it("creates a new runtime episode after recovery without changing generation", () => {
    const connected = {
      ...initialRealtimeState,
      processConnection: "ready",
      connection: "connected" as const,
    };
    const failedEvent = (sequence: number, state: string): EventEnvelope => ({
      sequence,
      occurredAt: "2026-01-01T00:00:00Z",
      type: "connection.state",
      payload: { state, generation: 7 },
      threadId: null,
      turnId: null,
      itemId: null,
    });
    let state = reducer(connected, { type: "event", event: failedEvent(1, "failed") });
    const first = state.recoveryEpisodes.runtime;
    state = reducer(state, { type: "event", event: failedEvent(2, "ready") });
    expect(state.recoveryEpisodes.runtime).toBeNull();
    state = reducer(state, { type: "event", event: failedEvent(3, "recovering") });
    expect(state.generation).toBe(7);
    expect(state.recoveryEpisodes.runtime?.id).not.toBe(first?.id);
  });

  it("projects a reproducible long conversation batch without losing final text", () => {
    const state = { ...initialRealtimeState, threads: [thread] };
    const events = Array.from({ length: 5_000 }, (_, index) => delta(index + 1, "x"));
    const started = performance.now();
    const next = reducer(state, { type: "events", events });
    const duration = performance.now() - started;
    expect(next.lastSequence).toBe(5_000);
    expect(next.threads[0]?.turns[0]?.items[0]?.text).toHaveLength(5_000);
    expect(duration).toBeLessThan(2_000);
  });

  it("applies a persisted thread title update in realtime", () => {
    const next = reducer({ ...initialRealtimeState, threads: [thread] }, {
      type: "event",
      event: {
        sequence: 1, occurredAt: "2026-01-01T00:00:00Z", type: "thread.name",
        payload: { name: "Runtime Recovery absichern", titleSource: "manual",
          titleUpdatedAt: 2, titleGenerationState: "succeeded", titleErrorCode: null },
        threadId: thread.id, turnId: null, itemId: null,
      },
    });
    expect(next.threads[0]?.name).toBe("Runtime Recovery absichern");
    expect(next.threads[0]?.titleSource).toBe("manual");
  });
});
