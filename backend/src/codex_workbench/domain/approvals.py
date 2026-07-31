"""One-shot, generation- and session-bound app-server approval registry."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from codex_workbench.app_server.transport import JsonObject, JsonScalar, JsonValue
from codex_workbench.security.redaction import sanitize_browser_text


class ApprovalRegistryError(RuntimeError):
    """Base class for safe approval registry errors."""


class ApprovalUnavailableError(ApprovalRegistryError):
    pass


class ApprovalReplayError(ApprovalRegistryError):
    pass


class RegistryApprovalState(StrEnum):
    PRESENTED = "presented"
    DECIDING = "deciding"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    FAILED = "failed"


@dataclass(slots=True)
class ApprovalRecord:
    id: str
    generation: int
    server_request_id: JsonScalar
    method: str
    session_id: str
    thread_id: str
    turn_id: str | None
    item_id: str | None
    kind: str
    requested_at: float
    expires_at: float
    available_decisions: tuple[str, ...]
    summary: str
    reason: str | None
    command: str | None
    cwd_display: str | None
    writable_roots_display: tuple[str, ...]
    protocol_params: JsonObject
    state: RegistryApprovalState = RegistryApprovalState.PRESENTED

    def browser_dict(self) -> JsonObject:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state.value,
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "itemId": self.item_id,
            "requestedAt": datetime.fromtimestamp(self.requested_at, UTC).isoformat(),
            "expiresAt": datetime.fromtimestamp(self.expires_at, UTC).isoformat(),
            "summary": self.summary,
            "reason": self.reason,
            "command": self.command,
            "cwdDisplay": self.cwd_display,
            "writableRootsDisplay": list(self.writable_roots_display),
            "availableDecisions": list(self.available_decisions),
        }


@dataclass(frozen=True, slots=True)
class ApprovalClaim:
    record: ApprovalRecord
    response: JsonObject


class ApprovalRegistry:
    """Atomically maps browser decisions to server-retained protocol values."""

    _SUPPORTED = frozenset(
        {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "execCommandApproval",
            "applyPatchApproval",
        }
    )

    def __init__(self, *, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        generation: int,
        request_id: JsonScalar,
        method: str,
        params: JsonObject,
        session_id: str,
        cwd_display: str | None,
    ) -> ApprovalRecord | None:
        if method not in self._SUPPORTED:
            return None
        thread_id = self._thread_id(method, params)
        if thread_id is None:
            return None
        turn_id = self._optional_string(params.get("turnId"))
        item_id = self._optional_string(params.get("itemId"))
        kind, decisions = self._kind_and_decisions(method, params)
        command = self._command(method, params)
        reason = self._optional_string(params.get("reason"))
        safe_command = self._display_command(command, params, cwd_display)
        safe_reason = (
            None
            if reason is None
            else sanitize_browser_text(
                reason,
                known_path=self._optional_string(params.get("cwd")),
                known_label=cwd_display or "repository",
            )[:1000]
        )
        roots = self._writable_roots(method, params)
        now = time.time()
        record = ApprovalRecord(
            id="approval_" + secrets.token_urlsafe(24),
            generation=generation,
            server_request_id=request_id,
            method=method,
            session_id=session_id,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            kind=kind,
            requested_at=now,
            expires_at=now + self._ttl,
            available_decisions=decisions,
            summary=self._summary(kind, safe_command, safe_reason),
            reason=safe_reason,
            command=safe_command,
            cwd_display=cwd_display,
            writable_roots_display=roots,
            protocol_params=params,
        )
        async with self._lock:
            self._records[record.id] = record
        return record

    async def claim(
        self,
        approval_id: str,
        *,
        session_id: str,
        generation: int,
        decision: str,
    ) -> ApprovalClaim:
        async with self._lock:
            record = self._records.get(approval_id)
            if record is None or record.session_id != session_id or record.generation != generation:
                raise ApprovalUnavailableError("approval is unavailable")
            if record.state is not RegistryApprovalState.PRESENTED:
                raise ApprovalReplayError("approval was already handled")
            if record.expires_at <= time.time():
                raise ApprovalUnavailableError("approval has expired")
            if decision not in record.available_decisions:
                raise ApprovalUnavailableError("approval decision is not available")
            record.state = RegistryApprovalState.DECIDING
            return ApprovalClaim(record, self._response(record, decision))

    async def mark_responded(self, approval_id: str, *, succeeded: bool) -> None:
        async with self._lock:
            record = self._records.get(approval_id)
            if record is not None and record.state is RegistryApprovalState.DECIDING:
                record.state = (
                    RegistryApprovalState.RESPONDED if succeeded else RegistryApprovalState.FAILED
                )

    async def resolve_native(
        self, generation: int, request_id: JsonScalar
    ) -> ApprovalRecord | None:
        async with self._lock:
            for record in self._records.values():
                if (
                    record.generation == generation
                    and record.server_request_id == request_id
                    and record.state
                    in {RegistryApprovalState.PRESENTED, RegistryApprovalState.RESPONDED}
                ):
                    record.state = RegistryApprovalState.RESOLVED
                    return record
        return None

    async def invalidate_generation(self, generation: int) -> tuple[ApprovalRecord, ...]:
        return await self._invalidate(lambda record: record.generation == generation)

    async def invalidate_session(self, session_id: str) -> tuple[ApprovalRecord, ...]:
        return await self._invalidate(lambda record: record.session_id == session_id)

    async def invalidate_turn(
        self, thread_id: str, turn_id: str | None
    ) -> tuple[ApprovalRecord, ...]:
        return await self._invalidate(
            lambda record: record.thread_id == thread_id
            and (turn_id is None or record.turn_id == turn_id)
        )

    async def list_pending(self, session_id: str) -> tuple[ApprovalRecord, ...]:
        async with self._lock:
            now = time.time()
            return tuple(
                record
                for record in self._records.values()
                if record.session_id == session_id
                and record.state is RegistryApprovalState.PRESENTED
                and record.expires_at > now
            )

    async def expire(self, approval_id: str) -> ApprovalRecord | None:
        async with self._lock:
            record = self._records.get(approval_id)
            if (
                record is None
                or record.state is not RegistryApprovalState.PRESENTED
                or record.expires_at > time.time()
            ):
                return None
            record.state = RegistryApprovalState.EXPIRED
            return record

    async def _invalidate(
        self, predicate: Callable[[ApprovalRecord], bool]
    ) -> tuple[ApprovalRecord, ...]:
        invalidated: list[ApprovalRecord] = []
        async with self._lock:
            for record in self._records.values():
                if predicate(record) and record.state in {
                    RegistryApprovalState.PRESENTED,
                    RegistryApprovalState.DECIDING,
                }:
                    record.state = RegistryApprovalState.INVALIDATED
                    invalidated.append(record)
        return tuple(invalidated)

    @staticmethod
    def _thread_id(method: str, params: JsonObject) -> str | None:
        field = (
            "conversationId"
            if method in {"execCommandApproval", "applyPatchApproval"}
            else "threadId"
        )
        return ApprovalRegistry._optional_string(params.get(field))

    @staticmethod
    def _kind_and_decisions(method: str, params: JsonObject) -> tuple[str, tuple[str, ...]]:
        if method in {"item/commandExecution/requestApproval", "execCommandApproval"}:
            decisions = ["accept", "accept_for_session", "decline", "cancel"]
            if method == "item/commandExecution/requestApproval":
                if isinstance(params.get("proposedExecpolicyAmendment"), list):
                    decisions.append("accept_with_execpolicy_amendment")
                amendments = params.get("proposedNetworkPolicyAmendments")
                if isinstance(amendments, list) and len(amendments) == 1:
                    decisions.append("apply_network_policy_amendment")
            return "command_execution", tuple(decisions)
        if method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
            return "file_change", ("accept", "accept_for_session", "decline", "cancel")
        return "permissions", ("accept", "decline")

    @staticmethod
    def _command(method: str, params: JsonObject) -> str | None:
        command = params.get("command")
        if isinstance(command, str):
            return command
        if method == "execCommandApproval" and isinstance(command, list):
            values = [item for item in command if isinstance(item, str)]
            return " ".join(values)
        return None

    @staticmethod
    def _writable_roots(method: str, params: JsonObject) -> tuple[str, ...]:
        if method == "item/permissions/requestApproval":
            permissions = params.get("permissions")
            filesystem = permissions.get("fileSystem") if isinstance(permissions, dict) else None
            writes = filesystem.get("write") if isinstance(filesystem, dict) else None
            if isinstance(writes, list):
                return tuple(
                    ApprovalRegistry._path_label(value)
                    for value in writes
                    if isinstance(value, str)
                )
        root = params.get("grantRoot")
        return (ApprovalRegistry._path_label(root),) if isinstance(root, str) else ()

    @staticmethod
    def _summary(kind: str, command: str | None, reason: str | None) -> str:
        if kind == "command_execution":
            return (command or "Codex möchte einen Befehl ausführen")[:500]
        if kind == "file_change":
            return (reason or "Codex möchte Dateien ändern")[:500]
        return (reason or "Codex fordert zusätzliche Berechtigungen an")[:500]

    @staticmethod
    def _response(record: ApprovalRecord, decision: str) -> JsonObject:
        if record.method in {"execCommandApproval", "applyPatchApproval"}:
            legacy: JsonValue
            if decision == "accept":
                legacy = "approved"
            elif decision == "accept_for_session":
                legacy = "approved_for_session"
            elif decision == "cancel":
                legacy = "abort"
            else:
                legacy = {"denied": {"rejection": "Declined in Codex Workbench"}}
            return {"decision": legacy}
        if record.method == "item/permissions/requestApproval":
            permissions: JsonObject = {}
            if decision == "accept":
                requested = record.protocol_params.get("permissions")
                if isinstance(requested, dict):
                    for key in ("network", "fileSystem"):
                        value = requested.get(key)
                        if isinstance(value, dict):
                            permissions[key] = value
            return {"permissions": permissions, "scope": "turn", "strictAutoReview": False}
        protocol_decision: JsonValue
        if decision == "accept_for_session":
            protocol_decision = "acceptForSession"
        elif decision == "accept_with_execpolicy_amendment":
            amendment = record.protocol_params.get("proposedExecpolicyAmendment")
            protocol_decision = {
                "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment}
            }
        elif decision == "apply_network_policy_amendment":
            amendments = record.protocol_params.get("proposedNetworkPolicyAmendments")
            amendment = amendments[0] if isinstance(amendments, list) and amendments else None
            protocol_decision = {
                "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment}
            }
        else:
            protocol_decision = decision
        return {"decision": protocol_decision}

    @staticmethod
    def _optional_string(value: JsonValue | None) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _path_label(value: str) -> str:
        from pathlib import Path

        name = Path(value).name
        return (name or "Repository-Root")[:500]

    @staticmethod
    def _display_command(
        command: str | None, params: JsonObject, cwd_display: str | None
    ) -> str | None:
        if command is None:
            return None
        cwd = params.get("cwd")
        if isinstance(cwd, str) and cwd_display is not None:
            return sanitize_browser_text(command, known_path=cwd, known_label=cwd_display)[:4000]
        return sanitize_browser_text(command)[:4000]
