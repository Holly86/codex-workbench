"""Application orchestration across transport, protocol, domain, and persistence boundaries."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from codex_workbench.app_server.protocol import ProtocolAdapter
from codex_workbench.app_server.supervisor import AppServerSupervisor, ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonScalar, JsonValue
from codex_workbench.config import Settings
from codex_workbench.domain.approvals import (
    ApprovalRecord,
    ApprovalRegistry,
    ApprovalReplayError,
    ApprovalUnavailableError,
)
from codex_workbench.domain.capabilities import FullAccessRegistry
from codex_workbench.domain.projection import DomainProjection, ProjectedThread
from codex_workbench.persistence.metadata import MetadataRepository, ThreadReference
from codex_workbench.realtime.broker import RealtimeBroker
from codex_workbench.repositories import RepositoryDiscovery, RepositorySummary
from codex_workbench.security.rate_limit import LoginRateLimiter, RateLimitDecision
from codex_workbench.uploads import UploadService

logger = logging.getLogger("codex_workbench.runtime")


class RuntimeUnavailableError(RuntimeError):
    pass


class RuntimeConflictError(RuntimeError):
    pass


class WorkbenchRuntime:
    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        self.settings = settings
        self.metadata = metadata
        self.repositories = RepositoryDiscovery(settings.repositories_root)
        self.broker = RealtimeBroker(
            history_size=settings.realtime_history_size,
            subscriber_queue_size=settings.realtime_client_queue_size,
        )
        self.projection = DomainProjection(
            self.broker,
            metadata,
            max_output_bytes=settings.max_command_output_bytes,
        )
        self.approvals = ApprovalRegistry(ttl_seconds=settings.approval_ttl_seconds)
        self.full_access = FullAccessRegistry(
            ttl_seconds=settings.full_access_confirmation_ttl_seconds
        )
        self.uploads = UploadService(settings, metadata)
        self.protocol = ProtocolAdapter()
        self.supervisor = AppServerSupervisor(
            settings,
            notification_handler=self._on_notification,
            server_request_handler=self._on_server_request,
            disconnect_handler=self._on_disconnect,
            ready_handler=self._on_ready,
            terminal_failure_handler=self._on_terminal_failure,
        )
        self._thread_mutations: dict[str, asyncio.Lock] = {}
        self._approval_expiry_tasks: dict[str, asyncio.Task[None]] = {}
        self._mutation_limiter = LoginRateLimiter(
            address_limit=settings.mutation_rate_limit_requests,
            global_limit=settings.mutation_rate_limit_global_requests,
            window_seconds=settings.mutation_rate_limit_window_seconds,
        )

    @property
    def app_server_ready(self) -> bool:
        return self.supervisor.status.state is ProcessState.READY

    async def start(self) -> None:
        await self.uploads.initialize()
        if self.settings.app_server_start_enabled:
            await self.projection.connection_state("connecting", 0)
            await self.supervisor.start()
        else:
            await self.projection.connection_state("disconnected", 0, "disabled")

    async def stop(self) -> None:
        for task in self._approval_expiry_tasks.values():
            task.cancel()
        if self._approval_expiry_tasks:
            await asyncio.gather(*self._approval_expiry_tasks.values(), return_exceptions=True)
        self._approval_expiry_tasks.clear()
        await self.supervisor.stop()

    async def list_repositories(self) -> tuple[RepositorySummary, ...]:
        return await self.repositories.list_repositories()

    def consume_mutation(self, session_id: str) -> RateLimitDecision:
        return self._mutation_limiter.consume(session_id)

    async def list_threads(self, repository_id: str, session_id: str) -> tuple[JsonObject, ...]:
        summary, path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        result = await self._call(
            "thread/list",
            self.protocol.thread_list_params(str(path)),
            owner=session_id,
        )
        payload = self._object(result, "thread/list result")
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeUnavailableError("thread/list returned invalid data")
        projected: list[JsonObject] = []
        for value in data:
            if not isinstance(value, dict):
                continue
            raw = value
            if not self._thread_cwd_matches(raw, path):
                continue
            thread_id = raw.get("id")
            stored = (
                await asyncio.to_thread(self.metadata.get_thread, thread_id)
                if isinstance(thread_id, str)
                else None
            )
            thread = await self.projection.register_thread(
                raw,
                repository_id=summary.id,
                repository_name=summary.name,
                repository_path=path,
                repository_relative_path=summary.relative_path,
                session_id=session_id,
                sandbox_mode=stored.sandbox_mode if stored is not None else "workspace-write",
                approval_policy=stored.approval_policy if stored is not None else "on-request",
            )
            projected.append(self.projection.thread_browser_dict(thread))
        return tuple(projected)

    async def start_thread(
        self,
        *,
        repository_id: str,
        session_id: str,
        sandbox_mode: str,
        approval_policy: str,
        confirmation_id: str | None,
        full_access_scope_id: str,
    ) -> JsonObject:
        summary, path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        self.protocol.validate_permission_selection(sandbox_mode, approval_policy)
        if sandbox_mode == "danger-full-access":
            await self.full_access.consume(
                confirmation_id,
                session_id=session_id,
                repository_id=repository_id,
                scope_id=full_access_scope_id,
                approval_policy=approval_policy,
            )
        elif confirmation_id is not None:
            raise ValueError("full access confirmation is not applicable")
        result = await self._call(
            "thread/start",
            self.protocol.thread_start_params(str(path), sandbox_mode, approval_policy),
            owner=session_id,
        )
        response = self._object(result, "thread/start result")
        raw_thread = self._object(response.get("thread"), "thread/start thread")
        if not self._thread_cwd_matches(raw_thread, path):
            raise RuntimeUnavailableError("thread/start returned an unexpected working directory")
        thread = await self.projection.register_thread(
            raw_thread,
            repository_id=summary.id,
            repository_name=summary.name,
            repository_path=path,
            repository_relative_path=summary.relative_path,
            session_id=session_id,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
        )
        if sandbox_mode == "danger-full-access":
            await asyncio.to_thread(
                self.metadata.audit,
                "full_access_activated",
                occurred_at=int(time.time()),
                session_id=session_id,
                thread_id=thread.id,
                repository_id=repository_id,
            )
        browser = self.projection.thread_browser_dict(thread)
        await self.broker.publish(session_id, "thread.started", browser, thread_id=thread.id)
        return browser

    async def resume_thread(
        self,
        *,
        thread_id: str,
        repository_id: str,
        session_id: str,
        sandbox_mode: str,
        approval_policy: str,
        confirmation_id: str | None,
    ) -> JsonObject:
        summary, path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        self.protocol.validate_permission_selection(sandbox_mode, approval_policy)
        read = await self._call(
            "thread/read", {"threadId": thread_id, "includeTurns": False}, owner=session_id
        )
        read_response = self._object(read, "thread/read result")
        candidate = self._object(read_response.get("thread"), "thread/read thread")
        if candidate.get("id") != thread_id or not self._thread_cwd_matches(candidate, path):
            raise ValueError("thread does not belong to the selected repository")
        if sandbox_mode == "danger-full-access":
            await self.full_access.consume(
                confirmation_id,
                session_id=session_id,
                repository_id=repository_id,
                scope_id=thread_id,
                approval_policy=approval_policy,
            )
        elif confirmation_id is not None:
            raise ValueError("full access confirmation is not applicable")
        result = await self._call(
            "thread/resume",
            self.protocol.thread_resume_params(thread_id, str(path), sandbox_mode, approval_policy),
            owner=session_id,
        )
        response = self._object(result, "thread/resume result")
        raw_thread = self._object(response.get("thread"), "thread/resume thread")
        if not self._thread_cwd_matches(raw_thread, path):
            raise RuntimeUnavailableError("thread/resume returned an unexpected working directory")
        thread = await self.projection.register_thread(
            raw_thread,
            repository_id=summary.id,
            repository_name=summary.name,
            repository_path=path,
            repository_relative_path=summary.relative_path,
            session_id=session_id,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
        )
        if sandbox_mode == "danger-full-access":
            await asyncio.to_thread(
                self.metadata.audit,
                "full_access_activated",
                occurred_at=int(time.time()),
                session_id=session_id,
                thread_id=thread_id,
                repository_id=repository_id,
            )
        browser = self.projection.thread_browser_dict(thread)
        await self.broker.publish(session_id, "thread.resumed", browser, thread_id=thread.id)
        return browser

    async def read_thread(self, thread_id: str, session_id: str) -> JsonObject:
        thread = self.projection.require_thread(thread_id)
        if thread.session_id != session_id:
            raise ValueError("thread is unavailable")
        _, validated_path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, thread.repository_id
        )
        if validated_path != thread.repository_path:
            raise ValueError("thread repository changed")
        result = await self._call(
            "thread/read", {"threadId": thread_id, "includeTurns": True}, owner=session_id
        )
        response = self._object(result, "thread/read result")
        raw_thread = self._object(response.get("thread"), "thread/read thread")
        if not self._thread_cwd_matches(raw_thread, validated_path):
            raise ValueError("thread working directory changed")
        refreshed = await self.projection.register_thread(
            raw_thread,
            repository_id=thread.repository_id,
            repository_name=thread.repository_name,
            repository_path=validated_path,
            repository_relative_path=thread.repository_relative_path,
            session_id=session_id,
            sandbox_mode=thread.sandbox_mode,
            approval_policy=thread.approval_policy,
        )
        return self.projection.thread_browser_dict(refreshed)

    async def start_turn(
        self,
        *,
        thread_id: str,
        session_id: str,
        text: str,
        images: tuple[tuple[str, str], ...],
        sandbox_mode: str,
        approval_policy: str,
        confirmation_id: str | None,
        idempotency_key: str,
    ) -> JsonObject:
        lock = self._thread_mutations.setdefault(thread_id, asyncio.Lock())
        async with lock:
            thread = self.projection.require_thread(thread_id)
            if thread.session_id != session_id:
                raise ValueError("thread is unavailable")
            if thread.active_turn_id is not None or thread.status in {
                "running",
                "awaiting_approval",
            }:
                raise RuntimeConflictError("a turn is already running")
            summary, path = await asyncio.to_thread(
                self.repositories.resolve_repository_id, thread.repository_id
            )
            if path != thread.repository_path:
                raise ValueError("thread repository changed")
            self.protocol.validate_permission_selection(sandbox_mode, approval_policy)
            activating_full_access = sandbox_mode == "danger-full-access" and (
                thread.sandbox_mode != "danger-full-access"
                or thread.approval_policy != approval_policy
            )
            if activating_full_access:
                await self.full_access.consume(
                    confirmation_id,
                    session_id=session_id,
                    repository_id=summary.id,
                    scope_id=thread_id,
                    approval_policy=approval_policy,
                )
            elif confirmation_id is not None:
                raise ValueError("full access confirmation is not applicable")
            resolved_images = await self.uploads.resolve_images(images, session_id=session_id)
            await self.projection.turn_starting(thread_id)
            try:
                result = await self._call(
                    "turn/start",
                    self.protocol.turn_start_params(
                        thread_id=thread_id,
                        cwd=str(path),
                        text=text,
                        image_paths=resolved_images,
                        sandbox_mode=sandbox_mode,
                        approval_policy=approval_policy,
                        client_message_id=idempotency_key,
                    ),
                    owner=session_id,
                )
            except BaseException:
                await self.projection.turn_start_failed(thread_id)
                raise
            response = self._object(result, "turn/start result")
            raw_turn = self._object(response.get("turn"), "turn/start turn")
            turn_id = raw_turn.get("id")
            if not isinstance(turn_id, str):
                raise RuntimeUnavailableError("turn/start returned no turn id")
            thread.active_turn_id = turn_id
            thread.status = "running"
            await self.projection.set_permission_selection(thread_id, sandbox_mode, approval_policy)
            stored = await asyncio.to_thread(self.metadata.get_thread, thread_id)
            now = int(time.time())
            await asyncio.to_thread(
                self.metadata.upsert_thread,
                ThreadReference(
                    thread_id=thread_id,
                    repository_id=thread.repository_id,
                    repository_relative_path=thread.repository_relative_path,
                    last_session_id=session_id,
                    sandbox_mode=sandbox_mode,
                    approval_policy=approval_policy,
                    created_at=stored.created_at if stored is not None else now,
                    updated_at=now,
                ),
            )
            await self.uploads.mark_consumed(
                tuple(upload_id for upload_id, _detail in images), session_id=session_id
            )
            if activating_full_access:
                await asyncio.to_thread(
                    self.metadata.audit,
                    "full_access_activated",
                    occurred_at=now,
                    session_id=session_id,
                    thread_id=thread_id,
                    repository_id=thread.repository_id,
                )
            return {"turnId": turn_id, "status": "inProgress"}

    async def interrupt_turn(self, thread_id: str, session_id: str) -> JsonObject:
        thread = self.projection.require_thread(thread_id)
        if thread.session_id != session_id:
            raise ValueError("thread is unavailable")
        turn_id = thread.active_turn_id
        if turn_id is None:
            raise RuntimeConflictError("there is no active turn")
        await self._call(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            owner=session_id,
        )
        await self.broker.publish(
            session_id,
            "turn.interrupt_requested",
            {"turnId": turn_id},
            thread_id=thread_id,
            turn_id=turn_id,
        )
        return {"turnId": turn_id, "status": "interrupting"}

    async def decide_approval(
        self, approval_id: str, *, session_id: str, decision: str
    ) -> JsonObject:
        generation = self.supervisor.status.generation
        try:
            claim = await self.approvals.claim(
                approval_id,
                session_id=session_id,
                generation=generation,
                decision=decision,
            )
        except ApprovalReplayError:
            raise RuntimeConflictError("approval was already answered") from None
        except ApprovalUnavailableError:
            raise ValueError("approval is unavailable") from None
        client = self.supervisor.client_for_generation(generation)
        succeeded = False
        try:
            await client.respond(claim.record.server_request_id, claim.response)
            succeeded = True
        finally:
            await self.approvals.mark_responded(approval_id, succeeded=succeeded)
        await self.projection.publish_approval_resolved(claim.record.thread_id, approval_id)
        await asyncio.to_thread(
            self.metadata.audit,
            "approval_responded",
            occurred_at=int(time.time()),
            session_id=session_id,
            thread_id=claim.record.thread_id,
        )
        return {"approvalId": approval_id, "state": "responded"}

    async def read_rate_limits(self, session_id: str) -> JsonObject:
        result = await self._call("account/rateLimits/read", None, owner=session_id)
        return await self.projection.set_rate_limits(
            session_id, self._object(result, "account/rateLimits/read result")
        )

    async def snapshot(self, session_id: str) -> JsonObject:
        pending = await self.approvals.list_pending(session_id)
        return self.projection.snapshot(
            session_id, tuple(record.browser_dict() for record in pending)
        )

    async def invalidate_session(self, session_id: str) -> None:
        invalidated = await self.approvals.invalidate_session(session_id)
        await self.full_access.invalidate_session(session_id)
        for record in invalidated:
            await self._respond_safe(record)
            await self.projection.publish_approval_resolved(record.thread_id, record.id)

    async def _on_notification(self, generation: int, method: str, params: JsonObject) -> None:
        if generation != self.supervisor.status.generation:
            return
        if method == "serverRequest/resolved":
            request_id = params.get("requestId")
            if isinstance(request_id, str | int) and not isinstance(request_id, bool):
                resolved = await self.approvals.resolve_native(generation, request_id)
                if resolved is not None:
                    await self.projection.publish_approval_resolved(resolved.thread_id, resolved.id)
        await self.projection.ingest_notification(generation, method, params)
        if method == "turn/completed":
            thread_id = params.get("threadId")
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if isinstance(thread_id, str):
                invalidated = await self.approvals.invalidate_turn(
                    thread_id, turn_id if isinstance(turn_id, str) else None
                )
                for record in invalidated:
                    await self.projection.publish_approval_resolved(thread_id, record.id)

    async def _on_server_request(
        self,
        generation: int,
        request_id: JsonScalar,
        method: str,
        params: JsonObject,
    ) -> None:
        client = self.supervisor.client_for_generation(generation)
        thread_id = self._server_request_thread_id(method, params)
        thread = self._optional_thread(thread_id)
        if thread is not None:
            record = await self.approvals.register(
                generation=generation,
                request_id=request_id,
                method=method,
                params=params,
                session_id=thread.session_id,
                cwd_display=thread.repository_name,
            )
            if record is not None:
                self._approval_expiry_tasks[record.id] = asyncio.create_task(
                    self._expire_approval(record),
                    name=f"approval-expiry-{record.id[-8:]}",
                )
                await self.projection.publish_approval(thread.id, record.browser_dict())
                return
        safe_response = self._safe_server_request_response(method)
        if safe_response is None:
            await client.respond_error(request_id)
        else:
            await client.respond(request_id, safe_response)

    async def _on_disconnect(self, generation: int, exc: BaseException) -> None:
        invalidated = await self.approvals.invalidate_generation(generation)
        for record in invalidated:
            await self.projection.publish_approval_resolved(record.thread_id, record.id)
        await self.projection.connection_state("reconnecting", generation, type(exc).__name__)

    async def _on_ready(self, generation: int) -> None:
        await self.projection.connection_state("connected", generation)
        if generation > 1:
            await self._reconcile()

    async def _on_terminal_failure(self, generation: int, error_code: str) -> None:
        await self.projection.connection_state("failed", generation, error_code)

    async def _expire_approval(self, record: ApprovalRecord) -> None:
        try:
            delay = max(0.0, record.expires_at - time.time())
            await asyncio.sleep(delay)
            expired = await self.approvals.expire(record.id)
            if expired is None:
                return
            await self._respond_safe(expired)
            await self.projection.publish_approval_resolved(expired.thread_id, expired.id)
            await asyncio.to_thread(
                self.metadata.audit,
                "approval_expired",
                occurred_at=int(time.time()),
                session_id=expired.session_id,
                thread_id=expired.thread_id,
            )
        finally:
            self._approval_expiry_tasks.pop(record.id, None)

    async def _respond_safe(self, record: ApprovalRecord) -> None:
        if record.generation != self.supervisor.status.generation:
            return
        try:
            client = self.supervisor.client_for_generation(record.generation)
            response = self._safe_server_request_response(record.method)
            if response is None:
                await client.respond_error(record.server_request_id)
            else:
                await client.respond(record.server_request_id, response)
        except RuntimeError:
            return

    async def _reconcile(self) -> None:
        references = await asyncio.to_thread(self.metadata.list_threads)
        for reference in references:
            try:
                summary, path = await asyncio.to_thread(
                    self.repositories.resolve_repository_id, reference.repository_id
                )
                result = await self._call(
                    "thread/read",
                    {"threadId": reference.thread_id, "includeTurns": True},
                    owner=reference.last_session_id,
                )
                response = self._object(result, "thread/read reconciliation")
                raw = self._object(response.get("thread"), "thread/read thread")
                if not self._thread_cwd_matches(raw, path):
                    continue
                thread = await self.projection.register_thread(
                    raw,
                    repository_id=summary.id,
                    repository_name=summary.name,
                    repository_path=path,
                    repository_relative_path=summary.relative_path,
                    session_id=reference.last_session_id,
                    sandbox_mode=reference.sandbox_mode,
                    approval_policy=reference.approval_policy,
                )
                await self.broker.publish(
                    thread.session_id,
                    "thread.reconciled",
                    self.projection.thread_browser_dict(thread),
                    thread_id=thread.id,
                )
            except (RuntimeError, ValueError):
                logger.warning(
                    "thread_reconciliation_failed",
                    extra={"thread_id": reference.thread_id},
                )

    async def _call(self, method: str, params: JsonObject | None, *, owner: str) -> JsonValue:
        if not self.settings.app_server_start_enabled:
            raise RuntimeUnavailableError("app-server startup is disabled")
        if not self.app_server_ready:
            raise RuntimeUnavailableError("app-server is not ready")
        return await self.supervisor.client.call(method, params, owner=owner)

    @staticmethod
    def _object(value: JsonValue | None, label: str) -> JsonObject:
        if not isinstance(value, dict):
            raise RuntimeUnavailableError(f"{label} is invalid")
        return value

    @staticmethod
    def _thread_cwd_matches(thread: JsonObject, repository_path: Path) -> bool:
        cwd = thread.get("cwd")
        if not isinstance(cwd, str):
            return False
        try:
            return Path(cwd).resolve(strict=True) == repository_path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False

    @staticmethod
    def _server_request_thread_id(method: str, params: JsonObject) -> str | None:
        field = (
            "conversationId"
            if method in {"execCommandApproval", "applyPatchApproval"}
            else "threadId"
        )
        value = params.get(field)
        return value if isinstance(value, str) else None

    def _optional_thread(self, thread_id: str | None) -> ProjectedThread | None:
        if thread_id is None:
            return None
        try:
            return self.projection.require_thread(thread_id)
        except ValueError:
            return None

    @staticmethod
    def _safe_server_request_response(method: str) -> JsonObject | None:
        if method == "item/commandExecution/requestApproval":
            return {"decision": "decline"}
        if method == "item/fileChange/requestApproval":
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn", "strictAutoReview": False}
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": {"denied": {"rejection": "Unavailable in Workbench"}}}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None, "_meta": None}
        if method == "item/tool/call":
            return {"contentItems": [], "success": False}
        return None
