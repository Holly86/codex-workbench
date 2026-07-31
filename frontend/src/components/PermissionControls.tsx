import type { ApprovalPolicy, SandboxMode } from "../workbench/types";

interface PermissionControlsProps {
  sandboxMode: SandboxMode;
  approvalPolicy: ApprovalPolicy;
  disabled: boolean;
  onSandboxChange: (value: SandboxMode) => void;
  onApprovalChange: (value: ApprovalPolicy) => void;
}

export function PermissionControls({
  sandboxMode,
  approvalPolicy,
  disabled,
  onSandboxChange,
  onApprovalChange,
}: PermissionControlsProps) {
  return (
    <div className="permission-controls" aria-label="Berechtigungen">
      <label>
        <span>Sandbox</span>
        <select
          value={sandboxMode}
          disabled={disabled}
          className={sandboxMode === "danger-full-access" ? "danger-selection" : undefined}
          onChange={(event) => onSandboxChange(event.target.value as SandboxMode)}
        >
          <option value="read-only">Read only — nur lesen</option>
          <option value="workspace-write">Workspace write — Repository schreiben</option>
          <option value="danger-full-access">Full Access — keine Sandbox</option>
        </select>
      </label>
      <label>
        <span>Approval Policy</span>
        <select
          value={approvalPolicy}
          disabled={disabled}
          onChange={(event) => onApprovalChange(event.target.value as ApprovalPolicy)}
        >
          <option value="untrusted">Untrusted — nicht vertrauenswürdige Aktionen fragen</option>
          <option value="on-request">On request — Codex darf Freigaben anfordern</option>
          <option value="never">Never — keine Einzelabfragen</option>
        </select>
      </label>
    </div>
  );
}
