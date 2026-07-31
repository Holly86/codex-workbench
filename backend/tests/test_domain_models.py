from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from codex_workbench.domain.events import ConnectionState, ConnectionStateEvent
from codex_workbench.domain.models import (
    AgentActivityState,
    ApprovalPolicyMode,
    GranularApprovalPolicy,
    ImageInputDescriptor,
    SandboxMode,
)


def test_protocol_derived_permission_values_are_exact() -> None:
    assert [mode.value for mode in SandboxMode] == [
        "read-only",
        "workspace-write",
        "danger-full-access",
    ]
    assert [policy.value for policy in ApprovalPolicyMode] == [
        "untrusted",
        "on-request",
        "never",
    ]
    assert (
        GranularApprovalPolicy(
            sandbox_approval=True,
            rules=False,
            skill_approval=True,
            request_permissions=False,
            mcp_elicitations=True,
        ).sandbox_approval
        is True
    )


def test_activity_states_are_explicit_workbench_aggregations() -> None:
    assert {state.value for state in AgentActivityState} == {
        "idle",
        "starting",
        "thinking",
        "planning",
        "reading",
        "searching",
        "executing",
        "editing",
        "testing",
        "awaiting_approval",
        "responding",
        "completed",
        "failed",
        "cancelled",
        "reconnecting",
    }


def test_image_input_descriptor_never_accepts_a_raw_path() -> None:
    descriptor = ImageInputDescriptor(upload_id="upload_opaque")
    assert descriptor.model_dump() == {"upload_id": "upload_opaque", "detail": "auto"}
    with pytest.raises(ValidationError):
        ImageInputDescriptor.model_validate(
            {"upload_id": "upload_opaque", "path": "/untrusted/image.png"}
        )


def test_normalized_connection_event_is_not_a_raw_protocol_payload() -> None:
    event = ConnectionStateEvent(
        sequence=1,
        occurred_at=datetime.now(UTC),
        state=ConnectionState.RECONNECTING,
    )
    assert event.type == "connection_state"
    assert event.state is ConnectionState.RECONNECTING
