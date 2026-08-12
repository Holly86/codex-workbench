import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import * as api from "../workbench/api";
import type { SystemStatus } from "../workbench/types";
import { SystemStatusDrawer } from "./SystemStatusDrawer";

vi.mock("../workbench/api", async () => {
  const original = await vi.importActual<typeof import("../workbench/api")>("../workbench/api");
  return { ...original, readSystemStatus: vi.fn(), restartRuntime: vi.fn() };
});

const status: SystemStatus = {
  status: "healthy", workbenchVersion: "0.1.0", codexVersion: "0.146.0", observedAt: 1,
  components: [{ component: "codex_app_server", state: "healthy", observedAt: 1,
    errorCode: null, lastSuccessAt: 1, lastRecoveryAt: null, nextRecoveryAt: null, generation: 2,
    restartAttempts: 0, restartLimit: 5, activeCount: null, queuedCount: null,
    failedCount: null, connectionCount: null, optional: false, enabled: true }],
};

test("shows typed status and requires explicit confirmation before one restart", async () => {
  vi.mocked(api.readSystemStatus).mockResolvedValue(status);
  vi.mocked(api.restartRuntime).mockResolvedValue("started");
  render(<SystemStatusDrawer open csrfToken="csrf" realtimeState="connected" onClose={() => undefined} onReconnect={() => undefined} onReloadRepositories={() => Promise.resolve()} />);
  expect(await screen.findByText("Codex App Server")).toBeInTheDocument();
  const restart = screen.getByRole("button", { name: "Codex Runtime neu starten" });
  expect(restart).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(restart);
  await waitFor(() => expect(api.restartRuntime).toHaveBeenCalledTimes(1));
});
