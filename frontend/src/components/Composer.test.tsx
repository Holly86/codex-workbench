import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { Composer } from "./Composer";
import type { WorkbenchCapabilities } from "../workbench/types";

const capabilities = {
  models: [
    { id: "large", displayName: "Large", description: "Gründlich", isDefault: true, defaultReasoningEffort: "high", reasoningEfforts: [{ value: "medium", description: "Ausgewogen" }, { value: "high", description: "Gründlich" }], inputModalities: ["text"] },
    { id: "mini", displayName: "Mini", description: "Schnell", isDefault: false, defaultReasoningEffort: "medium", reasoningEfforts: [{ value: "low", description: "Schnell" }, { value: "medium", description: "Ausgewogen" }], inputModalities: ["text"] },
  ],
  skills: [],
  plugins: [],
  native: { models: true, skills: true, plugins: true, apps: false },
  status: { overall: "ready", generation: 1, cached: false, stale: false, models: { state: "ready", available: true, reason: null }, skills: { state: "ready", available: true, reason: null }, plugins: { state: "ready", available: true, reason: null }, apps: { state: "disabled", available: false, reason: null } },
  host: { sandbox: { workspaceWrite: { available: true, reason: null }, readOnly: { available: true, reason: null }, fullAccess: { available: true, reason: null } } },
} satisfies WorkbenchCapabilities;

test("enables sending only when the draft contains text", () => {
  render(
    <Composer
      csrfToken="csrf"
      disabled={false}
      working={false}
      onSend={() => Promise.resolve(true)}
      onStop={() => Promise.resolve()}
    />,
  );

  const input = screen.getByRole("textbox", { name: "Nachricht an Codex" });
  const submit = screen.getByRole("button", { name: "Senden" });

  expect(submit).toBeDisabled();
  fireEvent.change(input, { target: { value: "Prüfe den Git-Status" } });
  expect(submit).toBeEnabled();
  fireEvent.change(input, { target: { value: "   " } });
  expect(submit).toBeDisabled();
});

test("keeps model and reasoning as separate controls and validates effort on model change", () => {
  const onModelChange = vi.fn();
  render(
    <Composer
      csrfToken="csrf"
      disabled={false}
      working={false}
      capabilities={capabilities}
      model="large"
      effort="high"
      onModelChange={onModelChange}
      onSend={() => Promise.resolve(true)}
      onStop={() => Promise.resolve()}
    />,
  );

  expect(screen.getByRole("button", { name: "Model: Large" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reasoning: high" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Model: Large" }));
  expect(screen.getByRole("menu", { name: "Modell auswählen" })).toBeInTheDocument();
  expect(screen.queryByRole("menu", { name: "Reasoning auswählen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Mini Schnell" }));
  expect(onModelChange).toHaveBeenCalledWith("mini", "medium");
});

test("shows a loading Access state and keeps every mode unavailable before capabilities arrive", () => {
  render(
    <Composer
      csrfToken="csrf"
      disabled={false}
      working={false}
      capabilities={null}
      sandboxMode={null}
      accessMessage="Access-Optionen werden geladen …"
      onSend={() => Promise.resolve(true)}
      onStop={() => Promise.resolve()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Access: Lädt …" }));
  expect(screen.getByText("Access-Optionen werden geladen …")).toBeInTheDocument();
  for (const option of screen.getAllByRole("menuitemradio").slice(0, 3)) {
    expect(option).toBeDisabled();
  }
});

test("marks Access as required and disables a host-unavailable Workspace option", () => {
  const onAccessChange = vi.fn();
  const fullOnly = {
    ...capabilities,
    host: { sandbox: {
      workspaceWrite: { available: false, reason: "Workspace-Sandbox fehlt" },
      readOnly: { available: false, reason: "Read-only-Sandbox fehlt" },
      fullAccess: { available: true, reason: null },
    } },
  } satisfies WorkbenchCapabilities;
  render(
    <Composer
      csrfToken="csrf"
      disabled={false}
      working={false}
      capabilities={fullOnly}
      sandboxMode={null}
      accessMessage="Access-Auswahl erforderlich."
      onAccessChange={onAccessChange}
      onSend={() => Promise.resolve(true)}
      onStop={() => Promise.resolve()}
    />,
  );

  const access = screen.getByRole("button", { name: "Access: Auswahl erforderlich" });
  expect(access).toHaveAttribute("data-invalid", "true");
  expect(access).toHaveAccessibleDescription("Access-Auswahl erforderlich.");
  fireEvent.click(access);
  expect(screen.getByRole("menuitemradio", { name: /Workspace Workspace-Sandbox fehlt/ })).toBeDisabled();
  const fullAccess = screen.getByRole("menuitemradio", { name: /Full access Uneingeschränkter/ });
  expect(fullAccess).toBeEnabled();
  fireEvent.click(fullAccess);
  expect(onAccessChange).toHaveBeenCalledWith("danger-full-access", "on-request");
});
