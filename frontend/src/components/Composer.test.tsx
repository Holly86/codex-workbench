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
  status: { overall: "ready", generation: 1, cached: false, stale: false, models: { available: true, reason: null }, skills: { available: true, reason: null }, plugins: { available: true, reason: null } },
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
