"""Deterministic Workbench activity aggregation from reviewed protocol evidence."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.domain.models import AgentActivity, AgentActivityState

_LABELS: dict[AgentActivityState, str] = {
    AgentActivityState.IDLE: "Bereit",
    AgentActivityState.STARTING: "Wird gestartet …",
    AgentActivityState.THINKING: "Working …",
    AgentActivityState.PLANNING: "Plant …",
    AgentActivityState.READING: "Liest …",
    AgentActivityState.SEARCHING: "Sucht …",
    AgentActivityState.EXECUTING: "Führt aus …",
    AgentActivityState.EDITING: "Bearbeitet Dateien …",
    AgentActivityState.TESTING: "Testet …",
    AgentActivityState.AWAITING_APPROVAL: "Wartet auf Freigabe",
    AgentActivityState.RESPONDING: "Antwortet …",
    AgentActivityState.COMPLETED: "Abgeschlossen",
    AgentActivityState.FAILED: "Fehlgeschlagen",
    AgentActivityState.CANCELLED: "Abgebrochen",
    AgentActivityState.RECONNECTING: "Verbindung wird wiederhergestellt …",
}


@dataclass(slots=True)
class _MutableActivity:
    state: AgentActivityState = AgentActivityState.IDLE
    started_at: datetime | None = None
    started_monotonic: float | None = None
    last_activity: str | None = None
    current_tool: str | None = None
    current_command: str | None = None
    pending_approval_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ActivityAggregator:
    """State reducer with explicit precedence and no synthetic progress value."""

    def __init__(self) -> None:
        self._activities: dict[str, _MutableActivity] = {}

    def start(self, thread_id: str) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        now = datetime.now(UTC)
        value.state = AgentActivityState.STARTING
        value.started_at = now
        value.started_monotonic = time.monotonic()
        value.last_activity = "Turn wird gestartet"
        value.current_tool = None
        value.current_command = None
        value.pending_approval_id = None
        value.error_code = None
        value.error_message = None
        return self.snapshot(thread_id)

    def forget(self, thread_id: str) -> None:
        """Discard all transient presentation state for a deleted thread."""
        self._activities.pop(thread_id, None)

    def reconnecting(self, thread_id: str) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        if value.state not in {
            AgentActivityState.COMPLETED,
            AgentActivityState.CANCELLED,
            AgentActivityState.FAILED,
            AgentActivityState.IDLE,
        }:
            value.state = AgentActivityState.RECONNECTING
            value.last_activity = "App Server wird neu verbunden"
        return self.snapshot(thread_id)

    def reconcile(self, thread_id: str, *, active: bool, status: str) -> AgentActivity:
        """Align local presentation state with the authoritative native thread state."""
        value = self._activities.setdefault(thread_id, _MutableActivity())
        if active:
            if value.started_at is None:
                value.started_at = datetime.now(UTC)
                value.started_monotonic = time.monotonic()
            if value.state in {
                AgentActivityState.IDLE,
                AgentActivityState.COMPLETED,
                AgentActivityState.CANCELLED,
                AgentActivityState.FAILED,
                AgentActivityState.RECONNECTING,
            }:
                value.state = AgentActivityState.THINKING
                value.last_activity = "Aktiver Turn wiederhergestellt"
        else:
            if status in {"failed", "error"}:
                value.state = AgentActivityState.FAILED
            elif status in {"interrupted", "cancelled"}:
                value.state = AgentActivityState.CANCELLED
            elif value.state not in {AgentActivityState.FAILED, AgentActivityState.CANCELLED}:
                value.state = AgentActivityState.IDLE
            value.current_tool = None
            value.current_command = None
            value.pending_approval_id = None
        return self.snapshot(thread_id)

    def fail_start(self, thread_id: str) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        value.state = AgentActivityState.FAILED
        value.last_activity = "Turn konnte nicht gestartet werden"
        value.error_code = "turn_start_failed"
        value.error_message = "Der Turn konnte nicht gestartet werden."
        value.current_tool = None
        value.current_command = None
        return self.snapshot(thread_id)

    def approval(self, thread_id: str, approval_id: str, summary: str) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        value.state = AgentActivityState.AWAITING_APPROVAL
        value.pending_approval_id = approval_id
        value.last_activity = summary[:500]
        return self.snapshot(thread_id)

    def approval_resolved(self, thread_id: str, approval_id: str) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        if value.pending_approval_id == approval_id:
            value.pending_approval_id = None
            if value.state is AgentActivityState.AWAITING_APPROVAL:
                value.state = AgentActivityState.THINKING
                value.last_activity = "Freigabe beantwortet"
        return self.snapshot(thread_id)

    def notification(self, thread_id: str, method: str, params: JsonObject) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        if method == "turn/started":
            if value.started_at is None:
                value.started_at = datetime.now(UTC)
                value.started_monotonic = time.monotonic()
            value.state = AgentActivityState.THINKING
            value.last_activity = "Turn gestartet"
        elif method == "turn/plan/updated" or method == "item/plan/delta":
            value.state = AgentActivityState.PLANNING
            value.last_activity = "Plan aktualisiert"
        elif method in {
            "item/agentMessage/delta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/textDelta",
        }:
            value.state = (
                AgentActivityState.RESPONDING
                if method == "item/agentMessage/delta"
                else AgentActivityState.THINKING
            )
            value.last_activity = (
                "Antwort wird geschrieben"
                if method == "item/agentMessage/delta"
                else "Reasoning-Zusammenfassung aktualisiert"
            )
        elif method in {"item/fileChange/patchUpdated", "turn/diff/updated"}:
            value.state = AgentActivityState.EDITING
            value.last_activity = "Dateiänderungen werden aktualisiert"
        elif method == "item/started":
            item = params.get("item")
            if isinstance(item, dict):
                self._item_started(value, item)
        elif method == "turn/completed":
            turn = params.get("turn")
            status = turn.get("status") if isinstance(turn, dict) else None
            if status == "completed":
                value.state = AgentActivityState.COMPLETED
                value.last_activity = "Turn abgeschlossen"
            elif status == "interrupted":
                value.state = AgentActivityState.CANCELLED
                value.last_activity = "Turn abgebrochen"
            else:
                value.state = AgentActivityState.FAILED
                value.last_activity = "Turn fehlgeschlagen"
            value.current_tool = None
            value.current_command = None
            value.pending_approval_id = None
        elif method == "error":
            error = params.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            value.error_code = "codex_error"
            value.error_message = message[:1000] if isinstance(message, str) else "Codex-Fehler"
            if params.get("willRetry") is not True:
                value.state = AgentActivityState.FAILED
        return self.snapshot(thread_id)

    def snapshot(self, thread_id: str) -> AgentActivity:
        value = self._activities.setdefault(thread_id, _MutableActivity())
        observed = datetime.now(UTC)
        elapsed: int | None = None
        if value.started_monotonic is not None and value.state not in {
            AgentActivityState.IDLE,
        }:
            elapsed = max(0, int((time.monotonic() - value.started_monotonic) * 1000))
        return AgentActivity(
            state=value.state,
            label=_LABELS[value.state],
            last_activity=value.last_activity,
            started_at=value.started_at,
            observed_at=observed,
            elapsed_ms=elapsed,
            current_tool=value.current_tool,
            current_command=value.current_command,
            pending_approval_id=value.pending_approval_id,
            error_code=value.error_code,
            error_message=value.error_message,
        )

    def _item_started(self, value: _MutableActivity, item: JsonObject) -> None:
        item_type = item.get("type")
        if item_type == "commandExecution":
            state = self._classify_command(item)
            value.state = state
            value.current_tool = "command"
            command = item.get("command")
            value.current_command = command[:2000] if isinstance(command, str) else None
            value.last_activity = "Befehl gestartet"
        elif item_type == "fileChange":
            value.state = AgentActivityState.EDITING
            value.current_tool = "fileChange"
            value.last_activity = "Dateiänderung gestartet"
        elif item_type == "webSearch":
            value.state = AgentActivityState.SEARCHING
            value.current_tool = "webSearch"
            value.last_activity = "Websuche gestartet"
        elif item_type == "plan":
            value.state = AgentActivityState.PLANNING
            value.last_activity = "Plan wird erstellt"
        elif item_type in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
            value.state = AgentActivityState.EXECUTING
            value.current_tool = str(item_type)
            value.last_activity = "Tool gestartet"
        elif item_type == "contextCompaction":
            value.state = AgentActivityState.THINKING
            value.last_activity = "Kontext wird kompaktiert"

    @staticmethod
    def _classify_command(item: JsonObject) -> AgentActivityState:
        actions = item.get("commandActions")
        if isinstance(actions, list):
            action_types = {
                action.get("type")
                for action in actions
                if isinstance(action, dict) and isinstance(action.get("type"), str)
            }
            if action_types and action_types <= {"read", "listFiles"}:
                return AgentActivityState.READING
            if "search" in action_types:
                return AgentActivityState.SEARCHING
        command: JsonValue | None = item.get("command")
        if isinstance(command, str):
            executable = command.strip().split(maxsplit=1)[0].rsplit("/", 1)[-1]
            if executable in {"pytest", "vitest", "jest", "ruff", "mypy", "eslint"}:
                return AgentActivityState.TESTING
            if executable in {"npm", "pnpm", "yarn", "make"} and any(
                marker in command for marker in (" test", " verify", " check", " build")
            ):
                return AgentActivityState.TESTING
        return AgentActivityState.EXECUTING
