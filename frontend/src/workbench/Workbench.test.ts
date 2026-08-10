import { resolveSafeSandboxMode } from "./access";
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
      models: { available: true, reason: null },
      skills: { available: true, reason: null },
      plugins: { available: true, reason: null },
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
