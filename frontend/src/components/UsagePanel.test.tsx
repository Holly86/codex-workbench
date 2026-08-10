import { fireEvent, render, screen } from "@testing-library/react";

import type { RateLimits } from "../workbench/types";
import { UsagePanel } from "./UsagePanel";

const rateLimits: RateLimits = {
  available: true,
  primary: { usedPercent: 26, windowDurationMins: 300, resetsAt: 1_785_941_207 },
  secondary: null,
  credits: { hasCredits: false, unlimited: false, balance: null },
  spendControlReached: false,
  rateLimitReachedType: null,
  observedAt: 1_775_865_567,
};

test("keeps the primary-window remainder visible and reveals details on click", () => {
  render(<UsagePanel thread={null} rateLimits={rateLimits} />);

  const summary = screen.getByTitle("Restkontingent im Primärfenster: 74.0 % frei");
  expect(summary).toHaveTextContent("74.0 % frei");
  expect(summary.closest("details")).not.toHaveAttribute("open");

  fireEvent.click(summary);

  expect(summary.closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("heading", { name: "Nutzung & Kontingent" })).toBeInTheDocument();
  expect(screen.getByText(/26\.0 % verbraucht/)).toBeInTheDocument();
});

test("shows an always-present fallback when rate limits are unavailable", () => {
  render(<UsagePanel thread={null} rateLimits={{ available: false, observedAt: 0 }} />);

  expect(screen.getAllByText("nicht verfügbar").length).toBeGreaterThan(0);
  expect(screen.getByTitle("Restkontingent im Primärfenster: nicht verfügbar"))
    .toBeInTheDocument();
});
