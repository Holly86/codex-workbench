import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import type { Approval } from "../workbench/types";
import { ApprovalDialog } from "./ApprovalDialog";

const approval: Approval = {
  id: "approval-1",
  kind: "command_execution",
  state: "presented",
  threadId: "thread-1",
  turnId: "turn-1",
  itemId: "item-1",
  requestedAt: "2026-07-31T00:00:00Z",
  expiresAt: "2026-07-31T00:05:00Z",
  summary: "git status --short",
  reason: null,
  command: "git status --short",
  cwdDisplay: "codex-workbench",
  writableRootsDisplay: [],
  availableDecisions: ["accept", "decline"],
};

test("offers only the server-projected approval decisions", async () => {
  const decide = vi.fn(() => Promise.resolve());
  render(<ApprovalDialog approval={approval} onDecision={decide} />);

  expect(screen.getByRole("button", { name: "Einmal zulassen" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Für Sitzung zulassen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ablehnen" }));
  expect(decide).toHaveBeenCalledWith("decline");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Ablehnen" })).toBeEnabled(),
  );
});
