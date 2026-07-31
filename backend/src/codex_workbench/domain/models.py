"""Version-reviewed protocol-derived and explicitly internal Workbench models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SandboxMode(StrEnum):
    """Exact Codex 0.146.0 `v2/SandboxMode` values."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class ApprovalPolicyMode(StrEnum):
    """Exact string variants of Codex 0.146.0 `v2/AskForApproval`."""

    UNTRUSTED = "untrusted"
    ON_REQUEST = "on-request"
    NEVER = "never"


class GranularApprovalPolicy(DomainModel):
    """Protocol-derived object variant; runtime use still requires a probe."""

    sandbox_approval: bool
    rules: bool
    skill_approval: bool
    request_permissions: bool
    mcp_elicitations: bool


ApprovalPolicy = ApprovalPolicyMode | GranularApprovalPolicy


class AgentActivityState(StrEnum):
    """Workbench aggregation states; these are not a Codex protocol enum."""

    IDLE = "idle"
    STARTING = "starting"
    THINKING = "thinking"
    PLANNING = "planning"
    READING = "reading"
    SEARCHING = "searching"
    EXECUTING = "executing"
    EDITING = "editing"
    TESTING = "testing"
    AWAITING_APPROVAL = "awaiting_approval"
    RESPONDING = "responding"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONNECTING = "reconnecting"


class AgentActivity(DomainModel):
    state: AgentActivityState
    label: Annotated[str, Field(min_length=1, max_length=120)]
    last_activity: Annotated[str | None, Field(max_length=500)] = None
    started_at: datetime | None = None
    observed_at: datetime
    elapsed_ms: Annotated[int | None, Field(ge=0)] = None
    current_tool: Annotated[str | None, Field(max_length=200)] = None
    current_command: Annotated[str | None, Field(max_length=2000)] = None
    pending_approval_id: Annotated[str | None, Field(max_length=200)] = None
    error_code: Annotated[str | None, Field(max_length=100)] = None
    error_message: Annotated[str | None, Field(max_length=1000)] = None


class ContextCompactionState(StrEnum):
    """Workbench projection of observed compaction lifecycle."""

    UNAVAILABLE = "unavailable"
    NOT_OBSERVED = "not_observed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ContextUsage(DomainModel):
    """Context usage; used tokens and percentage are explicitly derived values."""

    used_tokens: Annotated[int | None, Field(ge=0)] = None
    limit_tokens: Annotated[int | None, Field(gt=0)] = None
    used_percent: Annotated[float | None, Field(ge=0)] = None
    derivation: str | None = None
    compaction: ContextCompactionState = ContextCompactionState.UNAVAILABLE


class TokenUsage(DomainModel):
    """Exact numeric breakdown available in `v2/TokenUsageBreakdown`."""

    total_tokens: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    cached_input_tokens: Annotated[int, Field(ge=0)]
    cache_write_input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    reasoning_output_tokens: Annotated[int, Field(ge=0)]


class RateLimitWindow(DomainModel):
    used_percent: Annotated[float, Field(ge=0)]
    window_duration_minutes: Annotated[int | None, Field(ge=0)] = None
    resets_at: datetime | None = None


class CreditSnapshot(DomainModel):
    has_credits: bool
    unlimited: bool
    balance: str | None = None


class SpendControlSnapshot(DomainModel):
    limit: str
    used: str
    remaining_percent: Annotated[float, Field(ge=0)]
    resets_at: datetime


class RateLimitReachedType(StrEnum):
    RATE_LIMIT_REACHED = "rate_limit_reached"
    WORKSPACE_OWNER_CREDITS_DEPLETED = "workspace_owner_credits_depleted"
    WORKSPACE_MEMBER_CREDITS_DEPLETED = "workspace_member_credits_depleted"
    WORKSPACE_OWNER_USAGE_LIMIT_REACHED = "workspace_owner_usage_limit_reached"
    WORKSPACE_MEMBER_USAGE_LIMIT_REACHED = "workspace_member_usage_limit_reached"


class RateLimitSnapshot(DomainModel):
    """Sanitized projection of the official account rate-limit response."""

    limit_id: str | None = None
    limit_name: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    credits: CreditSnapshot | None = None
    individual_limit: SpendControlSnapshot | None = None
    spend_control_reached: bool | None = None
    reached_type: RateLimitReachedType | None = None
    observed_at: datetime


class UsageSnapshot(DomainModel):
    thread_id: str
    turn_id: str | None = None
    context: ContextUsage
    latest_model_usage: TokenUsage | None = None
    thread_total_usage: TokenUsage | None = None
    account_rate_limit: RateLimitSnapshot | None = None
    observed_at: datetime


class UploadState(StrEnum):
    """Workbench-owned upload lifecycle."""

    STAGING = "staging"
    VALIDATING = "validating"
    READY = "ready"
    CONSUMED = "consumed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class UploadDescriptor(DomainModel):
    id: str
    display_name: Annotated[str, Field(min_length=1, max_length=180)]
    detected_mime: str | None = None
    size_bytes: Annotated[int | None, Field(ge=0)] = None
    bytes_received: Annotated[int, Field(ge=0)] = 0
    sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    state: UploadState
    created_at: datetime
    expires_at: datetime | None = None
    error_code: str | None = None


class ImageDetail(StrEnum):
    """Exact Codex 0.146.0 `ImageDetail` values."""

    AUTO = "auto"
    LOW = "low"
    HIGH = "high"
    ORIGINAL = "original"


class ImageInputDescriptor(DomainModel):
    """Browser-safe image input; it deliberately contains no filesystem path."""

    upload_id: str
    detail: ImageDetail = ImageDetail.AUTO


class ApprovalRequestKind(StrEnum):
    COMMAND_EXECUTION = "command_execution"
    FILE_CHANGE = "file_change"
    PERMISSIONS = "permissions"
    USER_INPUT = "user_input"
    MCP_ELICITATION = "mcp_elicitation"


class ApprovalDecisionKind(StrEnum):
    """Workbench-normalized names for exact schema decision variants."""

    ACCEPT = "accept"
    ACCEPT_FOR_SESSION = "accept_for_session"
    ACCEPT_WITH_EXECPOLICY_AMENDMENT = "accept_with_execpolicy_amendment"
    APPLY_NETWORK_POLICY_AMENDMENT = "apply_network_policy_amendment"
    DECLINE = "decline"
    CANCEL = "cancel"


class ApprovalRequestState(StrEnum):
    """Workbench-owned approval lifecycle."""

    RECEIVED = "received"
    PRESENTED = "presented"
    DECIDING = "deciding"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    FAILED = "failed"


class ApprovalRequestDescriptor(DomainModel):
    """Sanitized browser capability; raw JSON-RPC request ids are excluded."""

    id: str
    kind: ApprovalRequestKind
    state: ApprovalRequestState
    thread_id: str
    turn_id: str | None = None
    item_id: str | None = None
    requested_at: datetime
    expires_at: datetime | None = None
    reason: Annotated[str | None, Field(max_length=1000)] = None
    command: Annotated[str | None, Field(max_length=4000)] = None
    cwd_display: Annotated[str | None, Field(max_length=1000)] = None
    network_host: Annotated[str | None, Field(max_length=500)] = None
    writable_roots_display: tuple[str, ...] = ()
    available_decisions: tuple[ApprovalDecisionKind, ...]
