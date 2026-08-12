import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { Repository, RepositoryRemovalPreview } from "../workbench/types";
import { RepositoryTools } from "./RepositoryTools";

const repository: Repository = {
  id: "0123456789abcdef", name: "Workbench", directoryName: "codex-workbench",
  status: { branch: "main", detached: false, upstream: null, upstreamMissing: true,
    ahead: 0, behind: 0, staged: 0, modified: 1, untracked: 0, conflicts: 0,
    clean: false, changes: [], observedAt: 1 },
  preferences: { model: null, effort: null },
  renameCapabilities: { displayName: true, directory: false, remote: false },
};
const preview: RepositoryRemovalPreview = {
  repositoryId: repository.id, repositoryName: "codex-workbench",
  relativePath: "codex-workbench", threadCount: 3, hasUncommittedChanges: true,
  blockedReasons: [], canRemove: true, canDelete: true,
};

test("requires exact server-projected confirmation and keeps thread deletion opt-in", () => {
  const onSubmit = vi.fn(() => Promise.resolve());
  render(<RepositoryTools action="delete" repository={repository} github={null}
    pending={false} removalPreview={preview} onClose={() => undefined} onSubmit={onSubmit} />);

  expect(screen.getByText("codex-workbench", { selector: "code" })).toBeInTheDocument();
  const deleteThreads = screen.getByRole("checkbox", { name: /Threads ebenfalls löschen/ });
  expect(deleteThreads).not.toBeChecked();
  const confirm = screen.getByRole("textbox", { name: /exakten Repositorynamen/i });
  const submit = screen.getByRole("button", { name: "Lokal löschen" });
  expect(submit).toBeDisabled();
  fireEvent.change(confirm, { target: { value: "codex-workbench" } });
  expect(submit).toBeEnabled();
  fireEvent.click(submit);
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    confirmation: "codex-workbench", deleteThreads: false,
  }));
});

test("blocks removal while the server reports active work", () => {
  render(<RepositoryTools action="remove" repository={repository} github={null}
    pending={false} removalPreview={{ ...preview, blockedReasons: ["active_turn"], canRemove: false }}
    onClose={() => undefined} onSubmit={() => Promise.resolve()} />);
  expect(screen.getByRole("alert")).toHaveTextContent(/Aktive Turns, Jobs/);
  expect(screen.getByRole("button", { name: "Aus Workbench entfernen" })).toBeDisabled();
});

test("renders Git status as structured semantics with raw data collapsed", () => {
  render(<RepositoryTools action="status" repository={repository} github={null}
    pending={false} removalPreview={null} onClose={() => undefined}
    onSubmit={() => Promise.resolve()} />);
  expect(screen.getByText("Arbeitsbaum")).toBeInTheDocument();
  expect(screen.getByText(/1 geändert/)).toBeInTheDocument();
  expect(screen.getByText("Technische Details")).toBeInTheDocument();
  expect(screen.queryByText(/"branch": "main"/)).not.toBeVisible();
});

test("uses compact branch choices, searchable groups, and a specific action label", async () => {
  render(<RepositoryTools action="branches" repository={repository} github={null}
    pending={false} removalPreview={null} onClose={() => undefined}
    onLoadBranches={() => Promise.resolve({
      local: [{ name: "main", current: true }, { name: "feature/local", current: false }],
      remote: [{ name: "origin/release", current: false }],
    })}
    onSubmit={() => Promise.resolve()} />);
  expect(screen.getByRole("radio", { name: "Neuen lokalen Branch erstellen" })).toBeChecked();
  expect(screen.getByRole("button", { name: "Branch erstellen" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Branchname" }), { target: { value: "feature/new" } });
  expect(screen.getByRole("button", { name: "Branch erstellen" })).toBeEnabled();
  fireEvent.click(screen.getByRole("radio", { name: "Zu vorhandenem Branch wechseln" }));
  expect(await screen.findByText("Remote-Branches")).toBeInTheDocument();
  expect(await screen.findByText("origin/release")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Branch wechseln" })).toBeDisabled();
});
