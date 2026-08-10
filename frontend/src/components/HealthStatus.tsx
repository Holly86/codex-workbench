import { useEffect, useState } from "react";

import type { WorkbenchCapabilities } from "../workbench/types";

type BackendState = "checking" | "connected" | "unavailable";
type CapabilityStatus = WorkbenchCapabilities["status"];

interface HealthStatusProps {
  processConnection: string;
  capabilities: CapabilityStatus | null;
}

export function HealthStatus({ processConnection, capabilities }: HealthStatusProps) {
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

  return (
    <div className="health-status" role="status" aria-live="polite">
      <span className="status-dot" data-state={backend} aria-hidden="true" />
      <span>{backendLabel(backend)}</span>
      <span>{runtimeLabel(backend, runtimeReady, processConnection)}</span>
      <span>{capabilityLabel(capabilities)}</span>
    </div>
  );
}

function backendLabel(state: BackendState): string {
  if (state === "checking") return "Backend wird geprüft";
  return state === "connected" ? "Backend verbunden" : "Backend nicht erreichbar";
}

function runtimeLabel(backend: BackendState, ready: boolean, connection: string): string {
  if (backend !== "connected") return "Codex Runtime: unbekannt";
  if (connection === "reconnecting") return "Codex wird neu verbunden …";
  if (ready && connection === "connected") return "Codex Runtime bereit";
  return "Codex Runtime derzeit nicht verfügbar";
}

function capabilityLabel(status: CapabilityStatus | null): string {
  if (status === null) return "Capabilities werden geladen";
  if (status.overall === "ready") return "Capabilities bereit";
  if (!status.plugins.available && status.models.available) {
    return "Plugins konnten nicht geladen werden";
  }
  return status.overall === "partial"
    ? "Capabilities teilweise verfügbar"
    : "Capabilities nicht verfügbar";
}
