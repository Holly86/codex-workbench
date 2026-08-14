import { vi } from "vitest";

import { listRepositories, startThread } from "./api";
import type { FullAccessCapability, SandboxMode } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function threadResponse(sandboxMode: SandboxMode): Response {
  return new Response(JSON.stringify({
    thread: {
      id: "thread-1",
      repositoryId: "0123456789abcdef",
      repositoryName: "workbench",
      preview: "",
      name: null,
      status: "idle",
      sandboxMode,
      approvalPolicy: "on-request",
      fullAccess: sandboxMode === "danger-full-access",
      createdAt: 1,
      updatedAt: 1,
      activeTurnId: null,
      turns: [],
      usage: null,
      activity: { state: "idle" },
      latestDiff: null,
    },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}

async function postedBody(
  sandboxMode: SandboxMode,
  capability: FullAccessCapability | null,
): Promise<Record<string, unknown>> {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(threadResponse(sandboxMode));
  await startThread("csrf", "0123456789abcdef", sandboxMode, "on-request", capability);
  const init = fetchMock.mock.calls[0]?.[1];
  if (typeof init?.body !== "string") {
    throw new TypeError("Der Thread-Request muss einen serialisierten JSON-String enthalten.");
  }
  const parsed: unknown = JSON.parse(init.body);
  if (!isRecord(parsed)) {
    throw new TypeError("Der Thread-Request muss ein JSON-Objekt enthalten.");
  }
  return parsed;
}

test.each(["workspace-write", "read-only"] as const)(
  "omits Full Access fields from a %s thread request",
  async (sandboxMode) => {
    const body = await postedBody(sandboxMode, null);

    expect(body).toEqual({
      repositoryId: "0123456789abcdef",
      sandboxMode,
      approvalPolicy: "on-request",
    });
    expect(body).not.toHaveProperty("confirmationId");
    expect(body).not.toHaveProperty("fullAccessScopeId");
  },
);

test("includes both capability fields only for an explicitly confirmed Full Access request", async () => {
  const capability: FullAccessCapability = {
    confirmationId: "confirmation-1",
    scopeId: "scope-1",
    expiresAt: "2030-01-01T00:00:00Z",
    repository: "workbench",
    sandboxMode: "danger-full-access",
    approvalPolicy: "on-request",
  };

  await expect(postedBody("danger-full-access", capability)).resolves.toEqual({
    repositoryId: "0123456789abcdef",
    sandboxMode: "danger-full-access",
    approvalPolicy: "on-request",
    confirmationId: "confirmation-1",
    fullAccessScopeId: "scope-1",
  });
});

test("does not send Full Access without a capability", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");

  await expect(startThread(
    "csrf",
    "0123456789abcdef",
    "danger-full-access",
    "on-request",
    null,
  )).rejects.toThrow("Full Access erfordert eine gültige Bestätigung.");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("parses authoritative repository activity without requiring thread hydration", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    repositories: [
      { id: "active", name: "Active", activeTurnCount: 2 },
      { id: "idle", name: "Idle" },
    ],
  }), { status: 200, headers: { "Content-Type": "application/json" } }));

  const repositories = await listRepositories();

  expect(repositories.map(({ id, activeTurnCount }) => ({ id, activeTurnCount }))).toEqual([
    { id: "active", activeTurnCount: 2 },
    { id: "idle", activeTurnCount: 0 },
  ]);
});
