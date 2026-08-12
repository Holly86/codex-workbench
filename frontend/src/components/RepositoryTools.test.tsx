import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
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
  const submit = vi.fn(() => Promise.resolve());
  render(<RepositoryTools action="branches" repository={repository} github={null}
    pending={false} removalPreview={null} onClose={() => undefined}
    onLoadBranches={() => Promise.resolve({
      local: [
        { id: "refs/heads/main", kind: "local", displayName: "main", fullRef: "refs/heads/main", remoteName: null, remoteBranchName: null, proposedLocalName: null, current: true, disabledReason: "current_branch" },
        { id: "refs/heads/feature/local", kind: "local", displayName: "feature/local", fullRef: "refs/heads/feature/local", remoteName: null, remoteBranchName: null, proposedLocalName: null, current: false, disabledReason: null },
        { id: "refs/heads/release", kind: "local", displayName: "release", fullRef: "refs/heads/release", remoteName: null, remoteBranchName: null, proposedLocalName: null, current: false, disabledReason: null },
      ],
      remote: [
        { id: "refs/remotes/origin/release", kind: "remote", displayName: "origin/release", fullRef: "refs/remotes/origin/release", remoteName: "origin", remoteBranchName: "release", proposedLocalName: "release", current: false, disabledReason: null },
        { id: "refs/remotes/upstream/release", kind: "remote", displayName: "upstream/release", fullRef: "refs/remotes/upstream/release", remoteName: "upstream", remoteBranchName: "release", proposedLocalName: "release", current: false, disabledReason: null },
      ],
    })}
    onSubmit={submit} />);
  expect(screen.getByRole("radio", { name: "Neuen lokalen Branch erstellen" })).toBeChecked();
  expect(screen.getByRole("button", { name: "Branch erstellen" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Branchname" }), { target: { value: "feature/new" } });
  expect(screen.getByRole("button", { name: "Branch erstellen" })).toBeEnabled();
  fireEvent.click(screen.getByRole("radio", { name: "Zu vorhandenem Branch wechseln" }));
  expect(await screen.findByText("Remote-Branches")).toBeInTheDocument();
  expect(await screen.findByText("origin/release")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Branch wechseln" })).toBeDisabled();
  const origin = screen.getByRole("radio", { name: /origin\/release/ });
  const upstream = screen.getByRole("radio", { name: /upstream\/release/ });
  fireEvent.click(origin);
  expect(origin).toBeChecked();
  expect(upstream).not.toBeChecked();
  expect(screen.getByText(/lokaler Tracking-Branch/)).toBeInTheDocument();
  expect(screen.getByText(/lokaler Branch mit diesem Namen existiert bereits/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Lokaler Branchname" }), { target: { value: "origin-release" } });
  fireEvent.click(screen.getByRole("button", { name: "Branch wechseln" }));
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({
    branchTargetId: "refs/remotes/origin/release",
    localBranchName: "origin-release",
    branchOperation: "switch",
  }));
  expect(screen.getByRole("radio", { name: /main/ })).toBeDisabled();
});

test("passes special repository paths unchanged to the commit action", () => {
  const submit = vi.fn(() => Promise.resolve());
  const specialRepository: Repository = {
    ...repository,
    status: {
      ...repository.status,
      staged: 2,
      modified: 1,
      changes: [
        { path: "folder/space name.txt", staged: true, unstaged: false, kind: "modified" },
        { path: "tab\tunicodé.txt", staged: false, unstaged: true, kind: "modified" },
      ],
    },
  };
  render(<RepositoryTools action="commit" repository={specialRepository} github={null}
    pending={false} removalPreview={null} onClose={() => undefined}
    onSubmit={submit} />);

  fireEvent.click(screen.getByRole("checkbox", { name: /folder\/space name\.txt/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /tab\s+unicodé\.txt/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "Commit-Nachricht" }), { target: { value: "Exact paths" } });
  fireEvent.click(screen.getByRole("button", { name: "Commit erstellen" }));

  expect(submit).toHaveBeenCalledWith(expect.objectContaining({
    selected: ["folder/space name.txt", "tab\tunicodé.txt"],
  }));
});

test("loads branches once across parent rerenders and retries one failed request", async () => {
  const loader = vi.fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ local: [], remote: [] });
  const base = <RepositoryTools action="branches" repository={repository} github={null}
    pending={false} removalPreview={null} onClose={() => undefined}
    onLoadBranches={loader} onSubmit={() => Promise.resolve()} />;
  const view = render(base);
  fireEvent.click(screen.getByRole("radio", { name: "Zu vorhandenem Branch wechseln" }));
  expect(await screen.findByText("Branches konnten nicht geladen werden.")).toBeInTheDocument();
  for (let index = 0; index < 100; index += 1) view.rerender(base);
  expect(loader).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
  expect(await screen.findAllByText("Keine passenden Branches.")).toHaveLength(2);
});
