import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { ThreadView } from "../workbench/types";
import { ThreadDeleteDialog } from "./ThreadDeleteDialog";

const thread = {
  id: "thread_1", repositoryId: "repo_1", repositoryName: "sample", preview: "Preview",
  name: "Delete me", status: "idle", sandboxMode: "workspace-write",
  approvalPolicy: "on-request", fullAccess: false, createdAt: 1, updatedAt: 1,
  activeTurnId: null, turns: [], usage: null, latestDiff: null,
  activity: { state: "idle", label: "Bereit", last_activity: null, started_at: null,
    observed_at: "2026-01-01T00:00:00Z", elapsed_ms: 0, current_tool: null,
    current_command: null, pending_approval_id: null, error_code: null, error_message: null },
} satisfies ThreadView;

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  });
});

test("confirms the exact thread and explains that repository files remain", () => {
  const confirm = vi.fn();
  render(<ThreadDeleteDialog thread={thread} pending={false} onCancel={vi.fn()} onConfirm={confirm} />);
  expect(screen.getByText("Delete me")).toBeInTheDocument();
  expect(screen.getByText("sample")).toBeInTheDocument();
  expect(screen.getByText(/Git-Repository und seine Dateien werden nicht gelöscht/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Thread löschen" }));
  expect(confirm).toHaveBeenCalledOnce();
});

test("blocks confirmation while a turn is active", () => {
  render(<ThreadDeleteDialog thread={{ ...thread, activeTurnId: "turn_1" }} pending={false}
    onCancel={vi.fn()} onConfirm={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Thread löschen" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(/aktiven Turn/);
});
