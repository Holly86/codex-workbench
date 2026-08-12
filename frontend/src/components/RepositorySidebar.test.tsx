import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import type { ComponentProps } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { RepositorySidebar } from "./RepositorySidebar";
import type { Repository, ThreadView } from "../workbench/types";
import { readUiPreferences, writeUiPreferences } from "../workbench/uiPreferences";

function SidebarHarness(props: Omit<ComponentProps<typeof RepositorySidebar>, "preferences" | "onPreferencesChange">) {
  const [preferences, setPreferences] = useState(readUiPreferences);
  useEffect(() => writeUiPreferences(preferences), [preferences]);
  return <RepositorySidebar {...props} preferences={preferences} onPreferencesChange={setPreferences} />;
}

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
  render(<SidebarHarness repositories={[repository]} selectedRepositoryId="r" threads={[]} selectedThreadId={null} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn<(thread: ThreadView) => void>()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onRepositoryAction={action} />);
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

it("never nests a heading inside an interactive button", () => {
  const view = render(<SidebarHarness repositories={[repository]} selectedRepositoryId="r"
    threads={[thread]} selectedThreadId={thread.id} loading={false}
    onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()}
    onDeleteThread={vi.fn()} onRepositoryAction={vi.fn()} />);
  expect(view.container.querySelector("button h1, button h2, button h3, button h4, button h5, button h6"))
    .toBeNull();
  expect(screen.getByRole("heading", { name: "Projekte" })).toContainElement(
    screen.getByRole("button", { name: /Projekte/ }),
  );
  expect(screen.getByRole("heading", { name: "Threads" })).toContainElement(
    screen.getByRole("button", { name: /Threads/ }),
  );
});

it("persists independent collapse state and keyboard splitter size", async () => {
  const view = render(<SidebarHarness repositories={[repository]} selectedRepositoryId="r" threads={[thread]} selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onRepositoryAction={vi.fn()} />);
  const splitter = screen.getByRole("separator", { name: "Höhe der Projektliste" });
  fireEvent.keyDown(splitter, { key: "End" });
  expect(splitter).toHaveAttribute("aria-valuenow", "520");
  fireEvent.keyDown(splitter, { key: "ArrowUp" });
  expect(splitter).toHaveAttribute("aria-valuenow", "504");
  await waitFor(() => expect(localStorage.getItem("codex-workbench.ui.v2")).toContain('"projectsHeight":504'));
  fireEvent.click(screen.getByRole("button", { name: /Projekte/ }));
  expect(document.getElementById("project-list")).toHaveAttribute("hidden");
  expect(screen.queryByRole("separator", { name: "Höhe der Projektliste" })).not.toBeInTheDocument();
  expect(document.getElementById("repository-sidebar")).toHaveClass("sections-projects-closed-threads-open");
  fireEvent.click(screen.getByRole("button", { name: /Threads/ }));
  expect(document.getElementById("repository-sidebar")).toHaveClass("sections-projects-closed-threads-closed");
  fireEvent.click(screen.getByRole("button", { name: /Projekte/ }));
  expect(document.getElementById("repository-sidebar")).toHaveClass("sections-projects-open-threads-closed");

  view.unmount();
  render(<SidebarHarness repositories={[repository]} selectedRepositoryId="r" threads={[thread]} selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onRepositoryAction={vi.fn()} />);
  expect(screen.queryByRole("separator", { name: "Höhe der Projektliste" })).not.toBeInTheDocument();
  expect(document.getElementById("thread-list")).toHaveAttribute("hidden");
});

it("ends pointer resize on cancel and supports double-click reset", () => {
  render(<SidebarHarness repositories={[repository]} selectedRepositoryId="r" threads={[thread]}
    selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()}
    onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()}
    onRepositoryAction={vi.fn()} />);
  const splitter = screen.getByRole("separator", { name: "Höhe der Projektliste" });
  Object.assign(splitter, {
    setPointerCapture: vi.fn(), releasePointerCapture: vi.fn(), hasPointerCapture: () => true,
  });
  fireEvent.pointerDown(splitter, { pointerId: 4, clientY: 100 });
  fireEvent.pointerMove(splitter, { pointerId: 4, clientY: 140 });
  expect(splitter).toHaveAttribute("aria-valuenow", "320");
  fireEvent.pointerCancel(splitter, { pointerId: 4 });
  fireEvent.pointerMove(splitter, { pointerId: 4, clientY: 200 });
  expect(splitter).toHaveAttribute("aria-valuenow", "320");
  fireEvent.doubleClick(splitter);
  expect(splitter).toHaveAttribute("aria-valuenow", "280");
});

it("temporarily reveals collapsed search matches and exposes title actions", () => {
  const edit = vi.fn();
  render(<SidebarHarness repositories={[repository]} selectedRepositoryId="r" threads={[thread]} selectedThreadId={thread.id} loading={false} onSelectRepository={vi.fn()} onSelectThread={vi.fn()} onNewThread={vi.fn()} onDeleteThread={vi.fn()} onEditThread={edit} onRepositoryAction={vi.fn()} />);
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
