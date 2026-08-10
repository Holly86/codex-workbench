import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import * as api from "../workbench/api";
import type { DeployKeyView, Repository } from "../workbench/types";
import { DeployKeyDialog } from "./DeployKeyDialog";

vi.mock("../workbench/api", async (load) => {
  const actual = await load<typeof api>();
  return { ...actual, connectPrivateRepository: vi.fn(), testDeployKey: vi.fn() };
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
    onClose={vi.fn()} onCloneStarted={vi.fn()} />);

  fireEvent.change(screen.getByRole("textbox", { name: /GitHub Repository/ }), {
    target: { value: "owner/private" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Deploy Key erzeugen" }));

  await waitFor(() => expect(screen.getByText("SHA256:test")).toBeInTheDocument());
  expect(screen.getByText(/ssh-ed25519 AAAATEST/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("PRIVATE KEY");
  expect(screen.getByRole("button", { name: /privaten Clone starten/ })).toBeDisabled();
});
