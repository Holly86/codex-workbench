import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { RepositorySidebar } from "./RepositorySidebar";
import type { Repository, ThreadView } from "../workbench/types";

const repository = { id: "r", name: "codex-workbench", directoryName: "codex-workbench", status: { branch: "main", detached: false, upstream: "origin/main", upstreamMissing: false, ahead: 2, behind: 0, staged: 1, modified: 1, untracked: 1, conflicts: 0, clean: false, changes: [], observedAt: 1 }, preferences: { model: null, effort: null }, renameCapabilities: { displayName: true, directory: false, remote: false } } satisfies Repository;

const thread = {
  id: "thread_opaque", repositoryId: "r", repositoryName: "codex-workbench",
  preview: "Fallback", name: "Recovery testen", status: "idle", sandboxMode: "workspace-write",
  approvalPolicy: "on-request", fullAccess: false, createdAt: 1, updatedAt: 1,
  activeTurnId: null, turns: [], usage: null, latestDiff: null,
  activity: { state: "idle", label: "Bereit", last_activity: null, started_at: null,
    observed_at: "2026-01-01T00:00:00Z", elapsed_ms: null, current_tool: null,
    current_command: null, pending_approval_id: null, error_code: null, error_message: null },
} satisfies ThreadView;

beforeEach(() => localStorage.clear());

it("filters repositories and exposes a keyboard-accessible context menu", () => {
  const action = vi.fn();
  render(<RepositorySidebar repositories={[repository]} selectedRepositoryId="r" threads={[]} selectedThreadId={null} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn<(thread: ThreadView) => void>()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onRepositoryAction={action} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "missing" } });
  expect(screen.queryByText("codex-workbench")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "codex" } });
  fireEvent.click(screen.getByRole("button", { name: "codex-workbench Menü" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Commit…" }));
  expect(action).toHaveBeenCalledWith("commit", repository);
  fireEvent.click(screen.getByRole("button", { name: "codex-workbench Menü" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "SSH / Deploy Key" }));
  expect(action).toHaveBeenCalledWith("deploy-key", repository);
  fireEvent.click(screen.getByRole("button", { name: "Privat" }));
  expect(action).toHaveBeenCalledWith("connect-private", null);
});

it("persists independent collapse state and keyboard splitter size", async () => {
  const view = render(<RepositorySidebar repositories={[repository]} selectedRepositoryId="r" threads={[thread]} selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onRepositoryAction={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Projekte/ }));
  expect(document.getElementById("project-list")).toHaveAttribute("hidden");
  const splitter = screen.getByRole("separator", { name: "Höhe der Projektliste" });
  fireEvent.keyDown(splitter, { key: "End" });
  expect(splitter).toHaveAttribute("aria-valuenow", "520");
  fireEvent.keyDown(splitter, { key: "ArrowUp" });
  expect(splitter).toHaveAttribute("aria-valuenow", "504");
  await waitFor(() => expect(localStorage.getItem("codex-workbench.ui.v1")).toContain('"projectsHeight":504'));

  view.unmount();
  render(<RepositorySidebar repositories={[repository]} selectedRepositoryId="r" threads={[thread]} selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onRepositoryAction={vi.fn()} />);
  expect(screen.getByRole("separator", { name: "Höhe der Projektliste" })).toHaveAttribute("aria-valuenow", "504");
  expect(document.getElementById("project-list")).toHaveAttribute("hidden");
});

it("temporarily reveals collapsed search matches and exposes title actions", () => {
  const edit = vi.fn();
  render(<RepositorySidebar repositories={[repository]} selectedRepositoryId="r" threads={[thread]} selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onEditThread={edit} onRepositoryAction={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Threads/ }));
  expect(document.getElementById("thread-list")).toHaveAttribute("hidden");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Recovery" } });
  expect(document.getElementById("thread-list")).not.toHaveAttribute("hidden");
  fireEvent.click(screen.getByRole("button", { name: "Thread-Aktionen" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Titel bearbeiten …" }));
  expect(edit).toHaveBeenCalledWith(thread);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  expect(document.getElementById("thread-list")).toHaveAttribute("hidden");
});
