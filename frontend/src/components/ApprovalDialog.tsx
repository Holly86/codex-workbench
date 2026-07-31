import { useEffect, useRef, useState } from "react";

import type { Approval, ApprovalDecision } from "../workbench/types";

const LABELS: Record<ApprovalDecision, string> = {
  accept: "Einmal zulassen",
  accept_for_session: "Für Sitzung zulassen",
  accept_with_execpolicy_amendment: "Mit Befehlsregel zulassen",
  apply_network_policy_amendment: "Netzwerkregel übernehmen",
  decline: "Ablehnen",
  cancel: "Turn abbrechen",
};

export function ApprovalDialog({ approval, onDecision }: { approval: Approval | null; onDecision: (decision: ApprovalDecision) => Promise<void> }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (approval !== null && !dialogRef.current?.open) dialogRef.current?.showModal();
    if (approval === null && dialogRef.current?.open) dialogRef.current.close();
  }, [approval]);
  if (approval === null) return null;
  return (
    <dialog ref={dialogRef} className="decision-dialog" aria-labelledby="approval-heading" onCancel={(event) => event.preventDefault()}>
      <p className="eyebrow">Freigabe erforderlich</p>
      <h2 id="approval-heading">{approval.kind === "command_execution" ? "Befehl freigeben?" : approval.kind === "file_change" ? "Dateiänderung freigeben?" : "Berechtigungen freigeben?"}</h2>
      <p>{approval.summary}</p>
      {approval.command !== null ? <pre>{approval.command}</pre> : null}
      {approval.cwdDisplay !== null ? <p>Repository: <strong>{approval.cwdDisplay}</strong></p> : null}
      {approval.writableRootsDisplay.length > 0 ? <p>Schreibziele: {approval.writableRootsDisplay.join(", ")}</p> : null}
      <p className="expiry">Gültig bis {new Date(approval.expiresAt).toLocaleTimeString()}</p>
      <div className="dialog-actions">
        {approval.availableDecisions.map((decision) => (
          <button
            type="button"
            className={decision === "decline" || decision === "cancel" ? "secondary-action" : "primary-action"}
            disabled={pending}
            key={decision}
            onClick={() => {
              setPending(true);
              void onDecision(decision).finally(() => setPending(false));
            }}
          >{LABELS[decision]}</button>
        ))}
      </div>
    </dialog>
  );
}
