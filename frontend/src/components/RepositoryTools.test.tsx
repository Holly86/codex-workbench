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
  render(<RepositoryTools action="delete" repository={repository} jobs={[]} github={null}
    pending={false} removalPreview={preview} onClose={() => undefined} onSubmit={onSubmit} onRetryJob={() => Promise.resolve()} />);

  expect(screen.getByText("codex-workbench", { selector: "code" })).toBeInTheDocument();
  const deleteThreads = screen.getByRole("checkbox", { name: /Threads ebenfalls löschen/ });
  expect(deleteThreads).not.toBeChecked();
  const confirm = screen.getByRole("textbox", { name: /exakten Repositorynamen/ });
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
  render(<RepositoryTools action="remove" repository={repository} jobs={[]} github={null}
    pending={false} removalPreview={{ ...preview, blockedReasons: ["active_turn"], canRemove: false }}
    onClose={() => undefined} onSubmit={() => Promise.resolve()} onRetryJob={() => Promise.resolve()} />);
  expect(screen.getByRole("alert")).toHaveTextContent(/Turn oder Git-Job/);
  expect(screen.getByRole("button", { name: "Entfernen" })).toBeDisabled();
});
