import type { SandboxMode, WorkbenchCapabilities } from "./types";

export function sandboxAvailable(
  capabilities: WorkbenchCapabilities | null,
  mode: SandboxMode,
): boolean {
  const sandbox = capabilities?.host?.sandbox;
  if (sandbox === undefined) return false;
  if (mode === "read-only") return sandbox.readOnly.available;
  if (mode === "workspace-write") return sandbox.workspaceWrite.available;
  return sandbox.fullAccess.available;
}

export function resolveSafeSandboxMode(
  capabilities: WorkbenchCapabilities,
  preferred: SandboxMode | null,
): SandboxMode | null {
  if (
    preferred !== null &&
    preferred !== "danger-full-access" &&
    sandboxAvailable(capabilities, preferred)
  ) return preferred;
  if (sandboxAvailable(capabilities, "workspace-write")) return "workspace-write";
  if (sandboxAvailable(capabilities, "read-only")) return "read-only";
  return null;
}

export function isFullAccessOnlyHost(capabilities: WorkbenchCapabilities): boolean {
  return !sandboxAvailable(capabilities, "workspace-write")
    && !sandboxAvailable(capabilities, "read-only")
    && sandboxAvailable(capabilities, "danger-full-access");
}

export function resolveDefaultSandboxMode(
  capabilities: WorkbenchCapabilities,
  preferred: SandboxMode | null,
): SandboxMode | null {
  return resolveSafeSandboxMode(capabilities, preferred)
    ?? (isFullAccessOnlyHost(capabilities) ? "danger-full-access" : null);
}
