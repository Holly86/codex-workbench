export type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";
export type ApprovalPolicy = "untrusted" | "on-request" | "never";

export interface Repository {
  id: string;
  name: string;
  branch?: string | null;
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
}

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
