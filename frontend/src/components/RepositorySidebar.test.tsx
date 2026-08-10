import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RepositorySidebar } from "./RepositorySidebar";
import type { Repository, ThreadView } from "../workbench/types";

const repository = { id: "r", name: "codex-workbench", directoryName: "codex-workbench", status: { branch: "main", detached: false, upstream: "origin/main", upstreamMissing: false, ahead: 2, behind: 0, staged: 1, modified: 1, untracked: 1, conflicts: 0, clean: false, changes: [], observedAt: 1 }, preferences: { model: null, effort: null }, renameCapabilities: { displayName: true, directory: false, remote: false } } satisfies Repository;

it("filters repositories and exposes a keyboard-accessible context menu", () => {
  const action = vi.fn();
  render(<RepositorySidebar repositories={[repository]} selectedRepositoryId="r" threads={[]} selectedThreadId={null} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn<(thread: ThreadView) => void>()} onNewThread={vi.fn()} onRepositoryAction={action} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "missing" } });
  expect(screen.queryByText("codex-workbench")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "codex" } });
  fireEvent.click(screen.getByRole("button", { name: "codex-workbench Menü" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Commit…" }));
  expect(action).toHaveBeenCalledWith("commit", repository);
});
