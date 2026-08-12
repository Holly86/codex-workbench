import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import type { ThreadView } from "../workbench/types";
import { ThreadTitleDialog } from "./ThreadTitleDialog";

const thread = {
  id: "thread_opaque", repositoryId: "repo_opaque", repositoryName: "Beispiel",
  preview: "Fallback", name: "Alter Titel", status: "idle", sandboxMode: "workspace-write",
  approvalPolicy: "on-request", fullAccess: false, createdAt: 1, updatedAt: 1,
  activeTurnId: null, turns: [], usage: null, latestDiff: null,
  titleSource: "native", titleUpdatedAt: 1, titleGenerationState: "succeeded",
  titleErrorCode: null,
  activity: { state: "idle", label: "Bereit", last_activity: null, started_at: null,
    observed_at: "2026-01-01T00:00:00Z", elapsed_ms: null, current_tool: null,
    current_command: null, pending_approval_id: null, error_code: null, error_message: null },
} satisfies ThreadView;

test("normalizes and saves a manual title with initial focus", async () => {
  const save = vi.fn(() => Promise.resolve());
  render(<ThreadTitleDialog thread={thread} mode="edit" pending={false} onClose={() => undefined} onSave={save} />);
  const input = screen.getByRole("textbox", { name: "Titel" });
  await waitFor(() => expect(input).toHaveFocus());
  fireEvent.change(input, { target: { value: "  Runtime   Recovery absichern  " } });
  fireEvent.click(screen.getByRole("button", { name: "Titel speichern" }));
  expect(save).toHaveBeenCalledWith("Runtime Recovery absichern");
});

test("explains why unsafe AI regeneration stays unavailable", () => {
  render(<ThreadTitleDialog thread={thread} mode="regenerate" pending={false} onClose={() => undefined} onSave={() => Promise.resolve()} />);
  expect(screen.getByText(/keinen dokumentierten, toolfreien Regenerate-Aufruf/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /generieren/i })).not.toBeInTheDocument();
});

test("closes through the native dialog cancel event", () => {
  const close = vi.fn();
  render(<ThreadTitleDialog thread={thread} mode="edit" pending={false} onClose={close} onSave={() => Promise.resolve()} />);
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
  expect(close).toHaveBeenCalledOnce();
});
