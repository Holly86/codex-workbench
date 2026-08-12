import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../auth/api";
import { readSystemStatus, restartRuntime } from "../workbench/api";
import type { ComponentStatus, SystemStatus } from "../workbench/types";
import { DefinitionList, EmptyState, InlineAlert, StatusBadge } from "./ui";

const COMPONENT_LABELS: Record<ComponentStatus["component"], string> = {
  backend_http: "Backend HTTP API",
  codex_app_server: "Codex App Server",
  realtime_websocket: "Realtime WebSocket",
  sqlite_metadata: "SQLite / Metadaten",
  repository_root: "Repository-Speicher",
  upload_storage: "Upload-Speicher",
  background_jobs: "Hintergrundaufträge",
  plugin_discovery: "Plugin-Erkennung (optional)",
};

export function SystemStatusDrawer({ open, csrfToken, realtimeState, onClose, onReconnect, onReloadRepositories }: {
  open: boolean; csrfToken: string; realtimeState: string; onClose: () => void;
  onReconnect: () => void; onReloadRepositories: () => Promise<void>;
}) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [restartPending, setRestartPending] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(null);
    try { setStatus(await readSystemStatus(signal)); }
    catch (caught) { if (!signal?.aborted) setError(statusError(caught)); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, []);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const loadTimer = window.setTimeout(() => void refresh(controller.signal), 0);
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    window.setTimeout(() => closeRef.current?.focus());
    return () => { window.clearTimeout(loadTimer); controller.abort(); window.removeEventListener("keydown", escape); };
  }, [open, onClose, refresh]);
  const restart = async () => {
    if (!confirmRestart || restartPending) return;
    setRestartPending(true); setError(null);
    try { await restartRuntime(csrfToken); setConfirmRestart(false); await refresh(); }
    catch (caught) {
      setError(caught instanceof ApiError && caught.code === "operation_conflict"
        ? "Ein aktiver Turn wird nicht unterbrochen. Beende ihn vor dem Runtime-Neustart."
        : statusError(caught));
    } finally { setRestartPending(false); }
  };
  return <>
    {open ? <button type="button" className="drawer-scrim" aria-label="Systemstatus schließen" onClick={onClose} /> : null}
    <aside className={`system-status-drawer ${open ? "is-open" : ""}`} aria-hidden={!open} aria-label="Systemstatus">
      <header><div><p className="eyebrow">Workbench</p><h2>Systemstatus</h2></div><button ref={closeRef} type="button" className="icon-button" aria-label="Systemstatus schließen" onClick={onClose}>×</button></header>
      <div className="drawer-scroll">
        {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
        {status === null && loading ? <p role="status">Komponenten werden geprüft …</p> : null}
        {status === null && !loading ? <EmptyState>Der Systemstatus ist noch nicht verfügbar.</EmptyState> : null}
        {status ? <>
          <div className="system-summary"><StatusBadge state={status.status}>{stateLabel(status.status)}</StatusBadge><span>Geprüft {formatTime(status.observedAt)}</span></div>
          <DefinitionList values={[{ label: "Workbench", value: status.workbenchVersion }, { label: "Codex", value: status.codexVersion ?? "nicht verfügbar" }, { label: "Browser-Realtime", value: stateLabel(realtimeState) }]} />
          <div className="component-status-list">{status.components.map((component) => <article key={component.component}>
            <header><strong>{COMPONENT_LABELS[component.component]}</strong><StatusBadge state={component.state}>{stateLabel(component.state)}</StatusBadge></header>
            <ComponentDetails component={component} />
          </article>)}</div>
        </> : null}
        <section className="recovery-actions" aria-labelledby="recovery-heading"><h3 id="recovery-heading">Kontrollierte Wiederherstellung</h3>
          <button type="button" onClick={() => void refresh()} disabled={loading}>{loading ? "Prüfung läuft …" : "Status jetzt prüfen"}</button>
          <button type="button" onClick={onReconnect}>Realtime neu verbinden</button>
          <button type="button" onClick={() => void onReloadRepositories()}>Repositoryliste neu laden</button>
          <label className="checkbox-row"><input type="checkbox" checked={confirmRestart} onChange={(event) => setConfirmRestart(event.target.checked)} /><span>Ich bestätige: Codex Runtime neu starten. Aktive Turns werden nicht unterbrochen.</span></label>
          <button type="button" className="warning-action" disabled={!confirmRestart || restartPending} onClick={() => void restart()}>{restartPending ? "Neustart wird angefordert …" : "Codex Runtime neu starten"}</button>
        </section>
      </div>
    </aside>
  </>;
}

function ComponentDetails({ component }: { component: ComponentStatus }) {
  const details: string[] = [];
  if (!component.enabled) details.push("Deaktiviert");
  if (component.generation !== null) details.push(`Generation ${String(component.generation)}`);
  if (component.restartAttempts !== null && component.restartLimit !== null) details.push(`${String(component.restartAttempts)} von ${String(component.restartLimit)} Recovery-Versuchen`);
  if (component.connectionCount !== null) details.push(`${String(component.connectionCount)} aktive Verbindung(en)`);
  if (component.activeCount !== null) details.push(`${String(component.activeCount)} aktiv, ${String(component.queuedCount ?? 0)} ausstehend, ${String(component.failedCount ?? 0)} fehlgeschlagen`);
  if (component.errorCode) details.push(errorLabel(component.errorCode));
  if (component.lastSuccessAt) details.push(`Zuletzt bereit ${formatTime(component.lastSuccessAt)}`);
  if (component.lastRecoveryAt) details.push(`Letzte Recovery ${formatTime(component.lastRecoveryAt)}`);
  if (component.nextRecoveryAt) details.push(`Nächster Versuch ${formatTime(component.nextRecoveryAt)}`);
  if (details.length === 0) details.push(component.state === "healthy" ? "Komponente antwortet wie erwartet." : "Noch keine zusätzlichen Details.");
  return <p>{details.join(" · ")}</p>;
}
function stateLabel(state: string): string { return ({ healthy: "Gesund", degraded: "Eingeschränkt", recovering: "Wiederherstellung", blocked: "Blockiert", failed: "Fehlgeschlagen", unknown: "Unbekannt", connected: "Verbunden", reconnecting: "Verbindet neu", connecting: "Verbindet", disconnected: "Offline" } as Record<string, string>)[state] ?? state; }
function errorLabel(code: string): string { return ({ watchdog_unresponsive: "Runtime antwortete wiederholt nicht", restart_limit_reached: "Recovery-Limit erreicht", storage_capacity_low: "Freier Speicher wird knapp", storage_unavailable: "Speicher nicht verfügbar", database_quick_check_failed: "Datenbankprüfung fehlgeschlagen", plugin_discovery_degraded: "Optionale Plugin-Erkennung eingeschränkt" } as Record<string, string>)[code] ?? `Fehlercode: ${code}`; }
function formatTime(value: number): string { return new Date(value * 1000).toLocaleString(); }
function statusError(caught: unknown): string {
  if (caught instanceof ApiError) {
    return ({
      operation_conflict: "Ein aktiver Vorgang verhindert diese Recovery-Aktion.",
      rate_limited: "Zu viele Recovery-Anfragen. Bitte kurz warten.",
      runtime_unavailable: "Die Runtime-Recovery ist derzeit nicht verfügbar.",
      csrf_failed: "Die Sicherheitsprüfung ist abgelaufen. Bitte neu anmelden.",
    } as Record<string, string>)[caught.code] ?? "Der sichere Systemstatus konnte nicht geladen werden.";
  }
  return "Der sichere Systemstatus konnte nicht geladen werden.";
}
