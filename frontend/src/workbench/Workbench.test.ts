import { resolveDefaultSandboxMode, resolveSafeSandboxMode } from "./access";
import type { WorkbenchCapabilities } from "./types";

function capabilities(
  workspaceWrite: boolean,
  readOnly: boolean,
  fullAccess: boolean,
): WorkbenchCapabilities {
  return {
    models: [],
    skills: [],
    plugins: [],
    native: { models: true, skills: true, plugins: true, apps: false },
    status: {
      overall: "ready",
      generation: 1,
      cached: false,
      stale: false,
      models: { state: "ready", available: true, reason: null },
      skills: { state: "ready", available: true, reason: null },
      plugins: { state: "ready", available: true, reason: null },
      apps: { state: "disabled", available: false, reason: null },
    },
    host: { sandbox: {
      workspaceWrite: { available: workspaceWrite, reason: null },
      readOnly: { available: readOnly, reason: null },
      fullAccess: { available: fullAccess, reason: null },
    } },
  };
}

test("keeps an available safe preference", () => {
  expect(resolveSafeSandboxMode(capabilities(true, true, true), "read-only")).toBe("read-only");
});

test("replaces an unavailable Workspace preference with an available safe mode", () => {
  expect(resolveSafeSandboxMode(capabilities(false, true, true), "workspace-write")).toBe("read-only");
});

test("requires explicit selection when only Full Access is available", () => {
  expect(resolveSafeSandboxMode(capabilities(false, false, true), "workspace-write")).toBeNull();
  expect(resolveSafeSandboxMode(capabilities(false, false, true), "danger-full-access")).toBeNull();
});

test("defaults to Full Access only when no safe sandbox is available", () => {
  expect(resolveDefaultSandboxMode(capabilities(true, true, true), null))
    .toBe("workspace-write");
  expect(resolveDefaultSandboxMode(capabilities(false, true, true), null))
    .toBe("read-only");
  expect(resolveDefaultSandboxMode(capabilities(false, false, true), null))
    .toBe("danger-full-access");
});
