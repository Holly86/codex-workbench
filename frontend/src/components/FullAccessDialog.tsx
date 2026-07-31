import { useEffect, useRef, useState } from "react";

import type { ApprovalPolicy } from "../workbench/types";

interface FullAccessDialogProps {
  open: boolean;
  repository: string;
  approvalPolicy: ApprovalPolicy;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function FullAccessDialog({ open, repository, approvalPolicy, pending, onConfirm, onCancel }: FullAccessDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  useEffect(() => {
    if (open && !dialogRef.current?.open) {
      setAcknowledged(false);
      dialogRef.current?.showModal();
    }
    if (!open && dialogRef.current?.open) dialogRef.current.close();
  }, [open]);
  return (
    <dialog ref={dialogRef} className="decision-dialog full-access-dialog" aria-labelledby="full-access-heading" onCancel={(event) => { event.preventDefault(); onCancel(); }}>
      <p className="eyebrow">Bewusste Bestätigung</p>
      <h2 id="full-access-heading">Full Access aktivieren?</h2>
      <dl>
        <div><dt>Repository</dt><dd>{repository}</dd></div>
        <div><dt>Sandbox</dt><dd>danger-full-access</dd></div>
        <div><dt>Approval Policy</dt><dd>{approvalPolicy}</dd></div>
      </dl>
      <p>Codex arbeitet für diese Auswahl ohne Sandbox. Mit „never“ erscheinen keine einzelnen Approval-Dialoge.</p>
      <label className="confirmation-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> Ich möchte diese Kombination für diesen Thread bewusst verwenden.</label>
      <div className="dialog-actions">
        <button type="button" className="secondary-action" disabled={pending} onClick={onCancel}>Abbrechen</button>
        <button type="button" className="full-access-action" disabled={!acknowledged || pending} onClick={onConfirm}>{pending ? "Bestätigung wird erstellt …" : "Full Access aktivieren"}</button>
      </div>
    </dialog>
  );
}
