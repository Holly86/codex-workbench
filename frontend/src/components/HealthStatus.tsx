import { useEffect, useState } from "react";

type ConnectionState = "checking" | "ready" | "unavailable";

const labels: Record<ConnectionState, string> = {
  checking: "Verbindung wird geprüft",
  ready: "Backend bereit",
  unavailable: "Backend nicht erreichbar",
};

export function HealthStatus() {
  const [state, setState] = useState<ConnectionState>("checking");

  useEffect(() => {
    const controller = new AbortController();

    const checkHealth = async () => {
      try {
        const response = await fetch("/api/health/ready", {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        setState(response.ok ? "ready" : "unavailable");
      } catch {
        if (!controller.signal.aborted) {
          setState("unavailable");
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
      <span className="status-dot" data-state={state} aria-hidden="true" />
      <span>{labels[state]}</span>
    </div>
  );
}
