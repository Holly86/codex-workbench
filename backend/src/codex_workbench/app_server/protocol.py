"""Version-exact Codex 0.146.0 protocol construction and shape guards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from codex_workbench.app_server.transport import JsonObject, JsonValue

EXPECTED_CODEX_VERSION = "0.146.0"
SUPPORTED_APPROVAL_POLICIES = frozenset({"untrusted", "on-request", "never"})
SUPPORTED_SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})


class EnvelopeKind(StrEnum):
    RESPONSE = "response"
    NOTIFICATION = "notification"
    SERVER_REQUEST = "server_request"


@dataclass(frozen=True, slots=True)
class ClassifiedEnvelope:
    kind: EnvelopeKind
    method: str | None
    params: JsonObject


class ProtocolAdapter:
    """Keep raw protocol validation/construction outside domain and delivery code."""

    def classify(self, envelope: JsonObject) -> ClassifiedEnvelope:
        method = envelope.get("method")
        has_id = "id" in envelope
        if method is None and has_id:
            return ClassifiedEnvelope(EnvelopeKind.RESPONSE, None, {})
        if not isinstance(method, str) or not method or len(method) > 200:
            raise ValueError("protocol method is invalid")
        raw_params = envelope.get("params")
        if raw_params is None:
            params: JsonObject = {}
        elif isinstance(raw_params, dict) and all(isinstance(key, str) for key in raw_params):
            params = raw_params
        else:
            raise ValueError("protocol params must be an object")
        kind = EnvelopeKind.SERVER_REQUEST if has_id else EnvelopeKind.NOTIFICATION
        return ClassifiedEnvelope(kind, method, params)

    @staticmethod
    def initialize_params(application_version: str) -> JsonObject:
        return {
            "clientInfo": {
                "name": "codex_workbench",
                "title": "Codex Workbench",
                "version": application_version,
            },
            "capabilities": {
                "experimentalApi": False,
                "requestAttestation": False,
            },
        }

    @staticmethod
    def validate_initialize_result(result: JsonValue) -> None:
        if not isinstance(result, dict):
            raise ValueError("initialize result is not an object")
        required = ("userAgent", "codexHome", "platformFamily", "platformOs")
        if any(not isinstance(result.get(field), str) for field in required):
            raise ValueError("initialize result is missing required fields")

    @staticmethod
    def thread_list_params(cwd: str, *, cursor: str | None = None) -> JsonObject:
        params: JsonObject = {
            "limit": 100,
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "cwd": cwd,
            "archived": False,
        }
        if cursor is not None:
            params["cursor"] = cursor
        return params

    @staticmethod
    def thread_start_params(
        cwd: str,
        sandbox_mode: str,
        approval_policy: str,
    ) -> JsonObject:
        ProtocolAdapter.validate_permission_selection(sandbox_mode, approval_policy)
        return {
            "cwd": cwd,
            "sandbox": sandbox_mode,
            "approvalPolicy": approval_policy,
            "approvalsReviewer": "user",
            "ephemeral": False,
        }

    @staticmethod
    def thread_resume_params(
        thread_id: str,
        cwd: str,
        sandbox_mode: str,
        approval_policy: str,
    ) -> JsonObject:
        ProtocolAdapter.validate_permission_selection(sandbox_mode, approval_policy)
        return {
            "threadId": thread_id,
            "cwd": cwd,
            "sandbox": sandbox_mode,
            "approvalPolicy": approval_policy,
            "approvalsReviewer": "user",
        }

    @staticmethod
    def sandbox_policy(cwd: str, sandbox_mode: str) -> JsonObject:
        if sandbox_mode == "read-only":
            return {"type": "readOnly", "networkAccess": False}
        if sandbox_mode == "workspace-write":
            return {
                "type": "workspaceWrite",
                "writableRoots": [cwd],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            }
        if sandbox_mode == "danger-full-access":
            return {"type": "dangerFullAccess"}
        raise ValueError("unsupported sandbox mode")

    @staticmethod
    def turn_start_params(
        *,
        thread_id: str,
        cwd: str,
        text: str,
        image_paths: tuple[tuple[str, str], ...],
        sandbox_mode: str,
        approval_policy: str,
        client_message_id: str,
    ) -> JsonObject:
        ProtocolAdapter.validate_permission_selection(sandbox_mode, approval_policy)
        inputs: list[JsonValue] = [{"type": "text", "text": text, "text_elements": []}]
        inputs.extend(
            {"type": "localImage", "path": path, "detail": detail} for path, detail in image_paths
        )
        return {
            "threadId": thread_id,
            "clientUserMessageId": client_message_id,
            "input": inputs,
            "cwd": cwd,
            "approvalPolicy": approval_policy,
            "approvalsReviewer": "user",
            "sandboxPolicy": ProtocolAdapter.sandbox_policy(cwd, sandbox_mode),
        }

    @staticmethod
    def validate_permission_selection(sandbox_mode: str, approval_policy: str) -> None:
        if sandbox_mode not in SUPPORTED_SANDBOX_MODES:
            raise ValueError("unsupported sandbox mode")
        if approval_policy not in SUPPORTED_APPROVAL_POLICIES:
            raise ValueError("unsupported approval policy")
