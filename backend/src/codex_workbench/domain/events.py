"""Normalized internal events independent of raw JSON-RPC payload shapes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from codex_workbench.domain.models import (
    AgentActivity,
    ApprovalRequestDescriptor,
    DomainModel,
    UsageSnapshot,
)


class WorkbenchEvent(DomainModel):
    sequence: Annotated[int, Field(ge=0)]
    occurred_at: datetime
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None


class AssistantTextDeltaEvent(WorkbenchEvent):
    type: Literal["assistant_text_delta"] = "assistant_text_delta"
    delta: str


class ReasoningSummaryDeltaEvent(WorkbenchEvent):
    type: Literal["reasoning_summary_delta"] = "reasoning_summary_delta"
    summary_index: Annotated[int, Field(ge=0)]
    delta: str


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanStep(DomainModel):
    step: str
    status: PlanStepStatus


class PlanUpdatedEvent(WorkbenchEvent):
    type: Literal["plan_updated"] = "plan_updated"
    explanation: str | None = None
    plan: tuple[PlanStep, ...]


class ToolActivityKind(StrEnum):
    """Workbench classification, not a Codex protocol enum."""

    READING = "reading"
    SEARCHING = "searching"
    EXECUTING = "executing"
    EDITING = "editing"
    TESTING = "testing"


class ToolActivityEvent(WorkbenchEvent):
    type: Literal["tool_activity"] = "tool_activity"
    kind: ToolActivityKind
    tool: str | None = None
    summary: str
    status: Literal["started", "updated", "completed", "failed"]


class ApprovalRequestedEvent(WorkbenchEvent):
    type: Literal["approval_requested"] = "approval_requested"
    approval: ApprovalRequestDescriptor


class ApprovalResolvedEvent(WorkbenchEvent):
    type: Literal["approval_resolved"] = "approval_resolved"
    approval_id: str


class UsageUpdatedEvent(WorkbenchEvent):
    type: Literal["usage_updated"] = "usage_updated"
    usage: UsageSnapshot


class ActivityUpdatedEvent(WorkbenchEvent):
    type: Literal["activity_updated"] = "activity_updated"
    activity: AgentActivity


class TurnLifecycleEvent(WorkbenchEvent):
    type: Literal["turn_lifecycle"] = "turn_lifecycle"
    status: Literal["started", "completed", "failed", "cancelled"]


class ConnectionState(StrEnum):
    """Workbench transport state; reconnecting is not an app-server notification."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class ConnectionStateEvent(WorkbenchEvent):
    type: Literal["connection_state"] = "connection_state"
    state: ConnectionState
    message: str | None = None


class ErrorEvent(WorkbenchEvent):
    type: Literal["error"] = "error"
    code: str
    message: str
    retrying: bool = False


class CompatibilityUnknownEvent(WorkbenchEvent):
    """Retain method identity without forwarding an unreviewed raw payload."""

    type: Literal["compatibility_unknown"] = "compatibility_unknown"
    protocol_method: str


NormalizedWorkbenchEvent = (
    AssistantTextDeltaEvent
    | ReasoningSummaryDeltaEvent
    | PlanUpdatedEvent
    | ToolActivityEvent
    | ApprovalRequestedEvent
    | ApprovalResolvedEvent
    | UsageUpdatedEvent
    | ActivityUpdatedEvent
    | TurnLifecycleEvent
    | ConnectionStateEvent
    | ErrorEvent
    | CompatibilityUnknownEvent
)
