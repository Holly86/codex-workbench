export type RecoveryKind = "runtime" | "realtime";

export interface RecoveryEpisode {
  id: string;
  kind: RecoveryKind;
  state: "recovering" | "failed";
}

export interface RecoveryEpisodeState {
  runtime: RecoveryEpisode | null;
  realtime: RecoveryEpisode | null;
  nextOrdinal: number;
}

export const initialRecoveryEpisodeState: RecoveryEpisodeState = {
  runtime: null,
  realtime: null,
  nextOrdinal: 1,
};

export function advanceRecoveryEpisodes(
  episodes: RecoveryEpisodeState,
  previous: { runtime: string; realtime: string },
  next: { runtime: string; realtime: string },
): RecoveryEpisodeState {
  let nextOrdinal = episodes.nextOrdinal;
  const runtime = advanceEpisode(
    episodes.runtime,
    isRuntimeHealthy(previous.runtime),
    isRuntimeHealthy(next.runtime),
    runtimeState(next.runtime),
    "runtime",
    () => nextOrdinal++,
  );
  const realtime = advanceEpisode(
    episodes.realtime,
    previous.realtime === "connected",
    next.realtime === "connected",
    next.realtime === "disconnected" ? "failed" : "recovering",
    "realtime",
    () => nextOrdinal++,
  );
  return runtime === episodes.runtime && realtime === episodes.realtime
    ? episodes
    : { runtime, realtime, nextOrdinal };
}

export function activeRecoveryEpisodes(state: RecoveryEpisodeState): RecoveryEpisode[] {
  return [state.runtime, state.realtime].filter(
    (episode): episode is RecoveryEpisode => episode !== null,
  );
}

function advanceEpisode(
  current: RecoveryEpisode | null,
  wasHealthy: boolean,
  isHealthy: boolean,
  state: RecoveryEpisode["state"],
  kind: RecoveryKind,
  allocateOrdinal: () => number,
): RecoveryEpisode | null {
  if (isHealthy) return null;
  if (current !== null) return current.state === state ? current : { ...current, state };
  if (!wasHealthy) return null;
  const ordinal = allocateOrdinal();
  return { id: `recovery_${ordinal.toString(36).padStart(6, "0")}`, kind, state };
}

function isRuntimeHealthy(state: string): boolean {
  return state === "ready" || state === "connected";
}

function runtimeState(state: string): RecoveryEpisode["state"] {
  return state === "reconnecting" || state === "recovering" ? "recovering" : "failed";
}
