"""Sanitized thread/turn projection; raw protocol objects never cross this boundary."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.domain.activity import ActivityAggregator
from codex_workbench.domain.models import AgentActivity
from codex_workbench.persistence.metadata import MetadataRepository, ThreadReference
from codex_workbench.realtime.broker import RealtimeBroker
from codex_workbench.security.redaction import sanitize_browser_text


@dataclass(slots=True)
class ProjectedTurn:
    id: str
    status: str
    items: list[JsonObject] = field(default_factory=list)


@dataclass(slots=True)
class ProjectedThread:
    id: str
    repository_id: str
    repository_name: str
    repository_path: Path
    repository_relative_path: str
    session_id: str
    preview: str
    name: str | None
    status: str
    sandbox_mode: str
    approval_policy: str
    created_at: int
    updated_at: int
    active_turn_id: str | None = None
    turns: list[ProjectedTurn] = field(default_factory=list)
    usage: JsonObject | None = None
    latest_diff: str | None = None
    repository_available: bool = True
    title_source: str = "fallback"
    title_updated_at: int = 0
    title_generation_state: str = "idle"
    title_error_code: str | None = None


class DomainProjection:
    def __init__(
        self,
        broker: RealtimeBroker,
        metadata: MetadataRepository,
        *,
        max_output_bytes: int,
    ) -> None:
        self.broker = broker
        self._metadata = metadata
        self._max_output = max_output_bytes
        self._threads: dict[str, ProjectedThread] = {}
        self._activity = ActivityAggregator()
        self._rate_limits: dict[str, JsonObject] = {}
        self._connection: JsonObject = {"state": "disconnected", "generation": 0}
        self._lock = asyncio.Lock()

    async def register_thread(
        self,
        raw_thread: JsonObject,
        *,
        repository_id: str,
        repository_name: str,
        repository_path: Path,
        repository_relative_path: str,
        session_id: str,
        sandbox_mode: str,
        approval_policy: str,
        repository_available: bool = True,
    ) -> ProjectedThread:
        thread_id = self._required_string(raw_thread, "id")
        now = int(time.time())
        created = self._safe_timestamp(raw_thread.get("createdAt")) or now
        updated = self._safe_timestamp(raw_thread.get("updatedAt")) or now
        projection = ProjectedThread(
            id=thread_id,
            repository_id=repository_id,
            repository_name=repository_name,
            repository_path=repository_path,
            repository_relative_path=repository_relative_path,
            session_id=session_id,
            preview=self._safe_text(raw_thread.get("preview"), 500),
            name=self._clean_display_title(self._optional_text(raw_thread.get("name"), 200)),
            status=self._thread_status(raw_thread.get("status")),
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            created_at=created,
            updated_at=updated,
            turns=self._project_turns(raw_thread.get("turns"), repository_path),
            repository_available=repository_available,
        )
        for turn in projection.turns:
            if turn.status == "inProgress":
                projection.active_turn_id = turn.id
        async with self._lock:
            existing = self._threads.get(thread_id)
            if existing is not None and not projection.turns:
                projection.turns = existing.turns
                projection.active_turn_id = existing.active_turn_id
                projection.usage = existing.usage
            self._threads[thread_id] = projection
        self._activity.reconcile(
            thread_id,
            active=projection.active_turn_id is not None,
            status=projection.status,
        )
        await asyncio.to_thread(
            self._metadata.upsert_thread,
            ThreadReference(
                thread_id=thread_id,
                repository_id=repository_id,
                repository_relative_path=repository_relative_path,
                last_session_id=session_id,
                sandbox_mode=sandbox_mode,
                approval_policy=approval_policy,
                created_at=created,
                updated_at=updated,
            ),
        )
        title = await asyncio.to_thread(
            self._metadata.resolve_thread_title,
            thread_id,
            native_title=projection.name,
            fallback_title=self._fallback_title(projection.preview),
            observed_at=updated,
        )
        projection.name = title.display_title
        projection.title_source = title.source
        projection.title_updated_at = title.updated_at
        projection.title_generation_state = title.generation_state
        projection.title_error_code = title.error_code
        return projection

    async def apply_thread_title(
        self,
        thread_id: str,
        *,
        display_title: str,
        source: str,
        updated_at: int,
        generation_state: str = "succeeded",
        error_code: str | None = None,
    ) -> JsonObject:
        async with self._lock:
            thread = self.require_thread(thread_id)
            thread.name = display_title
            thread.title_source = source
            thread.title_updated_at = updated_at
            thread.title_generation_state = generation_state
            thread.title_error_code = error_code
            thread.updated_at = max(thread.updated_at, updated_at)
            return self.thread_browser_dict(thread)

    async def set_permission_selection(
        self, thread_id: str, sandbox_mode: str, approval_policy: str
    ) -> None:
        async with self._lock:
            thread = self._threads.get(thread_id)
            if thread is not None:
                thread.sandbox_mode = sandbox_mode
                thread.approval_policy = approval_policy
                thread.updated_at = int(time.time())

    async def ingest_notification(self, generation: int, method: str, params: JsonObject) -> None:
        thread_id = self._notification_thread_id(params)
        if thread_id is None:
            if method == "account/rateLimits/updated":
                snapshot = params.get("rateLimits")
                if isinstance(snapshot, dict):
                    for session_id in self.session_ids():
                        projected = self._merge_sparse_rate_limits(
                            self._rate_limits.get(session_id),
                            self.project_rate_limits(snapshot),
                        )
                        self._rate_limits[session_id] = projected
                        await self.broker.publish(session_id, "rate_limits.updated", projected)
            return
        thread = self._threads.get(thread_id)
        if thread is None:
            return
        session_id = thread.session_id
        turn_id = self._optional_text(params.get("turnId"), 200)
        item_id: str | None = None
        raw_item = params.get("item")
        if isinstance(raw_item, dict):
            item_id = self._optional_text(raw_item.get("id"), 200)
        else:
            item_id = self._optional_text(params.get("itemId"), 200)

        if method == "thread/name/updated":
            observed_at = int(time.time())
            title = await asyncio.to_thread(
                self._metadata.resolve_thread_title,
                thread.id,
                native_title=self._clean_display_title(
                    self._optional_text(params.get("threadName"), 200)
                ),
                fallback_title=self._fallback_title(thread.preview),
                observed_at=observed_at,
            )
            async with self._lock:
                current = self._threads.get(thread_id)
                if current is None:
                    return
                current.name = title.display_title
                current.title_source = title.source
                current.title_updated_at = title.updated_at
                current.title_generation_state = title.generation_state
                current.title_error_code = title.error_code
                current.updated_at = max(current.updated_at, observed_at)
                thread = current
                session_id = current.session_id
                event_type: str | None = "thread.name"
                payload: JsonObject = {
                    "name": current.name,
                    "titleSource": current.title_source,
                    "titleUpdatedAt": current.title_updated_at,
                    "titleGenerationState": current.title_generation_state,
                    "titleErrorCode": current.title_error_code,
                }
        else:
            event_type, payload = self._apply_notification(thread, method, params)
        activity = self._activity.notification(thread_id, method, params)
        if event_type is not None:
            await self.broker.publish(
                session_id,
                event_type,
                payload,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
            )
        activity_payload = self._activity_browser_dict(activity, thread.repository_path)
        await self.broker.publish(
            session_id,
            "activity.updated",
            activity_payload,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
        )
        if method == "turn/completed":
            await self.broker.publish(
                session_id,
                "thread.snapshot",
                self.thread_browser_dict(thread),
                thread_id=thread_id,
                turn_id=turn_id,
            )

    async def publish_approval(self, thread_id: str, approval: JsonObject) -> None:
        thread = self._threads.get(thread_id)
        if thread is None:
            return
        approval_id = approval.get("id")
        summary = approval.get("summary")
        if isinstance(approval_id, str):
            thread.status = "awaiting_approval"
            activity = self._activity.approval(
                thread_id,
                approval_id,
                summary if isinstance(summary, str) else "Freigabe erforderlich",
            )
            await self.broker.publish(
                thread.session_id,
                "approval.requested",
                approval,
                thread_id=thread_id,
                turn_id=self._optional_text(approval.get("turnId"), 200),
                item_id=self._optional_text(approval.get("itemId"), 200),
            )
            await self.broker.publish(
                thread.session_id,
                "activity.updated",
                self._activity_browser_dict(activity, thread.repository_path),
                thread_id=thread_id,
            )

    async def publish_approval_resolved(self, thread_id: str, approval_id: str) -> None:
        thread = self._threads.get(thread_id)
        if thread is None:
            return
        activity = self._activity.approval_resolved(thread_id, approval_id)
        if thread.active_turn_id is not None:
            thread.status = "running"
        await self.broker.publish(
            thread.session_id,
            "approval.resolved",
            {"approvalId": approval_id},
            thread_id=thread_id,
        )
        await self.broker.publish(
            thread.session_id,
            "activity.updated",
            self._activity_browser_dict(activity, thread.repository_path),
            thread_id=thread_id,
        )

    async def turn_starting(self, thread_id: str) -> None:
        thread = self.require_thread(thread_id)
        activity = self._activity.start(thread_id)
        await self.broker.publish(
            thread.session_id,
            "activity.updated",
            self._activity_browser_dict(activity, thread.repository_path),
            thread_id=thread_id,
        )

    async def turn_start_failed(self, thread_id: str) -> None:
        thread = self.require_thread(thread_id)
        activity = self._activity.fail_start(thread_id)
        thread.active_turn_id = None
        thread.status = "failed"
        await self.broker.publish(
            thread.session_id,
            "activity.updated",
            self._activity_browser_dict(activity, thread.repository_path),
            thread_id=thread_id,
        )

    async def set_rate_limits(self, session_id: str, raw: JsonObject) -> JsonObject:
        rate_limits = raw.get("rateLimits")
        projected = (
            self.project_rate_limits(rate_limits)
            if isinstance(rate_limits, dict)
            else {"available": False, "observedAt": int(time.time())}
        )
        by_id = raw.get("rateLimitsByLimitId")
        if isinstance(by_id, dict):
            projected["byLimitId"] = {
                str(key): self.project_rate_limits(value)
                for key, value in by_id.items()
                if isinstance(key, str) and isinstance(value, dict)
            }
        reset_credits = raw.get("rateLimitResetCredits")
        if isinstance(reset_credits, dict):
            count = reset_credits.get("availableCount")
            projected["resetCreditsAvailable"] = (
                count if self._is_nonnegative_number(count) else None
            )
        self._rate_limits[session_id] = projected
        await self.broker.publish(session_id, "rate_limits.updated", projected)
        return projected

    async def connection_state(
        self, state: str, generation: int, message: str | None = None
    ) -> None:
        payload: JsonObject = {"state": state, "generation": generation, "message": message}
        self._connection = payload
        for thread in tuple(self._threads.values()):
            await self.broker.publish(thread.session_id, "connection.state", payload)

    def snapshot(self, session_id: str, pending_approvals: tuple[JsonObject, ...]) -> JsonObject:
        return {
            "connection": self._connection,
            "threads": [
                self.thread_browser_dict(thread)
                for thread in self._threads.values()
                if thread.session_id == session_id
            ],
            "pendingApprovals": list(pending_approvals),
            "rateLimits": self._rate_limits.get(
                session_id, {"available": False, "observedAt": int(time.time())}
            ),
        }

    def repository_has_active_turn(self, repository_id: str) -> bool:
        return any(
            thread.repository_id == repository_id and thread.active_turn_id is not None
            for thread in self._threads.values()
        )

    def has_active_turns(self) -> bool:
        return any(
            thread.active_turn_id is not None
            or thread.status in {"running", "awaiting_approval"}
            for thread in self._threads.values()
        )

    def thread_has_active_turn(self, thread_id: str) -> bool:
        thread = self._threads.get(thread_id)
        return thread is not None and (
            thread.active_turn_id is not None
            or thread.status in {"running", "awaiting_approval"}
        )

    def forget_thread(self, thread_id: str) -> None:
        self._threads.pop(thread_id, None)
        self._activity.forget(thread_id)

    def mark_repository_unavailable(self, repository_id: str) -> None:
        for thread in self._threads.values():
            if thread.repository_id == repository_id:
                thread.repository_available = False

    def thread_browser_dict(self, thread: ProjectedThread) -> JsonObject:
        turns: list[JsonValue] = []
        for turn in thread.turns:
            items: list[JsonValue] = []
            items.extend(turn.items)
            turns.append({"id": turn.id, "status": turn.status, "items": items})
        return {
            "id": thread.id,
            "repositoryId": thread.repository_id,
            "repositoryName": thread.repository_name,
            "preview": thread.preview,
            "name": thread.name,
            "titleSource": thread.title_source,
            "titleUpdatedAt": thread.title_updated_at,
            "titleGenerationState": thread.title_generation_state,
            "titleErrorCode": thread.title_error_code,
            "status": thread.status,
            "sandboxMode": thread.sandbox_mode,
            "approvalPolicy": thread.approval_policy,
            "fullAccess": thread.sandbox_mode == "danger-full-access",
            "createdAt": thread.created_at,
            "updatedAt": thread.updated_at,
            "activeTurnId": thread.active_turn_id,
            "turns": turns,
            "usage": thread.usage,
            "activity": self._activity_browser_dict(
                self._activity.snapshot(thread.id), thread.repository_path
            ),
            "latestDiff": thread.latest_diff,
            "repositoryAvailable": thread.repository_available,
        }

    def require_thread(self, thread_id: str) -> ProjectedThread:
        thread = self._threads.get(thread_id)
        if thread is None:
            raise ValueError("thread is not loaded")
        return thread

    def session_ids(self) -> tuple[str, ...]:
        return tuple({thread.session_id for thread in self._threads.values()})

    def project_rate_limits(self, raw: JsonObject) -> JsonObject:
        return {
            "available": True,
            "limitId": self._optional_text(raw.get("limitId"), 100),
            "limitName": self._optional_text(raw.get("limitName"), 200),
            "primary": self._rate_window(raw.get("primary")),
            "secondary": self._rate_window(raw.get("secondary")),
            "credits": self._credits(raw.get("credits")),
            "individualLimit": self._spend_limit(raw.get("individualLimit")),
            "spendControlReached": (
                raw.get("spendControlReached")
                if isinstance(raw.get("spendControlReached"), bool)
                else None
            ),
            "rateLimitReachedType": self._optional_text(raw.get("rateLimitReachedType"), 100),
            "observedAt": int(time.time()),
        }

    @classmethod
    def _merge_sparse_rate_limits(
        cls, previous: JsonObject | None, update: JsonObject
    ) -> JsonObject:
        if previous is None:
            return update
        merged: JsonObject = dict(previous)
        for key, value in update.items():
            old = merged.get(key)
            if value is None:
                continue
            if isinstance(old, dict) and isinstance(value, dict):
                merged[key] = cls._merge_sparse_rate_limits(old, value)
            else:
                merged[key] = value
        return merged

    def _apply_notification(
        self, thread: ProjectedThread, method: str, params: JsonObject
    ) -> tuple[str | None, JsonObject]:
        if method == "turn/started":
            raw_turn = params.get("turn")
            if isinstance(raw_turn, dict):
                turn = self._project_turn(raw_turn, thread.repository_path)
                thread.turns.append(turn)
                thread.active_turn_id = turn.id
                thread.status = "running"
                return "turn.started", {"turnId": turn.id, "status": turn.status}
        elif method == "turn/completed":
            raw_turn = params.get("turn")
            if isinstance(raw_turn, dict):
                turn = self._project_turn(raw_turn, thread.repository_path)
                self._replace_turn(thread, turn)
                thread.active_turn_id = None
                thread.status = {
                    "completed": "completed",
                    "interrupted": "cancelled",
                    "failed": "failed",
                }.get(turn.status, "failed")
                return "turn.completed", {"turnId": turn.id, "status": thread.status}
        elif method == "item/started" or method == "item/completed":
            raw_item = params.get("item")
            turn_id = self._optional_text(params.get("turnId"), 200)
            if isinstance(raw_item, dict) and turn_id is not None:
                item = self._project_item(raw_item, thread.repository_path)
                turn = self._ensure_turn(thread, turn_id)
                self._replace_item(turn, item)
                return (
                    "item.started" if method == "item/started" else "item.completed",
                    item,
                )
        elif method == "item/agentMessage/delta":
            delta = self._redact_path_text(
                self._safe_text(params.get("delta"), self._max_output),
                thread.repository_path,
            )
            self._append_delta(thread, params, "agentMessage", "text", delta)
            return "assistant.delta", {"delta": delta}
        elif method == "item/reasoning/summaryTextDelta":
            delta = self._redact_path_text(
                self._safe_text(params.get("delta"), self._max_output),
                thread.repository_path,
            )
            summary_index = self._safe_int(params.get("summaryIndex")) or 0
            self._append_reasoning_delta(thread, params, delta, summary_index)
            return "reasoning.summary_delta", {
                "delta": delta,
                "summaryIndex": summary_index,
            }
        elif method == "turn/plan/updated":
            plan: list[JsonValue] = []
            raw_plan = params.get("plan")
            if isinstance(raw_plan, list):
                for step in raw_plan[:100]:
                    if isinstance(step, dict):
                        plan.append(
                            {
                                "step": self._redact_path_text(
                                    self._safe_text(step.get("step"), 2000),
                                    thread.repository_path,
                                ),
                                "status": self._safe_text(step.get("status"), 50),
                            }
                        )
            payload: JsonObject = {
                "explanation": self._redact_optional_text(
                    params.get("explanation"), 4000, thread.repository_path
                ),
                "plan": plan,
            }
            self._store_plan_update(thread, params, payload)
            return "plan.updated", payload
        elif method == "item/commandExecution/outputDelta":
            delta = self._redact_path_text(
                self._safe_text(params.get("delta"), self._max_output),
                thread.repository_path,
            )
            self._append_delta(thread, params, "commandExecution", "aggregatedOutput", delta)
            return "command.output_delta", {"delta": delta}
        elif method == "item/fileChange/patchUpdated":
            changes = params.get("changes")
            projected = self._project_changes(changes, thread.repository_path)
            self._store_file_change_update(thread, params, projected)
            return "file_change.updated", {"changes": projected}
        elif method == "turn/diff/updated":
            diff = self._redact_path_text(
                self._safe_text(params.get("diff"), self._max_output), thread.repository_path
            )
            thread.latest_diff = diff
            return "diff.updated", {"diff": diff}
        elif method == "thread/tokenUsage/updated":
            token_usage = params.get("tokenUsage")
            if isinstance(token_usage, dict):
                thread.usage = self._project_usage(token_usage)
                return "usage.updated", thread.usage
        elif method == "error":
            raw_error = params.get("error")
            message = (
                self._redact_path_text(
                    self._safe_text(raw_error.get("message"), 1000),
                    thread.repository_path,
                )
                if isinstance(raw_error, dict)
                else "Codex-Fehler"
            )
            return "error", {
                "code": "codex_error",
                "message": message,
                "willRetry": params.get("willRetry") is True,
            }
        elif method in {"warning", "configWarning", "guardianWarning"}:
            return "warning", {
                "message": self._redact_path_text(
                    self._safe_text(params.get("message"), 1000),
                    thread.repository_path,
                )
            }
        elif method == "thread/status/changed":
            thread.status = self._thread_status(params.get("status"))
            return "thread.status", {"status": thread.status}
        elif method == "thread/compacted":
            return "context.compacted", {"status": "completed"}
        return "compatibility.unknown", {"protocolMethod": method}

    def _project_turns(self, value: JsonValue | None, repository_path: Path) -> list[ProjectedTurn]:
        if not isinstance(value, list):
            return []
        return [
            self._project_turn(item, repository_path) for item in value if isinstance(item, dict)
        ]

    def _project_turn(self, raw: JsonObject, repository_path: Path) -> ProjectedTurn:
        raw_items = raw.get("items")
        projected_items = (
            [
                self._project_item(item, repository_path)
                for item in raw_items
                if isinstance(item, dict)
            ]
            if isinstance(raw_items, list)
            else []
        )
        return ProjectedTurn(
            id=self._required_string(raw, "id"),
            status=self._safe_text(raw.get("status"), 50) or "failed",
            items=projected_items,
        )

    def _project_item(self, item: JsonObject, repository_path: Path) -> JsonObject:
        item_type = self._safe_text(item.get("type"), 100)
        item_id = self._safe_text(item.get("id"), 200)
        base: JsonObject = {"id": item_id, "type": item_type}
        if item_type == "userMessage":
            content = item.get("content")
            projected_content: list[JsonValue] = []
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        projected_content.append(
                            {"type": "text", "text": self._safe_text(part.get("text"), 100_000)}
                        )
                    elif isinstance(part, dict) and part.get("type") in {"localImage", "image"}:
                        projected_content.append({"type": "image"})
            base["content"] = projected_content
        elif item_type == "agentMessage":
            base["text"] = self._redact_path_text(
                self._safe_text(item.get("text"), 500_000), repository_path
            )
        elif item_type == "reasoning":
            summary = item.get("summary")
            base["summary"] = (
                [
                    self._redact_path_text(value[:100_000], repository_path)
                    for value in summary
                    if isinstance(value, str)
                ]
                if isinstance(summary, list)
                else []
            )
        elif item_type == "plan":
            base["text"] = self._redact_path_text(
                self._safe_text(item.get("text"), 100_000), repository_path
            )
        elif item_type == "commandExecution":
            base.update(
                {
                    "command": self._redact_path_text(
                        self._safe_text(item.get("command"), 4000), repository_path
                    ),
                    "cwdDisplay": self._display_path(item.get("cwd"), repository_path),
                    "status": self._safe_text(item.get("status"), 50),
                    "output": self._redact_path_text(
                        self._safe_text(item.get("aggregatedOutput"), self._max_output),
                        repository_path,
                    ),
                    "exitCode": self._safe_int(item.get("exitCode")),
                    "durationMs": self._safe_int(item.get("durationMs")),
                }
            )
        elif item_type == "fileChange":
            base["status"] = self._safe_text(item.get("status"), 50)
            base["changes"] = self._project_changes(item.get("changes"), repository_path)
        elif item_type in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
            base["tool"] = self._safe_text(item.get("tool"), 200)
            base["status"] = self._safe_text(item.get("status"), 50)
        elif item_type == "webSearch" or item_type == "contextCompaction":
            base["status"] = "completed"
        else:
            base["summary"] = "Aktivität"
        return base

    def _project_changes(self, changes: JsonValue | None, repository_path: Path) -> list[JsonValue]:
        if not isinstance(changes, list):
            return []
        result: list[JsonValue] = []
        for change in changes[:500]:
            if not isinstance(change, dict):
                continue
            result.append(
                {
                    "path": self._display_path(change.get("path"), repository_path),
                    "kind": self._safe_text(change.get("kind"), 50),
                    "diff": self._redact_path_text(
                        self._safe_text(change.get("diff"), self._max_output), repository_path
                    ),
                }
            )
        return result

    def _project_usage(self, raw: JsonObject) -> JsonObject:
        latest = self._token_breakdown(raw.get("last"))
        total = self._token_breakdown(raw.get("total"))
        limit = self._safe_int(raw.get("modelContextWindow"))
        used = latest.get("totalTokens") if latest is not None else None
        percent: float | None = None
        if isinstance(used, int) and isinstance(limit, int) and limit > 0:
            percent = used / limit * 100
        return {
            "context": {
                "usedTokens": used,
                "limitTokens": limit,
                "usedPercent": percent,
                "derivation": "latest_model_usage" if percent is not None else None,
            },
            "latestModelUsage": latest,
            "threadTotal": total,
            "observedAt": int(time.time()),
        }

    def _token_breakdown(self, value: JsonValue | None) -> JsonObject | None:
        if not isinstance(value, dict):
            return None
        fields = (
            "totalTokens",
            "inputTokens",
            "cachedInputTokens",
            "cacheWriteInputTokens",
            "outputTokens",
            "reasoningOutputTokens",
        )
        result: JsonObject = {}
        for field_name in fields:
            number = self._safe_int(value.get(field_name))
            if number is None or number < 0:
                return None
            result[field_name] = number
        return result

    def _rate_window(self, value: JsonValue | None) -> JsonValue:
        if not isinstance(value, dict):
            return None
        used = value.get("usedPercent")
        return {
            "usedPercent": self._safe_float(used),
            "windowDurationMins": self._safe_int(value.get("windowDurationMins")),
            "resetsAt": self._validated_unix_time(value.get("resetsAt")),
        }

    def _credits(self, value: JsonValue | None) -> JsonValue:
        if not isinstance(value, dict):
            return None
        has_credits = value.get("hasCredits")
        unlimited = value.get("unlimited")
        return {
            "hasCredits": has_credits if isinstance(has_credits, bool) else None,
            "unlimited": unlimited if isinstance(unlimited, bool) else None,
            "balance": self._optional_text(value.get("balance"), 100),
        }

    def _spend_limit(self, value: JsonValue | None) -> JsonValue:
        if not isinstance(value, dict):
            return None
        return {
            "limit": self._optional_text(value.get("limit"), 100),
            "used": self._optional_text(value.get("used"), 100),
            "remainingPercent": self._safe_float(value.get("remainingPercent")),
            "resetsAt": self._validated_unix_time(value.get("resetsAt")),
        }

    def _append_delta(
        self,
        thread: ProjectedThread,
        params: JsonObject,
        expected_type: str,
        field_name: str,
        delta: str,
    ) -> None:
        turn_id = self._optional_text(params.get("turnId"), 200)
        item_id = self._optional_text(params.get("itemId"), 200)
        if turn_id is None or item_id is None:
            return
        turn = self._ensure_turn(thread, turn_id)
        item = next((entry for entry in turn.items if entry.get("id") == item_id), None)
        if item is None:
            item = {"id": item_id, "type": expected_type, field_name: ""}
            turn.items.append(item)
        current = item.get(field_name)
        current_text = current if isinstance(current, str) else ""
        item[field_name] = (current_text + delta)[-self._max_output :]

    def _append_reasoning_delta(
        self,
        thread: ProjectedThread,
        params: JsonObject,
        delta: str,
        summary_index: int,
    ) -> None:
        turn_id = self._optional_text(params.get("turnId"), 200)
        item_id = self._optional_text(params.get("itemId"), 200)
        if turn_id is None or item_id is None or not 0 <= summary_index < 100:
            return
        turn = self._ensure_turn(thread, turn_id)
        item = next((entry for entry in turn.items if entry.get("id") == item_id), None)
        if item is None:
            item = {"id": item_id, "type": "reasoning", "summary": []}
            turn.items.append(item)
        raw_summary = item.get("summary")
        summary: list[JsonValue] = (
            [value if isinstance(value, str) else "" for value in raw_summary]
            if isinstance(raw_summary, list)
            else []
        )
        while len(summary) <= summary_index:
            summary.append("")
        previous = summary[summary_index]
        previous_text = previous if isinstance(previous, str) else ""
        summary[summary_index] = (previous_text + delta)[-self._max_output :]
        item["summary"] = summary

    def _store_plan_update(
        self, thread: ProjectedThread, params: JsonObject, payload: JsonObject
    ) -> None:
        turn_id = self._optional_text(params.get("turnId"), 200)
        if turn_id is None:
            return
        turn = self._ensure_turn(thread, turn_id)
        item: JsonObject = {
            "id": f"plan-{turn_id}",
            "type": "planUpdate",
            "text": payload.get("explanation"),
            "plan": payload.get("plan"),
        }
        self._replace_item(turn, item)

    def _store_file_change_update(
        self, thread: ProjectedThread, params: JsonObject, changes: list[JsonValue]
    ) -> None:
        turn_id = self._optional_text(params.get("turnId"), 200)
        item_id = self._optional_text(params.get("itemId"), 200)
        if turn_id is None or item_id is None:
            return
        turn = self._ensure_turn(thread, turn_id)
        self._replace_item(
            turn,
            {
                "id": item_id,
                "type": "fileChange",
                "status": "inProgress",
                "changes": changes,
            },
        )

    @staticmethod
    def _replace_turn(thread: ProjectedThread, replacement: ProjectedTurn) -> None:
        for index, turn in enumerate(thread.turns):
            if turn.id == replacement.id:
                thread.turns[index] = replacement
                return
        thread.turns.append(replacement)

    @staticmethod
    def _replace_item(turn: ProjectedTurn, replacement: JsonObject) -> None:
        replacement_id = replacement.get("id")
        for index, item in enumerate(turn.items):
            if item.get("id") == replacement_id:
                turn.items[index] = replacement
                return
        turn.items.append(replacement)

    @staticmethod
    def _ensure_turn(thread: ProjectedThread, turn_id: str) -> ProjectedTurn:
        for turn in thread.turns:
            if turn.id == turn_id:
                return turn
        turn = ProjectedTurn(turn_id, "inProgress")
        thread.turns.append(turn)
        return turn

    @staticmethod
    def _notification_thread_id(params: JsonObject) -> str | None:
        thread_id = params.get("threadId")
        if isinstance(thread_id, str):
            return thread_id
        thread = params.get("thread")
        if isinstance(thread, dict) and isinstance(thread.get("id"), str):
            return cast(str, thread["id"])
        return None

    @staticmethod
    def _thread_status(value: JsonValue | None) -> str:
        if isinstance(value, dict):
            status = value.get("type")
            if status == "active":
                flags = value.get("activeFlags")
                if isinstance(flags, list) and "waitingOnApproval" in flags:
                    return "awaiting_approval"
                return "running"
            if status in {"idle", "notLoaded", "systemError"}:
                mapped = {"idle": "idle", "notLoaded": "unloaded", "systemError": "failed"}
                return mapped[cast(str, status)]
        return "unloaded"

    @staticmethod
    def _required_string(value: JsonObject, field_name: str) -> str:
        result = value.get(field_name)
        if not isinstance(result, str) or not result:
            raise ValueError(f"protocol object lacks {field_name}")
        return result

    @staticmethod
    def _safe_text(value: JsonValue | None, limit: int) -> str:
        return value[:limit] if isinstance(value, str) else ""

    @staticmethod
    def _optional_text(value: JsonValue | None, limit: int) -> str | None:
        return value[:limit] if isinstance(value, str) else None

    @staticmethod
    def _clean_display_title(value: str | None) -> str | None:
        if value is None:
            return None
        title = " ".join(value.split()).strip(" \t\r\n\"'`#*_~")
        title = title.removesuffix("...").strip()
        return title[:80].rstrip() or None

    @classmethod
    def _fallback_title(cls, preview: str) -> str:
        return cls._clean_display_title(preview) or "Neuer Thread"

    @staticmethod
    def _safe_int(value: JsonValue | None) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _safe_timestamp(value: JsonValue | None) -> int | None:
        timestamp = DomainProjection._safe_int(value)
        return timestamp if timestamp is not None and 0 <= timestamp <= 4_102_444_800 else None

    @staticmethod
    def _validated_unix_time(value: JsonValue | None) -> JsonValue:
        timestamp = DomainProjection._safe_int(value)
        return timestamp if timestamp is not None and 0 <= timestamp <= 4_102_444_800 else None

    @staticmethod
    def _is_nonnegative_number(value: JsonValue | None) -> bool:
        return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _safe_float(value: JsonValue | None) -> float | None:
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            return float(value)
        return None

    @staticmethod
    def _display_path(value: JsonValue | None, repository_path: Path) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            path = Path(value)
            resolved = path.resolve(strict=False)
            if resolved == repository_path:
                return repository_path.name
            if resolved.is_relative_to(repository_path):
                return resolved.relative_to(repository_path).as_posix()
        except (OSError, RuntimeError, ValueError):
            return Path(value).name[:500]
        return Path(value).name[:500]

    @staticmethod
    def _redact_path_text(value: str, repository_path: Path) -> str:
        return sanitize_browser_text(
            value,
            known_path=str(repository_path),
            known_label=repository_path.name,
        )

    @classmethod
    def _activity_browser_dict(cls, activity: AgentActivity, repository_path: Path) -> JsonObject:
        payload = cast(JsonObject, activity.model_dump(mode="json"))
        for field_name in ("last_activity", "current_command", "error_message"):
            value = payload.get(field_name)
            if isinstance(value, str):
                payload[field_name] = cls._redact_path_text(value, repository_path)
        return payload

    @classmethod
    def _redact_optional_text(
        cls, value: JsonValue | None, limit: int, repository_path: Path
    ) -> str | None:
        text = cls._optional_text(value, limit)
        return None if text is None else cls._redact_path_text(text, repository_path)
