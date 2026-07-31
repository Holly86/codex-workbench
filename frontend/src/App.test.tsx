import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { App } from "./App";

test("renders the accessible foundation layout and ready state", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
    }),
  );

  render(<App />);

  expect(screen.getByRole("heading", { level: 1, name: "Codex Workbench" })).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Repositories" })).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveAttribute("id", "conversation");
  expect(screen.getByRole("form", { name: "Nachricht verfassen" })).toBeInTheDocument();
  expect(await screen.findByText("Backend bereit")).toBeInTheDocument();
})
