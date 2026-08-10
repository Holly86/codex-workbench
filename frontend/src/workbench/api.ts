import { ApiError } from "../auth/api";
import type {
  ApprovalDecision,
  ApprovalPolicy,
  FullAccessCapability,
  RateLimits,
  Repository,
  SandboxMode,
  ThreadView,
  UploadedImage,
  WorkbenchCapabilities,
  RepositoryRemovalPreview,
  GitHubCapability,
  GitStatus,
  NativeJob,
  DeployKeyRemotePreview,
  DeployKeyView,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseErrorBody(value: unknown): { code: string; message: string } {
  if (isRecord(value) && isRecord(value.error)) {
    return {
      code: typeof value.error.code === "string" ? value.error.code : "request_failed",
      message:
        typeof value.error.message === "string"
          ? value.error.message
          : "Die Anfrage ist fehlgeschlagen.",
    };
  }
  return { code: "request_failed", message: "Die Anfrage ist fehlgeschlagen." };
}

async function requestJson(
  path: string,
  init: RequestInit,
  csrfToken?: string,
): Promise<unknown> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken !== undefined) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "same-origin",
  });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // The generic protocol error below is safer than exposing a malformed body.
  }
  if (!response.ok) {
    const error = parseErrorBody(body);
    const retry = Number(response.headers.get("retry-after"));
    throw new ApiError(
      response.status,
      error.code,
      error.message,
      Number.isFinite(retry) ? retry : null,
    );
  }
  return body;
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ApiError(502, "invalid_response", "Ungültige Serverantwort.", null);
  }
  return value;
}

function parseRepositories(value: unknown): Repository[] {
  const body = requireRecord(value);
  if (!Array.isArray(body.repositories)) {
    throw new ApiError(502, "invalid_response", "Ungültige Repository-Antwort.", null);
  }
  return body.repositories.flatMap((entry) => {
    if (!isRecord(entry) || typeof entry.id !== "string" || typeof entry.name !== "string") {
      return [];
    }
    const status = isRecord(entry.status) ? entry.status : {
      branch: null, detached: false, upstream: null, upstreamMissing: true, ahead: 0, behind: 0,
      staged: 0, modified: 0, untracked: 0, conflicts: 0, clean: true, changes: [], observedAt: 0,
    };
    return [{ ...entry, directoryName: typeof entry.directoryName === "string" ? entry.directoryName : entry.name,
      status, preferences: isRecord(entry.preferences) ? entry.preferences : { model: null, effort: null },
      renameCapabilities: isRecord(entry.renameCapabilities) ? entry.renameCapabilities : { displayName: true, directory: false, remote: false },
    } as unknown as Repository];
  });
}

function parseThread(value: unknown): ThreadView {
  const thread = requireRecord(value);
  if (
    typeof thread.id !== "string" ||
    typeof thread.repositoryId !== "string" ||
    typeof thread.repositoryName !== "string" ||
    typeof thread.status !== "string" ||
    !Array.isArray(thread.turns) ||
    !isRecord(thread.activity)
  ) {
    throw new ApiError(502, "invalid_response", "Ungültige Thread-Antwort.", null);
  }
  const sandboxMode = thread.sandboxMode;
  const approvalPolicy = thread.approvalPolicy;
  if (
    !["read-only", "workspace-write", "danger-full-access"].includes(String(sandboxMode)) ||
    !["untrusted", "on-request", "never"].includes(String(approvalPolicy))
  ) {
    throw new ApiError(502, "invalid_response", "Ungültige Berechtigungsauswahl.", null);
  }
  return thread as unknown as ThreadView;
}

function parseThreadBody(value: unknown): ThreadView {
  return parseThread(requireRecord(value).thread);
}

export async function listRepositories(signal?: AbortSignal): Promise<Repository[]> {
  return parseRepositories(await requestJson("/api/repositories", { method: "GET", signal }));
}

export async function readCapabilities(repositoryId: string): Promise<WorkbenchCapabilities> {
  const value = requireRecord(await requestJson(
    `/api/capabilities?repositoryId=${encodeURIComponent(repositoryId)}`,
    { method: "GET" },
  ));
  if (!Array.isArray(value.models) || !Array.isArray(value.skills) || !Array.isArray(value.plugins)) {
    throw new ApiError(502, "invalid_response", "Ungültige Capability-Antwort.", null);
  }
  return value as unknown as WorkbenchCapabilities;
}

export async function gitStatus(repositoryId: string): Promise<GitStatus> {
  return requireRecord(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/git/status`, { method: "GET" },
  )) as unknown as GitStatus;
}

export async function gitAction(
  csrfToken: string, repositoryId: string, action: string,
  options: { files?: string[]; message?: string; branch?: string } = {},
): Promise<Record<string, unknown>> {
  return requireRecord(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/git`,
    { method: "POST", body: JSON.stringify({ action, files: options.files ?? [], message: options.message ?? null, branch: options.branch ?? null }) }, csrfToken,
  ));
}

export async function createRepository(csrfToken: string, name: string, readme: boolean): Promise<void> {
  await requestJson("/api/repositories/create", { method: "POST", body: JSON.stringify({ name, readme }) }, csrfToken);
}

export async function cloneRepository(csrfToken: string, name: string, url: string): Promise<NativeJob> {
  return requireRecord(await requestJson("/api/repositories/clone", {
    method: "POST", body: JSON.stringify({ name, url, readme: false }),
  }, csrfToken)) as unknown as NativeJob;
}

export async function cloneRepositoryPreflight(
  csrfToken: string, url: string,
): Promise<{ allowed: boolean; mode: "public_https" | "github_cli" | "ssh" | "unavailable"; message: string }> {
  return requireRecord(await requestJson("/api/repositories/clone/preflight", {
    method: "POST", body: JSON.stringify({ url }),
  }, csrfToken)) as { allowed: boolean; mode: "public_https" | "github_cli" | "ssh" | "unavailable"; message: string };
}

function parseDeployKey(value: unknown): DeployKeyView {
  const deployKey = requireRecord(requireRecord(value).deployKey);
  if (
    typeof deployKey.credentialId !== "string" ||
    typeof deployKey.githubRepository !== "string" ||
    typeof deployKey.sshUrl !== "string" ||
    !isRecord(deployKey.active) ||
    typeof deployKey.active.publicKey !== "string" ||
    typeof deployKey.active.fingerprint !== "string"
  ) {
    throw new ApiError(502, "invalid_response", "Ungültige Deploy-Key-Antwort.", null);
  }
  return deployKey as unknown as DeployKeyView;
}

export async function readRepositoryDeployKey(
  repositoryId: string,
): Promise<DeployKeyView | null> {
  const body = requireRecord(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/deploy-key`,
    { method: "GET" },
  ));
  return body.deployKey === null ? null : parseDeployKey(body);
}

export async function createRepositoryDeployKey(
  csrfToken: string, repositoryId: string,
): Promise<DeployKeyView> {
  return parseDeployKey(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/deploy-key`,
    { method: "POST", body: "{}" }, csrfToken,
  ));
}

export async function connectPrivateRepository(
  csrfToken: string, githubRepository: string,
): Promise<DeployKeyView> {
  return parseDeployKey(await requestJson(
    "/api/repositories/private-connect",
    { method: "POST", body: JSON.stringify({ githubRepository }) }, csrfToken,
  ));
}

export async function clonePrivateRepository(
  csrfToken: string, name: string, credentialId: string,
): Promise<NativeJob> {
  return requireRecord(await requestJson(
    "/api/repositories/private-clone",
    { method: "POST", body: JSON.stringify({ name, credentialId, readme: false }) }, csrfToken,
  )) as unknown as NativeJob;
}

export async function testDeployKey(
  csrfToken: string, credentialId: string, candidate = false,
): Promise<{ succeeded: boolean; deployKey: DeployKeyView }> {
  const body = requireRecord(await requestJson(
    `/api/deploy-keys/${encodeURIComponent(credentialId)}/test`,
    { method: "POST", body: JSON.stringify({ candidate }) }, csrfToken,
  ));
  return { succeeded: body.succeeded === true, deployKey: parseDeployKey({ deployKey: body.deployKey }) };
}

async function deployKeyMutation(
  csrfToken: string,
  credentialId: string,
  suffix: "rotate" | "promote",
): Promise<DeployKeyView> {
  return parseDeployKey(await requestJson(
    `/api/deploy-keys/${encodeURIComponent(credentialId)}/${suffix}`,
    { method: "POST", body: "{}" }, csrfToken,
  ));
}

export function rotateDeployKey(csrfToken: string, credentialId: string): Promise<DeployKeyView> {
  return deployKeyMutation(csrfToken, credentialId, "rotate");
}

export function promoteDeployKey(csrfToken: string, credentialId: string): Promise<DeployKeyView> {
  return deployKeyMutation(csrfToken, credentialId, "promote");
}

export async function removeRetiredDeployKey(
  csrfToken: string, credentialId: string,
): Promise<DeployKeyView> {
  return parseDeployKey(await requestJson(
    `/api/deploy-keys/${encodeURIComponent(credentialId)}/retired`,
    { method: "DELETE", body: "{}" }, csrfToken,
  ));
}

export async function removeDeployKey(csrfToken: string, credentialId: string): Promise<void> {
  await requestJson(
    `/api/deploy-keys/${encodeURIComponent(credentialId)}`,
    { method: "DELETE", body: "{}" }, csrfToken,
  );
}

export async function deployKeyRemotePreview(
  repositoryId: string,
): Promise<DeployKeyRemotePreview> {
  return requireRecord(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/deploy-key/remote-preview`,
    { method: "GET" },
  )) as unknown as DeployKeyRemotePreview;
}

export async function switchOriginToDeployKey(
  csrfToken: string, repositoryId: string, confirmation: string,
): Promise<void> {
  await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/deploy-key/switch-origin`,
    { method: "POST", body: JSON.stringify({ confirmation }) }, csrfToken,
  );
}

export async function updateRepository(
  csrfToken: string, repositoryId: string,
  values: { displayName?: string | null; model?: string | null; effort?: string | null },
): Promise<Repository> {
  return requireRecord(await requestJson(`/api/repositories/${encodeURIComponent(repositoryId)}`, {
    method: "PATCH", body: JSON.stringify(values),
  }, csrfToken)) as unknown as Repository;
}

export async function repositoryRemovalPreview(repositoryId: string): Promise<RepositoryRemovalPreview> {
  return requireRecord(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/removal-preview`,
    { method: "GET" },
  )) as unknown as RepositoryRemovalPreview;
}

export async function removeRepository(
  csrfToken: string,
  repositoryId: string,
  mode: "remove" | "delete",
  confirmation: string,
  deleteThreads: boolean,
): Promise<void> {
  await requestJson(`/api/repositories/${encodeURIComponent(repositoryId)}/${mode}`, {
    method: "POST",
    body: JSON.stringify({ confirmation, deleteThreads }),
  }, csrfToken);
}

export async function listJobs(): Promise<NativeJob[]> {
  const body = requireRecord(await requestJson("/api/jobs", { method: "GET" }));
  return Array.isArray(body.jobs) ? body.jobs as NativeJob[] : [];
}

export async function githubCapability(repositoryId: string): Promise<GitHubCapability> {
  return requireRecord(await requestJson(
    `/api/github/capability?repositoryId=${encodeURIComponent(repositoryId)}`, { method: "GET" },
  )) as unknown as GitHubCapability;
}

export async function publishGithub(
  csrfToken: string, repositoryId: string,
  values: { owner: string; name: string; visibility: "private" | "public"; initialPush: boolean; publicConfirmation: string | null },
): Promise<NativeJob> {
  return requireRecord(await requestJson(
    `/api/repositories/${encodeURIComponent(repositoryId)}/github/publish`,
    { method: "POST", body: JSON.stringify({ ...values, remote: "origin" }) }, csrfToken,
  )) as unknown as NativeJob;
}

export async function listThreads(repositoryId: string, signal?: AbortSignal): Promise<ThreadView[]> {
  const body = requireRecord(
    await requestJson(`/api/threads?repositoryId=${encodeURIComponent(repositoryId)}`, {
      method: "GET",
      signal,
    }),
  );
  if (!Array.isArray(body.threads)) {
    throw new ApiError(502, "invalid_response", "Ungültige Thread-Liste.", null);
  }
  return body.threads.map(parseThread);
}

export async function startThread(
  csrfToken: string,
  repositoryId: string,
  sandboxMode: SandboxMode,
  approvalPolicy: ApprovalPolicy,
  capability: FullAccessCapability | null,
): Promise<ThreadView> {
  const payload: Record<string, unknown> = {
    repositoryId,
    sandboxMode,
    approvalPolicy,
  };
  if (sandboxMode === "danger-full-access") {
    if (capability === null) {
      throw new Error("Full Access erfordert eine gültige Bestätigung.");
    }
    payload.confirmationId = capability.confirmationId;
    payload.fullAccessScopeId = capability.scopeId;
  }
  return parseThreadBody(
    await requestJson(
      "/api/threads",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      csrfToken,
    ),
  );
}

export async function resumeThread(
  csrfToken: string,
  repositoryId: string,
  threadId: string,
  sandboxMode: SandboxMode,
  approvalPolicy: ApprovalPolicy,
  capability: FullAccessCapability | null,
): Promise<ThreadView> {
  const payload: Record<string, unknown> = {
    repositoryId,
    sandboxMode,
    approvalPolicy,
  };
  if (sandboxMode === "danger-full-access") {
    if (capability === null) {
      throw new Error("Full Access erfordert eine gültige Bestätigung.");
    }
    payload.confirmationId = capability.confirmationId;
  }
  return parseThreadBody(
    await requestJson(
      `/api/threads/${encodeURIComponent(threadId)}/resume`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      csrfToken,
    ),
  );
}

export async function readThread(threadId: string): Promise<ThreadView> {
  return parseThreadBody(
    await requestJson(`/api/threads/${encodeURIComponent(threadId)}`, { method: "GET" }),
  );
}

export async function startTurn(
  csrfToken: string,
  threadId: string,
  message: string,
  images: Array<{ uploadId: string; detail: "auto" | "low" | "high" | "original" }>,
  sandboxMode: SandboxMode,
  approvalPolicy: ApprovalPolicy,
  confirmationId: string | null,
  model: string | null,
  effort: string | null,
): Promise<void> {
  const payload: Record<string, unknown> = {
    message,
    images,
    sandboxMode,
    approvalPolicy,
    model,
    effort,
    idempotencyKey: crypto.randomUUID(),
  };
  if (sandboxMode === "danger-full-access" && confirmationId !== null) {
    payload.confirmationId = confirmationId;
  }
  await requestJson(
    `/api/threads/${encodeURIComponent(threadId)}/turns`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    csrfToken,
  );
}

export async function interruptTurn(csrfToken: string, threadId: string): Promise<void> {
  await requestJson(
    `/api/threads/${encodeURIComponent(threadId)}/interrupt`,
    { method: "POST", body: "{}" },
    csrfToken,
  );
}

export async function prepareFullAccess(
  csrfToken: string,
  repositoryId: string,
  approvalPolicy: ApprovalPolicy,
  threadId: string | null,
): Promise<FullAccessCapability> {
  const body = requireRecord(
    await requestJson(
      "/api/permissions/full-access/prepare",
      {
        method: "POST",
        body: JSON.stringify({ repositoryId, approvalPolicy, threadId }),
      },
      csrfToken,
    ),
  );
  if (
    typeof body.confirmationId !== "string" ||
    typeof body.scopeId !== "string" ||
    typeof body.expiresAt !== "string" ||
    typeof body.repository !== "string"
  ) {
    throw new ApiError(502, "invalid_response", "Ungültige Bestätigung.", null);
  }
  return {
    confirmationId: body.confirmationId,
    scopeId: body.scopeId,
    expiresAt: body.expiresAt,
    repository: body.repository,
    sandboxMode: "danger-full-access",
    approvalPolicy,
  };
}

export async function decideApproval(
  csrfToken: string,
  approvalId: string,
  decision: ApprovalDecision,
): Promise<void> {
  await requestJson(
    `/api/approvals/${encodeURIComponent(approvalId)}/decision`,
    { method: "POST", body: JSON.stringify({ decision }) },
    csrfToken,
  );
}

export async function readRateLimits(): Promise<RateLimits> {
  return requireRecord(
    await requestJson("/api/usage/rate-limits", { method: "GET" }),
  ) as unknown as RateLimits;
}

function parseUpload(value: unknown): UploadedImage {
  const upload = requireRecord(requireRecord(value).upload);
  if (
    typeof upload.id !== "string" ||
    typeof upload.displayName !== "string" ||
    typeof upload.detectedMime !== "string" ||
    typeof upload.sizeBytes !== "number" ||
    upload.state !== "ready"
  ) {
    throw new ApiError(502, "invalid_response", "Ungültige Upload-Antwort.", null);
  }
  return upload as unknown as UploadedImage;
}

export interface UploadOperation {
  promise: Promise<UploadedImage>;
  cancel: () => void;
}

export function uploadImage(
  file: File,
  csrfToken: string,
  onProgress: (loaded: number, total: number) => void,
): UploadOperation {
  const request = new XMLHttpRequest();
  const promise = new Promise<UploadedImage>((resolve, reject) => {
    request.open("POST", "/api/uploads");
    request.withCredentials = true;
    request.setRequestHeader("X-CSRF-Token", csrfToken);
    request.upload.addEventListener("progress", (event) => {
      onProgress(event.loaded, event.lengthComputable ? event.total : file.size);
    });
    request.addEventListener("load", () => {
      let body: unknown;
      try {
        body = JSON.parse(request.responseText) as unknown;
      } catch {
        reject(new ApiError(502, "invalid_response", "Ungültige Upload-Antwort.", null));
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        const error = parseErrorBody(body);
        reject(new ApiError(request.status, error.code, error.message, null));
        return;
      }
      try {
        resolve(parseUpload(body));
      } catch (error: unknown) {
        reject(error instanceof Error ? error : new Error("Ungültige Upload-Antwort."));
      }
    });
    request.addEventListener("error", () => {
      reject(new ApiError(0, "upload_network_error", "Der Upload ist fehlgeschlagen.", null));
    });
    request.addEventListener("abort", () => {
      const error = new Error("Upload abgebrochen.");
      error.name = "AbortError";
      reject(error);
    });
    const form = new FormData();
    form.append("file", file, file.name);
    request.send(form);
  });
  return { promise, cancel: () => request.abort() };
}

export async function removeUpload(csrfToken: string, uploadId: string): Promise<void> {
  await requestJson(
    `/api/uploads/${encodeURIComponent(uploadId)}`,
    { method: "DELETE", body: "{}" },
    csrfToken,
  );
}
