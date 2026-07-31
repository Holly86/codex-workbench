from __future__ import annotations

import pytest

from codex_workbench.app_server.transport import JsonObject
from codex_workbench.domain.activity import ActivityAggregator
from codex_workbench.domain.approvals import (
    ApprovalRegistry,
    ApprovalReplayError,
    ApprovalUnavailableError,
    RegistryApprovalState,
)
from codex_workbench.domain.capabilities import (
    FullAccessConfirmationError,
    FullAccessRegistry,
)
from codex_workbench.domain.models import AgentActivityState
from codex_workbench.domain.projection import DomainProjection
from codex_workbench.realtime.broker import (
    RealtimeBroker,
    SlowRealtimeClientError,
)


def test_activity_aggregation_is_deterministic_and_terminal() -> None:
    aggregator = ActivityAggregator()
    thread_id = "thread-1"
    assert aggregator.start(thread_id).state is AgentActivityState.STARTING
    reading = aggregator.notification(
        thread_id,
        "item/started",
        {
            "item": {
                "id": "item-1",
                "type": "commandExecution",
                "command": "sed -n 1,20p README.md",
                "commandActions": [
                    {
                        "type": "read",
                        "command": "sed -n 1,20p README.md",
                        "name": "README.md",
                        "path": "/workspace/repositories/sample/README.md",
                    }
                ],
            }
        },
    )
    assert reading.state is AgentActivityState.READING
    waiting = aggregator.approval(thread_id, "approval-1", "Command approval")
    assert waiting.state is AgentActivityState.AWAITING_APPROVAL
    completed = aggregator.notification(
        thread_id,
        "turn/completed",
        {"turn": {"id": "turn-1", "status": "completed"}},
    )
    assert completed.state is AgentActivityState.COMPLETED
    assert completed.current_command is None
    assert completed.pending_approval_id is None


@pytest.mark.asyncio
async def test_realtime_broker_detects_gaps_and_disconnects_slow_clients() -> None:
    broker = RealtimeBroker(history_size=2, subscriber_queue_size=1)
    await broker.publish("session", "one", {})
    await broker.publish("session", "two", {})
    await broker.publish("session", "three", {})

    missed_subscription, replay, missed, latest = await broker.subscribe("session", 0)
    assert missed is True
    assert replay == ()
    assert latest == 3
    await missed_subscription.close()

    replay_subscription, replay, missed, _latest = await broker.subscribe("session", 2)
    assert missed is False
    assert [event.sequence for event in replay] == [3]
    await replay_subscription.close()

    slow, _replay, _missed, _latest = await broker.subscribe("slow", None)
    await broker.publish("slow", "one", {})
    await broker.publish("slow", "two", {})
    assert isinstance(await slow.queue.get(), SlowRealtimeClientError)
    assert slow.closed is True


@pytest.mark.asyncio
async def test_permission_approval_maps_only_server_retained_values_and_rejects_replay() -> None:
    registry = ApprovalRegistry(ttl_seconds=60)
    params: JsonObject = {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "itemId": "item-1",
        "environmentId": None,
        "startedAtMs": 1_700_000_000_000,
        "cwd": "/workspace/repositories/sample",
        "reason": "Needs network",
        "permissions": {
            "network": {"enabled": True},
            "fileSystem": None,
        },
    }
    record = await registry.register(
        generation=3,
        request_id="native-request",
        method="item/permissions/requestApproval",
        params=params,
        session_id="session-1",
        cwd_display="sample",
    )
    assert record is not None
    assert record.available_decisions == ("accept", "decline")
    claim = await registry.claim(
        record.id,
        session_id="session-1",
        generation=3,
        decision="decline",
    )
    assert claim.response == {
        "permissions": {},
        "scope": "turn",
        "strictAutoReview": False,
    }
    await registry.mark_responded(record.id, succeeded=True)
    with pytest.raises(ApprovalReplayError):
        await registry.claim(
            record.id,
            session_id="session-1",
            generation=3,
            decision="accept",
        )


@pytest.mark.asyncio
async def test_expired_approval_is_claimed_once_by_the_expiry_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000.0
    monkeypatch.setattr("codex_workbench.domain.approvals.time.time", lambda: now)
    registry = ApprovalRegistry(ttl_seconds=60)
    record = await registry.register(
        generation=7,
        request_id=42,
        method="item/commandExecution/requestApproval",
        params={
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "command": "make verify",
            "cwd": "/workspace/repositories/example",
        },
        session_id="session-1",
        cwd_display="example",
    )
    assert record is not None

    now = 1_061.0
    with pytest.raises(ApprovalUnavailableError, match="expired"):
        await registry.claim(
            record.id,
            session_id="session-1",
            generation=7,
            decision="decline",
        )

    expired = await registry.expire(record.id)
    assert expired is record
    assert expired.state is RegistryApprovalState.EXPIRED
    assert await registry.expire(record.id) is None
    assert await registry.list_pending("session-1") == ()


@pytest.mark.asyncio
async def test_full_access_confirmation_is_exact_and_one_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000.0
    monkeypatch.setattr("codex_workbench.domain.capabilities.time.time", lambda: now)
    registry = FullAccessRegistry(ttl_seconds=30)
    confirmation = await registry.issue(
        session_id="session-1",
        repository_id="repository-1",
        scope_id="thread-1",
        approval_policy="never",
    )
    with pytest.raises(FullAccessConfirmationError):
        await registry.consume(
            confirmation.id,
            session_id="session-1",
            repository_id="repository-1",
            scope_id="thread-1",
            approval_policy="on-request",
        )
    await registry.consume(
        confirmation.id,
        session_id="session-1",
        repository_id="repository-1",
        scope_id="thread-1",
        approval_policy="never",
    )
    with pytest.raises(FullAccessConfirmationError):
        await registry.consume(
            confirmation.id,
            session_id="session-1",
            repository_id="repository-1",
            scope_id="thread-1",
            approval_policy="never",
        )

    expired = await registry.issue(
        session_id="session-1",
        repository_id="repository-1",
        scope_id="thread-2",
        approval_policy="never",
    )
    now += 31
    with pytest.raises(FullAccessConfirmationError):
        await registry.consume(
            expired.id,
            session_id="session-1",
            repository_id="repository-1",
            scope_id="thread-2",
            approval_policy="never",
        )


def test_sparse_rate_limit_updates_do_not_erase_known_account_metadata() -> None:
    previous: JsonObject = {
        "available": True,
        "limitName": "Codex",
        "primary": {"usedPercent": 50.0, "resetsAt": 1_900_000_000},
        "credits": {"balance": "10", "unlimited": False},
        "observedAt": 1,
    }
    update: JsonObject = {
        "available": True,
        "limitName": None,
        "primary": {"usedPercent": 60.0, "resetsAt": None},
        "credits": None,
        "observedAt": 2,
    }
    merged = DomainProjection._merge_sparse_rate_limits(previous, update)
    assert merged["limitName"] == "Codex"
    assert merged["primary"] == {
        "usedPercent": 60.0,
        "resetsAt": 1_900_000_000,
    }
    assert merged["credits"] == {"balance": "10", "unlimited": False}
    assert merged["observedAt"] == 2
