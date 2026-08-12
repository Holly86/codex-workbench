import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { ApiError } from "../auth/api";
import * as api from "../workbench/api";
import type { DeployKeyView, Repository } from "../workbench/types";
import { DeployKeyDialog } from "./DeployKeyDialog";

vi.mock("../workbench/api", async (load) => {
  const actual = await load<typeof api>();
  return {
    ...actual,
    clonePrivateRepository: vi.fn(),
    connectPrivateRepository: vi.fn(),
    testDeployKey: vi.fn(),
  };
});

const repository = {
  id: "0123456789abcdef", name: "Private", directoryName: "private",
  status: { branch: "main", detached: false, upstream: null, upstreamMissing: true,
    ahead: 0, behind: 0, staged: 0, modified: 0, untracked: 0, conflicts: 0,
    clean: true, changes: [], observedAt: 1 },
  preferences: { model: null, effort: null },
  renameCapabilities: { displayName: true, directory: false, remote: false },
} satisfies Repository;

const deployKey: DeployKeyView = {
  credentialId: "deploy_public_identifier_only_1234567890",
  repositoryId: null,
  githubRepository: "owner/private",
  sshUrl: "git@github.com:owner/private.git",
  status: "configured",
  active: { publicKey: "ssh-ed25519 AAAATEST public-comment", fingerprint: "SHA256:test", createdAt: 1 },
  candidate: null,
  retiredKeyAvailable: false,
  lastTestedAt: null,
  lastSuccessfulTestAt: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  });
});

test("reveals only public deploy-key material before a private clone", async () => {
  vi.mocked(api.connectPrivateRepository).mockResolvedValue(deployKey);
  render(<DeployKeyDialog mode="private-clone" repository={repository} csrfToken="csrf"
    repositoryNames={[]} onClose={vi.fn()} onCloneStarted={vi.fn()} />);

  fireEvent.change(screen.getByRole("textbox", { name: /GitHub Repository/ }), {
    target: { value: "owner/private" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Deploy Key erzeugen" }));

  await waitFor(() => expect(screen.getByText("SHA256:test")).toBeInTheDocument());
  expect(screen.getByText(/ssh-ed25519 AAAATEST/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("PRIVATE KEY");
  expect(screen.getByRole("button", { name: /privaten Clone starten/ })).toBeDisabled();
});

test("prevents a known local-name conflict without discarding the deploy key", async () => {
  vi.mocked(api.connectPrivateRepository).mockResolvedValue({ ...deployKey, status: "verified" });
  render(<DeployKeyDialog mode="private-clone" repository={null} csrfToken="csrf"
    repositoryNames={["private"]} onClose={vi.fn()} onCloneStarted={vi.fn()} />);

  fireEvent.change(screen.getByRole("textbox", { name: /GitHub Repository/ }), {
    target: { value: "owner/private" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Deploy Key erzeugen" }));

  await waitFor(() => expect(screen.getByText("Dieser lokale Repositoryname wird bereits verwendet.")).toBeInTheDocument());
  expect(screen.getByText("SHA256:test")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /privaten Clone starten/ })).toBeDisabled();
  expect(api.clonePrivateRepository).not.toHaveBeenCalled();
});

test("keeps a resumed key and local name visible after a server conflict, then retries", async () => {
  const verified = { ...deployKey, status: "verified" as const, lastSuccessfulTestAt: 2 };
  const onClose = vi.fn();
  const onCloneStarted = vi.fn().mockResolvedValue(undefined);
  vi.mocked(api.connectPrivateRepository).mockResolvedValue(verified);
  vi.mocked(api.clonePrivateRepository)
    .mockRejectedValueOnce(new ApiError(409, "repository_name_conflict", "Ein Repository mit diesem lokalen Namen existiert bereits.", null))
    .mockResolvedValueOnce({
      id: "job_retry", repositoryId: null, repositoryName: "private-smoke", type: "git_clone",
      status: "queued", startedAt: 1, endedAt: null, exitCode: null, error: null,
      errorCode: null, retryable: false, operationLabel: "Repository klonen", durationMs: null,
    });
  render(<DeployKeyDialog mode="private-clone" repository={null} csrfToken="csrf"
    repositoryNames={[]} onClose={onClose} onCloneStarted={onCloneStarted} />);

  fireEvent.change(screen.getByRole("textbox", { name: /GitHub Repository/ }), {
    target: { value: "owner/private" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Deploy Key erzeugen" }));
  await waitFor(() => expect(screen.getByText("SHA256:test")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /privaten Clone starten/ }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Ein Repository mit diesem lokalen Namen existiert bereits."));
  expect(screen.getByRole("textbox", { name: "Lokaler Repositoryname" })).toHaveValue("private");
  expect(screen.getByText("SHA256:test")).toBeInTheDocument();

  fireEvent.change(screen.getByRole("textbox", { name: "Lokaler Repositoryname" }), {
    target: { value: "private-smoke" },
  });
  fireEvent.click(screen.getByRole("button", { name: /privaten Clone starten/ }));
  await waitFor(() => expect(api.clonePrivateRepository).toHaveBeenLastCalledWith(
    "csrf", "private-smoke", deployKey.credentialId,
  ));
  expect(onCloneStarted).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledOnce();
});
