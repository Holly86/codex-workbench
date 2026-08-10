import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { removeUpload, uploadImage, type UploadOperation } from "../workbench/api";
import type { ApprovalPolicy, SandboxMode, UploadedImage, WorkbenchCapabilities } from "../workbench/types";
import { AnchoredMenu } from "./AnchoredMenu";

interface PendingAttachment {
  key: string;
  file: File;
  previewUrl: string;
  progress: number;
  state: "uploading" | "ready" | "error" | "cancelled";
  upload: UploadedImage | null;
  operation: UploadOperation | null;
  error: string | null;
}

interface ComposerProps {
  csrfToken: string;
  disabled: boolean;
  working: boolean;
  onSend: (message: string, uploads: UploadedImage[]) => Promise<boolean>;
  onStop: () => Promise<void>;
  capabilities?: WorkbenchCapabilities | null;
  model?: string | null;
  effort?: string | null;
  sandboxMode?: SandboxMode | null;
  approvalPolicy?: ApprovalPolicy;
  accessMessage?: string | null;
  onModelChange?: (model: string, effort: string | null) => void;
  onAccessChange?: (sandbox: SandboxMode, approval: ApprovalPolicy) => void;
}

function attachmentKey(): string {
  return crypto.randomUUID();
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.name !== "AbortError"
    ? error.message
    : "Upload abgebrochen.";
}

export function Composer({ csrfToken, disabled, working, onSend, onStop, capabilities, model,
  effort, sandboxMode = null, approvalPolicy = "on-request", accessMessage = null,
  onModelChange = () => undefined, onAccessChange = () => undefined }: ComposerProps) {
  const inputId = useId();
  const pickerId = useId();
  const accessMessageId = useId();
  const [message, setMessage] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [openMenu, setOpenMenu] = useState<"add" | "model" | "reasoning" | "access" | "skills" | null>(null);
  const attachmentsRef = useRef(attachments);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const modelRef = useRef<HTMLButtonElement>(null);
  const reasoningRef = useRef<HTMLButtonElement>(null);
  const accessRef = useRef<HTMLButtonElement>(null);
  const skillsRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  useEffect(
    () => () => {
      for (const attachment of attachmentsRef.current) {
        attachment.operation?.cancel();
        URL.revokeObjectURL(attachment.previewUrl);
      }
    },
    [],
  );

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (textarea === null) return;
    textarea.style.height = "auto";
    textarea.style.height = `${String(Math.min(textarea.scrollHeight, 208))}px`;
  }, [message]);

  const addFiles = (files: File[]) => {
    for (const file of files) {
      if (!file.type.startsWith("image/")) {
        setSubmitError(`${file.name}: Nur Bilddateien können angehängt werden.`);
        continue;
      }
      const key = attachmentKey();
      const previewUrl = URL.createObjectURL(file);
      const initial: PendingAttachment = {
        key,
        file,
        previewUrl,
        progress: 0,
        state: "uploading",
        upload: null,
        operation: null,
        error: null,
      };
      setAttachments((current) => [...current, initial]);
      const operation = uploadImage(file, csrfToken, (loaded, total) => {
        const progress = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
        setAttachments((current) =>
          current.map((item) => (item.key === key ? { ...item, progress } : item)),
        );
      });
      setAttachments((current) =>
        current.map((item) => (item.key === key ? { ...item, operation } : item)),
      );
      void operation.promise
        .then((upload) => {
          setAttachments((current) =>
            current.map((item) =>
              item.key === key
                ? { ...item, state: "ready", progress: 100, upload, operation: null }
                : item,
            ),
          );
        })
        .catch((error: unknown) => {
          setAttachments((current) =>
            current.map((item) =>
              item.key === key
                ? {
                    ...item,
                    state: error instanceof Error && error.name === "AbortError" ? "cancelled" : "error",
                    operation: null,
                    error: errorMessage(error),
                  }
                : item,
            ),
          );
        });
    }
  };

  const removeAttachment = (key: string) => {
    const attachment = attachments.find((item) => item.key === key);
    if (attachment === undefined) return;
    attachment.operation?.cancel();
    URL.revokeObjectURL(attachment.previewUrl);
    setAttachments((current) => current.filter((item) => item.key !== key));
    if (attachment.upload !== null) {
      void removeUpload(csrfToken, attachment.upload.id).catch(() => {
        // Expiry cleanup remains authoritative if the immediate removal fails.
      });
    }
  };

  const handleSubmit = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const ready = attachments.filter((item) => item.state === "ready" && item.upload !== null);
    if (
      message.trim().length === 0 ||
      attachments.some((item) => item.state === "uploading") ||
      disabled ||
      working
    ) return;
    setSubmitError(null);
    const sent = await onSend(
      message.trim(),
      ready.flatMap((item) => (item.upload === null ? [] : [item.upload])),
    );
    if (sent) {
      setMessage("");
      for (const attachment of attachments) URL.revokeObjectURL(attachment.previewUrl);
      setAttachments([]);
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (files.length > 0) {
      event.preventDefault();
      addFiles(files);
    }
  };

  const handleDrop = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault();
    addFiles(Array.from(event.dataTransfer.files));
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const uploading = attachments.some((item) => item.state === "uploading");
  const canSend = message.trim().length > 0 && !disabled && !working && !uploading;
  const selectedModel = capabilities?.models.find((item) => item.id === model)
    ?? capabilities?.models.find((item) => item.isDefault) ?? null;
  const selectedEffort = selectedModel?.reasoningEfforts.find((item) => item.value === effort)
    ?? selectedModel?.reasoningEfforts.find((item) => item.value === selectedModel.defaultReasoningEffort)
    ?? selectedModel?.reasoningEfforts[0] ?? null;
  const capabilitiesLoading = capabilities === null || capabilities === undefined;
  const accessLabel = capabilitiesLoading
    ? "Lädt …"
    : sandboxMode === "read-only"
      ? "Read only"
      : sandboxMode === "workspace-write"
        ? "Workspace"
        : sandboxMode === "danger-full-access"
          ? "Full"
          : "Auswahl erforderlich";

  return (
    <form
      className="composer"
      aria-label="Nachricht verfassen"
      onSubmit={(event) => void handleSubmit(event)}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      {attachments.length > 0 ? (
        <div className="attachment-list" aria-label="Anhänge">
          {attachments.map((attachment) => (
            <article className={`attachment-chip is-${attachment.state}`} key={attachment.key}>
              <img src={attachment.previewUrl} alt="Vorschau des Anhangs" />
              <div>
                <strong>{attachment.file.name}</strong>
                <span>{formatBytes(attachment.file.size)} · {attachment.state === "ready" ? "Bereit" : attachment.state === "uploading" ? `${attachment.progress} %` : attachment.error}</span>
                {attachment.state === "uploading" ? (
                  <progress value={attachment.progress} max={100} aria-label={`Upload ${attachment.file.name}`} />
                ) : null}
              </div>
              <button
                type="button"
                className="icon-action"
                aria-label={`${attachment.file.name} entfernen${attachment.state === "uploading" ? " und Upload abbrechen" : ""}`}
                onClick={() => removeAttachment(attachment.key)}
              >
                ×
              </button>
            </article>
          ))}
        </div>
      ) : null}
      <label className="sr-only" htmlFor={inputId}>Nachricht an Codex</label>
      <textarea
        id={inputId}
        ref={textareaRef}
        value={message}
        disabled={disabled}
        onChange={(event) => setMessage(event.target.value)}
        onPaste={handlePaste}
        onKeyDown={handleKeyDown}
        placeholder={disabled ? "Wähle oder erstelle zuerst einen Thread." : "Beschreibe eine Aufgabe …"}
        rows={1}
      />
      {submitError !== null ? <p className="inline-error" role="alert">{submitError}</p> : null}
      <div className="composer-actions">
        <input
          className="sr-only"
          id={pickerId}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          disabled={disabled}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            addFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
            setOpenMenu(null);
          }}
        />
        <button
          ref={addRef}
          type="button"
          className="composer-control icon-control"
          aria-label="Anhang hinzufügen"
          aria-haspopup="menu"
          aria-expanded={openMenu === "add"}
          onClick={() => setOpenMenu(openMenu === "add" ? null : "add")}
        >
          <span aria-hidden="true">＋</span>
        </button>
        <div className="composer-control-group" aria-label="Modell und Reasoning">
          <button
            ref={modelRef}
            type="button"
            className="composer-control model-control"
            aria-label={`Model: ${selectedModel?.displayName ?? (capabilitiesLoading ? "wird geladen" : "nicht verfügbar")}`}
            aria-haspopup="menu"
            aria-expanded={openMenu === "model"}
            onClick={() => setOpenMenu(openMenu === "model" ? null : "model")}
          >
            <span className="control-kicker">Model</span>
            <span className="control-value">{selectedModel?.displayName ?? (capabilitiesLoading ? "Lädt …" : "Nicht verfügbar")}</span>
            <span className="control-chevron" aria-hidden="true">⌄</span>
          </button>
          <button
            ref={reasoningRef}
            type="button"
            className="composer-control reasoning-control"
            aria-label={`Reasoning: ${selectedEffort?.value ?? "Standard"}`}
            aria-haspopup="menu"
            aria-expanded={openMenu === "reasoning"}
            disabled={selectedModel === null || selectedModel.reasoningEfforts.length === 0}
            onClick={() => setOpenMenu(openMenu === "reasoning" ? null : "reasoning")}
          >
            <span className="control-kicker">Reasoning</span>
            <span className="control-value">{selectedEffort?.value ?? "Standard"}</span>
            <span className="control-chevron" aria-hidden="true">⌄</span>
          </button>
        </div>
        <button
          ref={accessRef}
          type="button"
          className={`composer-control access-control ${sandboxMode === "danger-full-access" ? "danger-selection" : ""} ${sandboxMode === null ? "is-required" : ""}`}
          aria-label={`Access: ${accessLabel}`}
          aria-describedby={accessMessage !== null ? accessMessageId : undefined}
          data-invalid={sandboxMode === null && !capabilitiesLoading ? "true" : undefined}
          aria-haspopup="menu"
          aria-expanded={openMenu === "access"}
          onClick={() => setOpenMenu(openMenu === "access" ? null : "access")}
        >
          <span className="control-kicker">Access</span>
          <span className="control-value">{accessLabel}</span>
          <span className="control-chevron" aria-hidden="true">⌄</span>
        </button>
        <button
          ref={skillsRef}
          type="button"
          className="composer-control skills-control"
          aria-label={`Skills und Plugins: ${String(capabilities?.skills.length ?? 0)}`}
          aria-haspopup="dialog"
          aria-expanded={openMenu === "skills"}
          onClick={() => setOpenMenu(openMenu === "skills" ? null : "skills")}
        >
          <span className="control-kicker">Tools</span>
          <span className="control-value">Skills</span>
          <span className="control-count">{capabilities?.skills.length ?? 0}</span>
        </button>
        <span className="composer-hint">Enter senden · Shift+Enter Zeilenumbruch</span>
        {working ? (
          <button type="button" className="stop-action composer-submit" onClick={() => void onStop()}><span aria-hidden="true">■</span><span className="sr-only">Turn abbrechen</span></button>
        ) : (
          <button type="submit" className="primary-action composer-submit" disabled={!canSend}><span aria-hidden="true">↑</span><span className="sr-only">Senden</span></button>
        )}
      </div>
      {accessMessage !== null ? <p id={accessMessageId} className="composer-access-message" role="status">{accessMessage}</p> : null}
      <AnchoredMenu anchorRef={addRef} open={openMenu === "add"} onClose={() => setOpenMenu(null)} ariaLabel="Hinzufügen" preferredPlacement="top">
        <p className="menu-heading">Hinzufügen</p>
        <label className="menu-item" htmlFor={pickerId}>
          <strong>Dateien oder Bilder</strong><span>PNG, JPEG oder WebP anhängen</span>
        </label>
        <div className="menu-static is-muted"><strong>Repository-Kontext</strong><span>Wird automatisch bereitgestellt</span></div>
      </AnchoredMenu>
      <AnchoredMenu anchorRef={modelRef} open={openMenu === "model"} onClose={() => setOpenMenu(null)} ariaLabel="Modell auswählen" width={380} preferredPlacement="top">
        <p className="menu-heading">Model</p>
        <p className="menu-description">Wähle das Modell für den nächsten Turn.</p>
        {capabilities?.models.map((item) => (
          <button type="button" role="menuitemradio" aria-checked={item.id === selectedModel?.id} key={item.id} onClick={() => {
            const retainedEffort = item.reasoningEfforts.some((option) => option.value === effort)
              ? effort ?? item.defaultReasoningEffort
              : item.defaultReasoningEffort;
            onModelChange(item.id, retainedEffort);
            setOpenMenu(null);
          }}>
            <span className="menu-selection-mark" aria-hidden="true">{item.id === selectedModel?.id ? "✓" : ""}</span>
            <span className="menu-copy"><strong>{item.displayName}</strong><span>{item.description}</span></span>
          </button>
        ))}
        {!capabilitiesLoading && capabilities?.models.length === 0 ? <p className="menu-empty">Keine Modelle verfügbar.</p> : null}
      </AnchoredMenu>
      <AnchoredMenu anchorRef={reasoningRef} open={openMenu === "reasoning"} onClose={() => setOpenMenu(null)} ariaLabel="Reasoning auswählen" width={336} preferredPlacement="top">
        <p className="menu-heading">Reasoning</p>
        <p className="menu-description">Steuert, wie gründlich {selectedModel?.displayName ?? "das Modell"} analysiert.</p>
        {selectedModel?.reasoningEfforts.map((item) => (
          <button type="button" role="menuitemradio" aria-checked={item.value === selectedEffort?.value} key={item.value} onClick={() => {
            onModelChange(selectedModel.id, item.value);
            setOpenMenu(null);
          }}>
            <span className="menu-selection-mark" aria-hidden="true">{item.value === selectedEffort?.value ? "✓" : ""}</span>
            <span className="menu-copy"><strong>{reasoningLabel(item.value)}</strong><span>{item.description}</span></span>
          </button>
        ))}
      </AnchoredMenu>
      <AnchoredMenu anchorRef={accessRef} open={openMenu === "access"} onClose={() => setOpenMenu(null)} ariaLabel="Zugriff auswählen" width={352} preferredPlacement="top" align="end">
        <p className="menu-heading">Arbeitszugriff</p>
        {([['read-only','Read only','Lesen, nichts verändern'],['workspace-write','Workspace','Änderungen innerhalb des Repositorys'],['danger-full-access','Full access','Uneingeschränkter Agentenzugriff']] as const).map(([value,label,help]) => {
          const key = value === "read-only" ? "readOnly" : value === "workspace-write" ? "workspaceWrite" : "fullAccess";
          const available = capabilities?.host?.sandbox[key].available === true;
          const reason = capabilities?.host?.sandbox[key].reason;
          return <button type="button" role="menuitemradio" aria-checked={sandboxMode === value} key={value} disabled={!available} onClick={() => {
            onAccessChange(value, approvalPolicy);
            setOpenMenu(null);
          }}>
            <span className="menu-selection-mark" aria-hidden="true">{sandboxMode === value ? "✓" : ""}</span>
            <span className="menu-copy"><strong>{label}</strong><span>{available ? help : capabilitiesLoading ? "Capabilities werden geladen …" : reason ?? "Auf diesem Host nicht verfügbar"}</span></span>
          </button>;
        })}
        <div className="menu-separator" role="separator" />
        <p className="menu-heading">Freigaben</p>
        {(['untrusted','on-request','never'] as const).map((value) => (
          <button type="button" role="menuitemradio" aria-checked={approvalPolicy === value} key={value} disabled={sandboxMode === null} onClick={() => {
            if (sandboxMode !== null) onAccessChange(sandboxMode, value);
            setOpenMenu(null);
          }}>
            <span className="menu-selection-mark" aria-hidden="true">{approvalPolicy === value ? "✓" : ""}</span>
            <span className="menu-copy"><strong>{approvalLabel(value)}</strong><span>{approvalDescription(value)}</span></span>
          </button>
        ))}
      </AnchoredMenu>
      <AnchoredMenu anchorRef={skillsRef} open={openMenu === "skills"} onClose={() => setOpenMenu(null)} ariaLabel="Skills und Plugins" width={400} preferredPlacement="top" align="end" role="dialog">
        <div className="menu-panel-header"><div><p className="menu-heading">Skills &amp; Plugins</p><p className="menu-description">Für dieses Repository erkannte Erweiterungen.</p></div><button type="button" className="menu-close" aria-label="Menü schließen" onClick={() => setOpenMenu(null)}>×</button></div>
        <div className="capability-menu-list">
          {capabilities?.skills.map((item) => <div className="menu-static" key={item.name}><strong>{item.name}</strong><span>{item.description}</span></div>)}
          {capabilities?.plugins.map((item) => <div className="menu-static" key={item.id}><strong>{item.name}</strong><span>{item.installed ? "Plugin installiert" : "Plugin nicht installiert"}</span></div>)}
          {!capabilities?.native.apps ? <div className="menu-static is-muted"><strong>Apps</strong><span>Nicht aktiviert</span></div> : null}
          {capabilitiesLoading ? <p className="menu-empty">Capabilities werden geladen …</p> : null}
        </div>
      </AnchoredMenu>
    </form>
  );
}

function reasoningLabel(value: string): string {
  const labels: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high" };
  return labels[value] ?? value;
}

function approvalLabel(value: ApprovalPolicy): string {
  if (value === "untrusted") return "Bei nicht vertrauenswürdigen Aktionen";
  if (value === "on-request") return "Auf Anfrage";
  return "Nie nachfragen";
}

function approvalDescription(value: ApprovalPolicy): string {
  if (value === "untrusted") return "Codex fragt bei potenziell riskanten Aktionen.";
  if (value === "on-request") return "Codex kann gezielt eine Freigabe anfordern.";
  return "Es erscheinen keine einzelnen Freigabe-Dialoge.";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${String(value)} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
