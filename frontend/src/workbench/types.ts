export type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";
export type ApprovalPolicy = "untrusted" | "on-request" | "never";

export interface Repository {
  id: string;
  name: string;
  directoryName: string;
  status: GitStatus;
  preferences: { model: string | null; effort: string | null };
  renameCapabilities: { displayName: boolean; directory: boolean; remote: boolean };
}

export interface GitChange { path: string; staged: boolean; kind: string }
export interface GitStatus {
  branch: string | null; detached: boolean; upstream: string | null; upstreamMissing: boolean;
  ahead: number; behind: number; staged: number; modified: number; untracked: number;
  conflicts: number; clean: boolean; changes: GitChange[]; observedAt: number;
}
export interface NativeJob {
  id: string; repositoryId: string | null; repositoryName: string;
  type: "git_clone" | "git_fetch" | "git_pull" | "git_push" | "github_publish";
  status: "queued" | "running" | "succeeded" | "failed"; startedAt: number;
  endedAt: number | null; exitCode: number | null; output: string; error: string | null;
  command: string; durationMs: number | null;
}
export interface GitHubCapability {
  available: boolean; authenticated: boolean; account?: string | null; reason?: string | null;
  published?: boolean; remoteUrl?: string | null; webUrl?: string | null;
}

export interface DeployKeyMaterial {
  publicKey: string;
  fingerprint: string;
  createdAt: number;
}

export interface DeployKeyView {
  credentialId: string;
  repositoryId: string | null;
  githubRepository: string;
  sshUrl: string;
  status: "configured" | "verified" | "failed" | "rotation_verified";
  active: DeployKeyMaterial;
  candidate: DeployKeyMaterial | null;
  retiredKeyAvailable: boolean;
  lastTestedAt: number | null;
  lastSuccessfulTestAt: number | null;
}

export interface DeployKeyRemotePreview {
  currentUrl: string;
  newUrl: string;
}

export interface RepositoryRemovalPreview {
  repositoryId: string;
  repositoryName: string;
  relativePath: string;
  threadCount: number;
  hasUncommittedChanges: boolean;
  blockedReasons: Array<"active_turn" | "active_job" | "turn_state_unknown">;
  canRemove: boolean;
  canDelete: boolean;
}

export interface ModelCapability {
  id: string;
  displayName: string;
  description: string;
  isDefault: boolean;
  defaultReasoningEffort: string;
  reasoningEfforts: Array<{ value: string; description: string }>;
  inputModalities: string[];
}

export interface WorkbenchCapabilities {
  models: ModelCapability[];
  skills: Array<{ name: string; description: string; enabled: boolean }>;
  plugins: Array<{ id: string; name: string; installed: boolean; enabled: boolean }>;
  native: { models: boolean; skills: boolean; plugins: boolean; apps: boolean };
  status: {
    overall: "ready" | "partial" | "unavailable";
    generation: number;
    cached: boolean;
    stale: boolean;
    models: CapabilityAvailability;
    skills: CapabilityAvailability;
    plugins: CapabilityAvailability;
  };
  host?: { sandbox: Record<"workspaceWrite" | "readOnly" | "fullAccess", { available: boolean; reason: string | null }> };
}

export interface CapabilityAvailability { available: boolean; reason: string | null }

export type ActivityState =
  | "idle"
  | "starting"
  | "thinking"
  | "planning"
  | "reading"
  | "searching"
  | "executing"
  | "editing"
  | "testing"
  | "awaiting_approval"
  | "responding"
  | "completed"
  | "failed"
  | "cancelled"
  | "reconnecting";

export interface Activity {
  state: ActivityState;
  label: string;
  last_activity: string | null;
  started_at: string | null;
  observed_at: string;
  elapsed_ms: number | null;
  current_tool: string | null;
  current_command: string | null;
  pending_approval_id: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ThreadItem {
  id: string;
  type: string;
  text?: string;
  summary?: string | string[];
  content?: Array<{ type: string; text?: string }>;
  command?: string;
  cwdDisplay?: string | null;
  status?: string;
  output?: string;
  exitCode?: number | null;
  durationMs?: number | null;
  tool?: string;
  changes?: Array<{ path: string | null; kind: string; diff: string }>;
  plan?: Array<{ step: string; status: string }>;
}

export interface Turn {
  id: string;
  status: string;
  items: ThreadItem[];
}

export interface TokenBreakdown {
  totalTokens: number;
  inputTokens: number;
  cachedInputTokens: number;
  cacheWriteInputTokens: number;
  outputTokens: number;
  reasoningOutputTokens: number;
}

export interface Usage {
  context: {
    usedTokens: number | null;
    limitTokens: number | null;
    usedPercent: number | null;
    derivation: string | null;
  };
  latestModelUsage: TokenBreakdown | null;
  threadTotal: TokenBreakdown | null;
  observedAt: number;
}

export interface ThreadView {
  id: string;
  repositoryId: string;
  repositoryName: string;
  preview: string;
  name: string | null;
  status: string;
  sandboxMode: SandboxMode;
  approvalPolicy: ApprovalPolicy;
  fullAccess: boolean;
  createdAt: number;
  updatedAt: number;
  activeTurnId: string | null;
  turns: Turn[];
  usage: Usage | null;
  activity: Activity;
  latestDiff: string | null;
  repositoryAvailable?: boolean;
}

export type ApprovalDecision =
  | "accept"
  | "accept_for_session"
  | "accept_with_execpolicy_amendment"
  | "apply_network_policy_amendment"
  | "decline"
  | "cancel";

export interface Approval {
  id: string;
  kind: "command_execution" | "file_change" | "permissions";
  state: string;
  threadId: string;
  turnId: string | null;
  itemId: string | null;
  requestedAt: string;
  expiresAt: string;
  summary: string;
  reason: string | null;
  command: string | null;
  cwdDisplay: string | null;
  writableRootsDisplay: string[];
  availableDecisions: ApprovalDecision[];
}

export interface RateWindow {
  usedPercent: number | null;
  windowDurationMins: number | null;
  resetsAt: number | null;
}

export interface RateLimits {
  available: boolean;
  limitId?: string | null;
  limitName?: string | null;
  primary?: RateWindow | null;
  secondary?: RateWindow | null;
  credits?: { hasCredits: boolean | null; unlimited: boolean | null; balance: string | null } | null;
  individualLimit?: {
    limit: string | null;
    used: string | null;
    remainingPercent: number | null;
    resetsAt: number | null;
  } | null;
  spendControlReached?: boolean | null;
  rateLimitReachedType?: string | null;
  observedAt: number;
}

export interface Snapshot {
  connection: { state: string; generation: number; message: string | null };
  threads: ThreadView[];
  pendingApprovals: Approval[];
  rateLimits: RateLimits;
  jobs?: NativeJob[];
  unavailableThreads?: Array<{ id: string; repositoryId: string; status: "unavailable"; reason: string }>;
}

export interface FullAccessCapability {
  confirmationId: string;
  scopeId: string;
  expiresAt: string;
  repository: string;
  sandboxMode: "danger-full-access";
  approvalPolicy: ApprovalPolicy;
}

export interface UploadedImage {
  id: string;
  displayName: string;
  detectedMime: string;
  sizeBytes: number;
  bytesReceived: number;
  state: "ready";
  createdAt: number;
  expiresAt: number;
}
