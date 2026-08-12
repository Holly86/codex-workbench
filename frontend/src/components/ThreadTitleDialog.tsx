import { useState } from "react";

import type { ThreadView } from "../workbench/types";
import { DialogShell, FormField, InlineAlert, PrimaryButton, SecondaryButton } from "./ui";

export function ThreadTitleDialog({ thread, mode, pending, onClose, onSave }: {
  thread: ThreadView | null; mode: "edit" | "regenerate" | null; pending: boolean;
  onClose: () => void; onSave: (title: string) => Promise<void>;
}) {
  if (thread === null || mode === null) return null;
  return <ThreadTitleContent key={`${thread.id}:${mode}`} thread={thread} mode={mode} pending={pending} onClose={onClose} onSave={onSave} />;
}

function ThreadTitleContent({ thread, mode, pending, onClose, onSave }: {
  thread: ThreadView; mode: "edit" | "regenerate"; pending: boolean;
  onClose: () => void; onSave: (title: string) => Promise<void>;
}) {
  const [title, setTitle] = useState(thread.name ?? "");
  const clean = title.replace(/\s+/g, " ");
  const valid = title.trim().length > 0 && title.trim().length <= 80;
  return <DialogShell
    open
    title={mode === "edit" ? "Threadtitel bearbeiten" : "KI-Titel neu generieren"}
    description={mode === "edit" ? "Ein manueller Titel hat dauerhaft Vorrang vor nativen Titelupdates." : "Sichere Titelgenerierung benötigt einen isolierten Codex-Vertrag ohne Tools und Berechtigungsausweitung."}
    onClose={onClose}
    footer={<><SecondaryButton onClick={onClose}>Schließen</SecondaryButton>{mode === "edit" ? <PrimaryButton disabled={!valid || pending} onClick={() => void onSave(clean.trim())}>{pending ? "Wird gespeichert …" : "Titel speichern"}</PrimaryButton> : null}</>}
  >
    {mode === "edit" ? <FormField label="Titel" description="Maximal 80 Zeichen; ohne Markdown oder abschließende Auslassungspunkte."><input data-dialog-initial-focus value={title} maxLength={80} onChange={(event) => setTitle(event.target.value)} /></FormField> : <InlineAlert tone="warning">Codex 0.146.0 besitzt zwar native Titelupdates und <code>thread/name/set</code>, aber keinen dokumentierten, toolfreien Regenerate-Aufruf. Die Workbench startet deshalb keinen versteckten Turn und weitet keine Berechtigung aus. Der native automatische Titel bleibt erhalten und kann manuell überschrieben werden.</InlineAlert>}
  </DialogShell>;
}
