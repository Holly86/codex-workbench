from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.client import AppServerClient
from codex_workbench.app_server.correlation import (
    AppServerDisconnectedError,
    AppServerRequestError,
    AppServerRequestTimeoutError,
)
from codex_workbench.app_server.supervisor import ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.logging import JsonFormatter
from codex_workbench.persistence.metadata import MetadataRepository
from codex_workbench.runtime import WorkbenchRuntime
from tests.helpers import build_settings


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def call(
        self, method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del method, params, owner
        raise self.error


def _runtime(tmp_path: Path, error: Exception, *, generation: int = 2) -> WorkbenchRuntime:
    settings = build_settings(tmp_path).model_copy(
        update={"app_server_start_enabled": True}
    )
    runtime = WorkbenchRuntime(
        settings,
        cast(MetadataRepository, object()),
    )
    runtime.supervisor._state = ProcessState.READY  # noqa: SLF001
    runtime.supervisor._generation = generation  # noqa: SLF001
    runtime.supervisor._client = cast(AppServerClient, _FailingClient(error))  # noqa: SLF001
    return runtime


def _diagnostic_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "codex_workbench.runtime"
        and record.getMessage() == "app_server_request_error"
    ]


@pytest.mark.asyncio
async def test_call_logs_safe_request_error_and_reraises(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = AppServerRequestError(-32602, "sensitive test message")
    runtime = _runtime(tmp_path, error)
    params: JsonObject = {
        "text": "sensitive prompt",
        "cwd": "C:/sensitive/repository",
    }
    owner = "sensitive owner"
    caplog.set_level(logging.WARNING, logger="codex_workbench.runtime")

    with pytest.raises(AppServerRequestError) as captured:
        await runtime._call("thread/start", params, owner=owner)  # noqa: SLF001

    assert captured.value is error
    records = _diagnostic_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert record.__dict__["method"] == "thread/start"
    assert record.__dict__["error_code"] == -32602
    assert record.__dict__["generation"] == 2
    formatted_record = JsonFormatter().format(record)
    payload = cast(dict[str, object], json.loads(formatted_record))
    assert set(payload) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "method",
        "error_code",
        "generation",
    }
    for sensitive in (
        "sensitive test message",
        "sensitive prompt",
        "sensitive owner",
        "C:/sensitive/repository",
    ):
        assert sensitive not in caplog.text
        assert sensitive not in formatted_record


@pytest.mark.asyncio
async def test_call_does_not_log_disconnect_as_request_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(tmp_path, AppServerDisconnectedError("transport ended"))
    caplog.set_level(logging.WARNING, logger="codex_workbench.runtime")

    with pytest.raises(AppServerDisconnectedError):
        await runtime._call("thread/start", {}, owner="owner")  # noqa: SLF001

    assert _diagnostic_records(caplog) == []


@pytest.mark.asyncio
async def test_call_does_not_log_timeout_as_request_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(tmp_path, AppServerRequestTimeoutError("request timed out"))
    caplog.set_level(logging.WARNING, logger="codex_workbench.runtime")

    with pytest.raises(AppServerRequestTimeoutError):
        await runtime._call("thread/start", {}, owner="owner")  # noqa: SLF001

    assert _diagnostic_records(caplog) == []
