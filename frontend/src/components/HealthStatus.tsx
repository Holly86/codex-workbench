import { useEffect, useState } from "react";

type BackendState = "checking" | "connected" | "unavailable";

interface HealthStatusProps {
  processConnection: string;
  realtimeConnection?: string;
}

export function HealthStatus({ processConnection, realtimeConnection = "connected" }: HealthStatusProps) {
  const [backend, setBackend] = useState<BackendState>("checking");
  const [runtimeReady, setRuntimeReady] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    const checkHealth = async () => {
      try {
        const live = await fetch("/api/health/live", {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!live.ok) {
          setBackend("unavailable");
          setRuntimeReady(false);
          return;
        }
        setBackend("connected");
        const ready = await fetch("/api/health/ready", {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        setRuntimeReady(ready.ok);
      } catch {
        if (!controller.signal.aborted) {
          setBackend("unavailable");
          setRuntimeReady(false);
        }
      }
    };

    void checkHealth();
    const timer = window.setInterval(() => {
      void checkHealth();
    }, 30_000);

    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  const backendText = backendLabel(backend);
  const runtimeText = runtimeLabel(backend, runtimeReady, processConnection, realtimeConnection);
  return (
    <div className="health-status" role="status" aria-live="polite" aria-label={`${backendText}. ${runtimeText}.`}>
      <span className="status-chip" data-state={backend === "connected" ? "positive" : backend === "checking" ? "pending" : "negative"} title={backendText}>
        <span className="status-dot" aria-hidden="true" /><span className="status-key">Backend</span><span className="status-value">{backendText}</span><span className="status-compact">{backend === "connected" ? "Verbunden" : backend === "checking" ? "Prüfung" : "Offline"}</span>
      </span>
      <span className="status-chip" data-state={runtimeTone(backend, runtimeReady, processConnection, realtimeConnection)} title={runtimeText}>
        <span className="status-dot" aria-hidden="true" /><span className="status-key">Runtime</span><span className="status-value">{runtimeText}</span><span className="status-compact">{processConnection === "connected" && runtimeReady ? "Bereit" : processConnection === "reconnecting" || realtimeConnection !== "connected" ? "Verbindet" : "Offline"}</span>
      </span>
    </div>
  );
}

function backendLabel(state: BackendState): string {
  if (state === "checking") return "Backend wird geprüft";
  return state === "connected" ? "Backend verbunden" : "Backend nicht erreichbar";
}

function runtimeLabel(backend: BackendState, ready: boolean, connection: string, realtime: string): string {
  if (backend !== "connected") return "Codex Runtime: unbekannt";
  if (realtime !== "connected") return "Realtime wird verbunden …";
  if (connection === "reconnecting") return "Codex wird neu verbunden …";
  if (ready && connection === "connected") return "Codex Runtime bereit";
  return "Codex Runtime derzeit nicht verfügbar";
}

function runtimeTone(backend: BackendState, ready: boolean, connection: string, realtime: string): "positive" | "pending" | "negative" {
  if (backend !== "connected" || (!ready && connection !== "reconnecting")) return "negative";
  if (realtime !== "connected" || connection === "reconnecting") return "pending";
  return connection === "connected" ? "positive" : "negative";
}
