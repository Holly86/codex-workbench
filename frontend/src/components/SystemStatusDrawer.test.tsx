import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, expect, test, vi } from "vitest";

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

beforeEach(() => vi.clearAllMocks());

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
  expect(restart).toBeDisabled();
});

test("loads and focuses exactly once across 100 parent rerenders", async () => {
  vi.mocked(api.readSystemStatus).mockResolvedValue(status);
  const focus = vi.spyOn(HTMLButtonElement.prototype, "focus");
  function Parent({ tick }: { tick: number }) {
    void tick;
    return <SystemStatusDrawer open csrfToken="csrf" realtimeState="connected"
      onClose={() => undefined} onReconnect={() => undefined}
      onReloadRepositories={() => Promise.resolve()} />;
  }
  const view = render(<Parent tick={0} />);
  expect(await screen.findByText("Codex App Server")).toBeInTheDocument();
  await waitFor(() => expect(focus).toHaveBeenCalledTimes(1));
  for (let index = 1; index <= 100; index += 1) view.rerender(<Parent tick={index} />);
  expect(api.readSystemStatus).toHaveBeenCalledTimes(1);
  expect(focus).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Status jetzt prüfen" }));
  await waitFor(() => expect(api.readSystemStatus).toHaveBeenCalledTimes(2));
  focus.mockRestore();
});

test("reloads repositories once while pending and reports success", async () => {
  vi.mocked(api.readSystemStatus).mockResolvedValue(status);
  let resolveReload: () => void = () => undefined;
  const reload = vi.fn(() => new Promise<void>((resolve) => { resolveReload = resolve; }));
  render(<SystemStatusDrawer open csrfToken="csrf" realtimeState="connected"
    onClose={() => undefined} onReconnect={() => undefined}
    onReloadRepositories={reload} />);
  await screen.findByText("Codex App Server");
  const button = screen.getByRole("button", { name: "Repositoryliste neu laden" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(reload).toHaveBeenCalledTimes(1);
  expect(button).toBeDisabled();
  act(() => resolveReload());
  expect(await screen.findByText("Repositoryliste wurde neu geladen.")).toBeInTheDocument();
  expect(button).toBeEnabled();
});

test("contains repository reload failures without leaking raw exceptions", async () => {
  vi.mocked(api.readSystemStatus).mockResolvedValue(status);
  const unhandled = vi.fn();
  window.addEventListener("unhandledrejection", unhandled);
  const reload = vi.fn(() => Promise.reject(new Error("sensitive /srv/repositories path")));
  render(<SystemStatusDrawer open csrfToken="csrf" realtimeState="connected"
    onClose={() => undefined} onReconnect={() => undefined}
    onReloadRepositories={reload} />);
  await screen.findByText("Codex App Server");
  fireEvent.click(screen.getByRole("button", { name: "Repositoryliste neu laden" }));
  expect(await screen.findByText(/Repositoryliste konnte nicht neu geladen werden/)).toBeInTheDocument();
  expect(screen.queryByText(/sensitive|\/srv\/repositories/)).not.toBeInTheDocument();
  expect(unhandled).not.toHaveBeenCalled();
  window.removeEventListener("unhandledrejection", unhandled);
});

test("desktop is nonmodal, returns focus, and resets confirmation on reopen", async () => {
  vi.mocked(api.readSystemStatus).mockResolvedValue(status);
  function Harness() {
    const [open, setOpen] = useState(false);
    return <><button type="button" onClick={() => setOpen(true)}>Status öffnen</button>
      <SystemStatusDrawer open={open} csrfToken="csrf" realtimeState="connected"
        onClose={() => setOpen(false)} onReconnect={() => undefined}
        onReloadRepositories={() => Promise.resolve()} /></>;
  }
  render(<Harness />);
  const opener = screen.getByRole("button", { name: "Status öffnen" });
  opener.focus();
  fireEvent.click(opener);
  expect(screen.getByRole("button", { name: "Codex Runtime neu starten" })).toBeDisabled();
  await screen.findByText("Codex App Server");
  expect(screen.queryByRole("dialog", { name: "Systemstatus" })).not.toBeInTheDocument();
  expect(document.querySelector(".drawer-scrim")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Systemstatus schließen" }));
  expect(opener).toHaveFocus();
  fireEvent.click(opener);
  expect(screen.getByRole("button", { name: "Codex Runtime neu starten" })).toBeDisabled();
  await screen.findByText("Codex App Server");
  expect(screen.getByRole("button", { name: "Codex Runtime neu starten" })).toBeDisabled();
});

test("mobile sheet is modal, traps focus, makes background inert, and closes with Escape", async () => {
  const original = Object.getOwnPropertyDescriptor(window, "matchMedia");
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({ matches: true, media: "(max-width: 760px)", onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(),
      removeListener: vi.fn(), dispatchEvent: vi.fn() }),
  });
  vi.mocked(api.readSystemStatus).mockResolvedValue(status);
  const close = vi.fn();
  const view = render(<><div className="app-shell"><button type="button">Hintergrund</button></div>
    <SystemStatusDrawer open csrfToken="csrf" realtimeState="connected"
      onClose={close} onReconnect={() => undefined}
      onReloadRepositories={() => Promise.resolve()} /></>);
  const dialog = await screen.findByRole("dialog", { name: "Systemstatus" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(document.querySelector(".app-shell")).toHaveProperty("inert", true);
  expect(document.querySelector(".drawer-scrim")).not.toBeNull();
  const buttons = within(dialog).getAllByRole("button");
  buttons.at(-1)?.focus();
  fireEvent.keyDown(buttons.at(-1) ?? dialog, { key: "Tab" });
  expect(buttons[0]).toHaveFocus();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
  view.unmount();
  if (original === undefined) Reflect.deleteProperty(window, "matchMedia");
  else Object.defineProperty(window, "matchMedia", original);
});
