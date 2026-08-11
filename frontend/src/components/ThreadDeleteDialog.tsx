import { useEffect, useRef } from "react";

import type { ThreadView } from "../workbench/types";

interface ThreadDeleteDialogProps {
  thread: ThreadView | null;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ThreadDeleteDialog({ thread, pending, onCancel, onConfirm }: ThreadDeleteDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (thread !== null && dialog !== null && !dialog.open) dialog.showModal();
    if (thread === null && dialog?.open) dialog.close();
  }, [thread]);
  if (thread === null) return null;
  return <dialog ref={dialogRef} className="decision-dialog" aria-labelledby="thread-delete-heading"
    onCancel={(event) => { event.preventDefault(); onCancel(); }} onClose={onCancel}>
    <h2 id="thread-delete-heading">Thread dauerhaft löschen?</h2>
    <dl className="thread-delete-summary">
      <div><dt>Thread</dt><dd>{thread.name ?? (thread.preview || "Neuer Thread")}</dd></div>
      <div><dt>Repository</dt><dd>{thread.repositoryName}</dd></div>
    </dl>
    <p>Der Gesprächsverlauf wird dauerhaft entfernt. Das Git-Repository und seine Dateien werden nicht gelöscht.</p>
    {thread.activeTurnId !== null ? <p role="alert" className="inline-error">Stoppe zuerst den aktiven Turn.</p> : null}
    <div className="dialog-actions">
      <button type="button" className="secondary-action" disabled={pending} onClick={onCancel}>Abbrechen</button>
      <button type="button" className="danger-action" disabled={pending || thread.activeTurnId !== null} onClick={onConfirm}>{pending ? "Wird gelöscht …" : "Thread löschen"}</button>
    </div>
  </dialog>;
}
