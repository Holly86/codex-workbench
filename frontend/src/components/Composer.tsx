import {
  useEffect,
  useId,
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
  sandboxMode?: SandboxMode;
  approvalPolicy?: ApprovalPolicy;
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
  effort, sandboxMode = "workspace-write", approvalPolicy = "on-request",
  onModelChange = () => undefined, onAccessChange = () => undefined }: ComposerProps) {
  const inputId = useId();
  const pickerId = useId();
  const [message, setMessage] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [openMenu, setOpenMenu] = useState<"add" | "model" | "access" | "skills" | null>(null);
  const attachmentsRef = useRef(attachments);

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
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const uploading = attachments.some((item) => item.state === "uploading");
  const canSend = message.trim().length > 0 && !disabled && !working && !uploading;
  const selectedModel = capabilities?.models.find((item) => item.id === model)
    ?? capabilities?.models.find((item) => item.isDefault) ?? null;

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
        value={message}
        disabled={disabled}
        onChange={(event) => setMessage(event.target.value)}
        onPaste={handlePaste}
        onKeyDown={handleKeyDown}
        placeholder={disabled ? "Wähle oder erstelle zuerst einen Thread." : "Beschreibe eine Aufgabe …"}
        rows={3}
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
          }}
        />
        <div className="composer-menu-anchor">
          <button type="button" className="composer-pill icon-pill" aria-label="Hinzufügen"
            aria-haspopup="menu" aria-expanded={openMenu === "add"}
            onClick={() => setOpenMenu(openMenu === "add" ? null : "add")}>+</button>
          {openMenu === "add" ? <div className="composer-popover" role="menu">
            <p>Hinzufügen</p><label className="menu-item" htmlFor={pickerId}>Dateien / Bilder</label>
            <button type="button" role="menuitem" disabled>Repository-Kontext · automatisch</button>
          </div> : null}
        </div>
        <div className="composer-menu-anchor">
          <button type="button" className="composer-pill" aria-haspopup="menu"
            aria-expanded={openMenu === "model"} onClick={() => setOpenMenu(openMenu === "model" ? null : "model")}>{selectedModel?.displayName ?? "Model"} ▾</button>
          {openMenu === "model" ? <div className="composer-popover wide" role="menu">
            <p>Modell</p>{capabilities?.models.map((item) => <button type="button" role="menuitemradio"
              aria-checked={item.id === selectedModel?.id} key={item.id} onClick={() => {
                onModelChange(item.id, item.defaultReasoningEffort); setOpenMenu(null);
              }}><strong>{item.displayName}</strong><span>{item.description}</span></button>)}
            {selectedModel !== null ? <><p>Reasoning</p>{selectedModel.reasoningEfforts.map((item) =>
              <button type="button" role="menuitemradio" aria-checked={item.value === effort}
                key={item.value} onClick={() => { onModelChange(selectedModel.id, item.value); setOpenMenu(null); }}>
                <strong>{item.value}</strong><span>{item.description}</span></button>)}</> : null}
          </div> : null}
        </div>
        <div className="composer-menu-anchor">
          <button type="button" className={`composer-pill ${sandboxMode === "danger-full-access" ? "danger-selection" : ""}`}
            aria-haspopup="menu" aria-expanded={openMenu === "access"}
            onClick={() => setOpenMenu(openMenu === "access" ? null : "access")}>Access: {sandboxMode === "read-only" ? "Read only" : sandboxMode === "workspace-write" ? "Workspace" : "Full"} ▾</button>
          {openMenu === "access" ? <div className="composer-popover" role="menu"><p>Arbeitszugriff</p>
            {([['read-only','Read only','Lesen, nichts verändern'],['workspace-write','Workspace','Änderungen im Repository'],['danger-full-access','Full access','Uneingeschränkter Agentenzugriff']] as const).map(([value,label,help]) =>
              { const key = value === "read-only" ? "readOnly" : value === "workspace-write" ? "workspaceWrite" : "fullAccess"; const available = capabilities?.host?.sandbox[key].available ?? true; return <button type="button" role="menuitemradio" aria-checked={sandboxMode === value} key={value} disabled={!available} title={!available ? "Die lokale Linux-Sandbox wird vom Host-Kernel nicht unterstützt." : undefined}
                onClick={() => { onAccessChange(value, approvalPolicy); setOpenMenu(null); }}><strong>{label}</strong><span>{available ? help : "Nicht verfügbar auf diesem Host"}</span></button>; })}
            <p>Approval</p>{(['untrusted','on-request','never'] as const).map((value) => <button type="button"
              role="menuitemradio" aria-checked={approvalPolicy === value} key={value}
              onClick={() => { onAccessChange(sandboxMode, value); setOpenMenu(null); }}>{value}</button>)}
          </div> : null}
        </div>
        <div className="composer-menu-anchor"><button type="button" className="composer-pill"
          aria-haspopup="menu" aria-expanded={openMenu === "skills"}
          onClick={() => setOpenMenu(openMenu === "skills" ? null : "skills")}>Skills ▾</button>
          {openMenu === "skills" ? <div className="composer-popover wide" role="menu"><p>Skills & Plugins</p>
            {capabilities?.skills.map((item) => <div className="menu-static" key={item.name}><strong>{item.name}</strong><span>{item.description}</span></div>)}
            {capabilities?.plugins.map((item) => <div className="menu-static" key={item.id}><strong>{item.name}</strong><span>{item.installed ? "Installiert" : "Nicht installiert"}</span></div>)}
            {!capabilities?.native.apps ? <div className="menu-static"><strong>Apps</strong><span>Nicht aktiviert</span></div> : null}
          </div> : null}</div>
        <span className="composer-hint">Strg/⌘ + Enter</span>
        {working ? (
          <button type="button" className="stop-action" onClick={() => void onStop()}>Turn abbrechen</button>
        ) : (
          <button type="submit" className="primary-action" disabled={!canSend}>Senden</button>
        )}
      </div>
    </form>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${String(value)} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
