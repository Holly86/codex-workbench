from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest

from codex_workbench.app_server.client import AppServerClient
from codex_workbench.app_server.correlation import (
    AppServerDisconnectedError,
    AppServerRequestError,
    AppServerRequestTimeoutError,
    RequestCorrelator,
)
from codex_workbench.app_server.supervisor import (
    AppServerSupervisor,
    CodexVersionCheckError,
    ProcessState,
    VersionCheckReason,
    check_codex_version,
    parse_codex_version_output,
)
from codex_workbench.app_server.transport import (
    AppServerEofError,
    AppServerProtocolError,
    JsonObject,
    JsonScalar,
    SubprocessJsonlTransport,
)
from codex_workbench.config import Settings
from tests.helpers import build_settings, executable_fixture

_FIXTURE = Path(__file__).parent / "fixtures" / "fake_codex.py"


@pytest.mark.parametrize(
    "stdout",
    [
        b"codex-cli 0.146.0",
        b"codex-cli 0.146.0\n",
        b"codex-cli 0.146.0\r\n",
        b" \t codex-cli 0.146.0 \r\n\t ",
    ],
)
def test_version_parser_accepts_only_the_supported_cli_line(stdout: bytes) -> None:
    assert parse_codex_version_output(stdout, b"", expected_version="0.146.0") == "0.146.0"


@pytest.mark.parametrize(
    ("stdout", "stderr", "reason"),
    [
        (b"codex-cli 0.145.0\n", b"", VersionCheckReason.UNSUPPORTED_VERSION),
        (b"0.146.0\n", b"", VersionCheckReason.MALFORMED_VERSION_OUTPUT),
        (b"codex 0.146.0\n", b"", VersionCheckReason.MALFORMED_VERSION_OUTPUT),
        (
            b"codex-cli 0.146.0\nunexpected line\n",
            b"",
            VersionCheckReason.MALFORMED_VERSION_OUTPUT,
        ),
        (
            b"codex-cli 0.146.0\n",
            b"harmless-looking warning\n",
            VersionCheckReason.UNEXPECTED_STDERR,
        ),
    ],
)
def test_version_parser_fails_closed_with_a_safe_reason(
    stdout: bytes,
    stderr: bytes,
    reason: VersionCheckReason,
) -> None:
    with pytest.raises(CodexVersionCheckError) as captured:
        parse_codex_version_output(stdout, stderr, expected_version="0.146.0")
    assert captured.value.reason is reason


def _version_probe(tmp_path: Path, body: str, *, executable: bool = True) -> Path:
    del tmp_path
    return executable_fixture(
        "codex-version-probe-" + str(abs(hash(body))), body, executable=executable
    )


async def _run_version_probe(path: Path, *, probe_timeout: float = 1.0) -> str:
    return await check_codex_version(
        str(path),
        expected_version="0.146.0",
        environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        timeout_seconds=probe_timeout,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("exit 7", VersionCheckReason.EXECUTION_FAILED),
        ("printf '%s\\n' 'codex-cli 0.146.0' >&2", VersionCheckReason.UNEXPECTED_STDERR),
    ],
)
async def test_version_probe_rejects_failed_execution_and_stderr(
    tmp_path: Path,
    body: str,
    reason: VersionCheckReason,
) -> None:
    with pytest.raises(CodexVersionCheckError) as captured:
        await _run_version_probe(_version_probe(tmp_path, body))
    assert captured.value.reason is reason


@pytest.mark.asyncio
async def test_version_probe_reports_binary_not_found(tmp_path: Path) -> None:
    with pytest.raises(CodexVersionCheckError) as captured:
        await _run_version_probe(tmp_path / "missing-codex")
    assert captured.value.reason is VersionCheckReason.BINARY_NOT_FOUND


@pytest.mark.asyncio
async def test_version_probe_reports_permission_denied(tmp_path: Path) -> None:
    with pytest.raises(CodexVersionCheckError) as captured:
        await _run_version_probe(_version_probe(tmp_path, "exit 0", executable=False))
    assert captured.value.reason is VersionCheckReason.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_version_probe_reports_timeout(tmp_path: Path) -> None:
    with pytest.raises(CodexVersionCheckError) as captured:
        await _run_version_probe(_version_probe(tmp_path, "exec sleep 60"), probe_timeout=0.05)
    assert captured.value.reason is VersionCheckReason.TIMEOUT


def _transport(scenario: str = "normal") -> SubprocessJsonlTransport:
    environment = dict(os.environ)
    environment["FAKE_CODEX_SCENARIO"] = scenario
    return SubprocessJsonlTransport(
        (str(_FIXTURE), "app-server", "--stdio"),
        environment=environment,
        max_line_bytes=1024 * 1024,
        shutdown_timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_initialize_and_concurrent_request_correlation_are_deterministic() -> None:
    notifications: list[str] = []
    server_requests: list[tuple[JsonScalar, str]] = []
    disconnected = asyncio.Event()

    async def notification(_generation: int, method: str, _params: JsonObject) -> None:
        notifications.append(method)

    async def server_request(
        _generation: int, request_id: JsonScalar, method: str, _params: JsonObject
    ) -> None:
        server_requests.append((request_id, method))

    async def disconnect(_generation: int, _error: BaseException) -> None:
        disconnected.set()

    transport = _transport()
    await transport.start()
    client = AppServerClient(
        transport,
        generation=1,
        application_version="0.1.0",
        request_timeout_seconds=2,
        notification_handler=notification,
        server_request_handler=server_request,
        disconnect_handler=disconnect,
    )
    try:
        await client.initialize()
        first, second = await asyncio.gather(
            client.call("test/outOfOrder", {"value": 1}, owner="test"),
            client.call("test/outOfOrder", {"value": 2}, owner="test"),
        )
        assert first == {"value": 1}
        assert second == {"value": 2}
        assert notifications == []
        assert server_requests == []
        assert not disconnected.is_set()
    finally:
        await client.close()
        await transport.stop()


@pytest.mark.asyncio
async def test_unknown_response_ids_timeouts_and_cancellation_are_bounded() -> None:
    correlator = RequestCorrelator(7)
    pending = correlator.create("known", "test")
    assert correlator.resolve({"id": 999, "result": {}}) is False
    assert correlator.pending_count == 1
    correlator.abandon(pending.request_id)
    assert correlator.pending_count == 0

    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    transport = _transport()
    await transport.start()
    client = AppServerClient(
        transport,
        generation=1,
        application_version="0.1.0",
        request_timeout_seconds=2,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
    )
    try:
        await client.initialize()
        with pytest.raises(AppServerRequestTimeoutError):
            await client.call("test/never", {}, owner="timeout", timeout_seconds=0.05)
        cancelled = asyncio.create_task(
            client.call("test/never", {}, owner="cancel", timeout_seconds=10)
        )
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
    finally:
        await client.close()
        await transport.stop()


@pytest.mark.asyncio
async def test_json_rpc_error_and_handler_error_do_not_disconnect_client() -> None:
    disconnected = asyncio.Event()

    async def failing_notification(
        _generation: int, _method: str, _params: JsonObject
    ) -> None:
        raise ValueError("projection failed")

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def disconnect(_generation: int, _error: BaseException) -> None:
        disconnected.set()

    transport = _transport("plugin_method_not_found")
    await transport.start()
    client = AppServerClient(
        transport,
        generation=1,
        application_version="0.1.0",
        request_timeout_seconds=2,
        notification_handler=failing_notification,
        server_request_handler=noop_request,
        disconnect_handler=disconnect,
    )
    try:
        await client.initialize()
        with pytest.raises(AppServerRequestError) as captured:
            await client.call("plugin/list", {"cwds": []}, owner="test")
        assert captured.value.code == -32601
        assert await client.call("test/notifyThenRespond", {}, owner="test") == {"ok": True}
        assert await client.call("model/list", {}, owner="test")
        assert not disconnected.is_set()
    finally:
        await client.close()
        await transport.stop()


@pytest.mark.asyncio
async def test_malformed_json_is_a_transport_failure() -> None:
    disconnected: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    async def notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def server_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def disconnect(_generation: int, error: BaseException) -> None:
        if not disconnected.done():
            disconnected.set_result(error)

    transport = _transport("malformed")
    await transport.start()
    client = AppServerClient(
        transport,
        generation=1,
        application_version="0.1.0",
        request_timeout_seconds=2,
        notification_handler=notification,
        server_request_handler=server_request,
        disconnect_handler=disconnect,
    )
    try:
        await client.initialize()
        error = await asyncio.wait_for(disconnected, timeout=2)
        assert isinstance(error, AppServerProtocolError)
    finally:
        await client.close()
        await transport.stop()


@pytest.mark.asyncio
async def test_unexpected_clean_eof_is_a_transport_failure() -> None:
    disconnected: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    async def notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def server_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def disconnect(_generation: int, error: BaseException) -> None:
        if not disconnected.done():
            disconnected.set_result(error)

    transport = _transport("eof")
    await transport.start()
    client = AppServerClient(
        transport,
        generation=1,
        application_version="0.1.0",
        request_timeout_seconds=2,
        notification_handler=notification,
        server_request_handler=server_request,
        disconnect_handler=disconnect,
    )
    try:
        await client.initialize()
        error = await asyncio.wait_for(disconnected, timeout=2)
        assert isinstance(error, AppServerEofError)
    finally:
        await client.close()
        await transport.stop()


@pytest.mark.asyncio
async def test_stderr_without_newlines_is_drained_and_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    caplog.set_level(logging.WARNING, logger="codex_workbench.app_server.transport")
    transport = _transport("stderr_flood")
    await transport.start()
    client = AppServerClient(
        transport,
        generation=1,
        application_version="0.1.0",
        request_timeout_seconds=2,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
    )
    try:
        await client.initialize()
        result = await client.call("account/rateLimits/read", None, owner="stderr-test")
        assert isinstance(result, dict)
        assert "fixture-secret" not in caplog.text
        assert "authorization=secret" not in caplog.text
        assert "app_server_stderr" in caplog.text
    finally:
        await client.close()
        await transport.stop()


def _supervisor_settings(tmp_path: Path, *, restart_limit: int = 2) -> Settings:
    base = build_settings(tmp_path)
    values = base.model_dump()
    values.update(
        {
            "codex_executable": str(_FIXTURE),
            "app_server_start_enabled": True,
            "app_server_backoff_initial_seconds": 0.05,
            "app_server_backoff_max_seconds": 0.1,
            "app_server_restart_limit": restart_limit,
            "app_server_restart_window_seconds": 10,
        }
    )
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_supervisor_starts_one_initialized_generation(tmp_path: Path) -> None:
    ready: list[int] = []

    async def notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def server_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def disconnect(_generation: int, _error: BaseException) -> None:
        return None

    async def on_ready(generation: int) -> None:
        ready.append(generation)

    settings = _supervisor_settings(tmp_path)
    supervisor = AppServerSupervisor(
        settings,
        notification_handler=notification,
        server_request_handler=server_request,
        disconnect_handler=disconnect,
        ready_handler=on_ready,
    )
    try:
        await supervisor.start()
        assert supervisor.status.state is ProcessState.READY
        assert supervisor.status.version == "0.146.0"
        assert ready == [1]
    finally:
        await supervisor.stop()
    assert supervisor.status.state.value == "stopped"


@pytest.mark.asyncio
async def test_supervisor_stops_after_bounded_crash_restarts(tmp_path: Path) -> None:
    settings = _supervisor_settings(tmp_path, restart_limit=2)
    environment = dict(os.environ)
    environment["FAKE_CODEX_SCENARIO"] = "crash"
    terminal_failures: list[tuple[int, str]] = []

    def factory(_generation: int) -> SubprocessJsonlTransport:
        return SubprocessJsonlTransport(
            (str(_FIXTURE), "app-server", "--stdio"),
            environment=environment,
            shutdown_timeout_seconds=0.5,
        )

    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    async def noop_ready(_generation: int) -> None:
        return None

    async def terminal_failure(generation: int, error_code: str) -> None:
        terminal_failures.append((generation, error_code))

    async def version() -> str:
        return "0.146.0"

    supervisor = AppServerSupervisor(
        settings,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
        ready_handler=noop_ready,
        terminal_failure_handler=terminal_failure,
        transport_factory=factory,
        version_checker=version,
    )
    try:
        await supervisor.start()
        for _ in range(100):
            if supervisor.status.error_code == "restart_limit_reached":
                break
            await asyncio.sleep(0.02)
        assert supervisor.status.state is ProcessState.FAILED
        assert supervisor.status.error_code == "restart_limit_reached"
        assert supervisor.status.generation == 3
        assert terminal_failures == [(3, "restart_limit_reached")]
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "method"),
    [("plugin_eof", "plugin/list"), ("model_eof", "model/list")],
)
async def test_supervisor_restarts_after_capability_eof(
    tmp_path: Path, scenario: str, method: str
) -> None:
    settings = _supervisor_settings(tmp_path, restart_limit=2)
    environment = dict(os.environ)
    environment["FAKE_CODEX_SCENARIO"] = scenario

    def factory(_generation: int) -> SubprocessJsonlTransport:
        return SubprocessJsonlTransport(
            (str(_FIXTURE), "app-server", "--stdio"),
            environment=environment,
            shutdown_timeout_seconds=0.5,
        )

    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    async def noop_ready(_generation: int) -> None:
        return None

    async def version() -> str:
        return "0.146.0"

    supervisor = AppServerSupervisor(
        settings,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
        ready_handler=noop_ready,
        transport_factory=factory,
        version_checker=version,
    )
    try:
        await supervisor.start()
        with pytest.raises(AppServerDisconnectedError):
            await supervisor.client.call(method, {"cwds": []}, owner="test")
        for _ in range(100):
            if supervisor.status.state is ProcessState.READY and supervisor.status.generation == 2:
                break
            await asyncio.sleep(0.02)
        assert supervisor.status.state is ProcessState.READY
        assert supervisor.status.generation == 2
        assert supervisor.status.restart_attempts == 1
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_model_rpc_error_does_not_restart_supervisor(tmp_path: Path) -> None:
    settings = _supervisor_settings(tmp_path, restart_limit=2)
    environment = dict(os.environ)
    environment["FAKE_CODEX_SCENARIO"] = "model_error"

    def factory(_generation: int) -> SubprocessJsonlTransport:
        return SubprocessJsonlTransport(
            (str(_FIXTURE), "app-server", "--stdio"),
            environment=environment,
            shutdown_timeout_seconds=0.5,
        )

    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    async def noop_ready(_generation: int) -> None:
        return None

    async def version() -> str:
        return "0.146.0"

    supervisor = AppServerSupervisor(
        settings,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
        ready_handler=noop_ready,
        transport_factory=factory,
        version_checker=version,
    )
    try:
        await supervisor.start()
        with pytest.raises(AppServerRequestError):
            await supervisor.client.call("model/list", {}, owner="test")
        assert supervisor.status.state is ProcessState.READY
        assert supervisor.status.generation == 1
        assert supervisor.status.restart_attempts == 0
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_disconnect_during_recovery_requests_the_next_restart(tmp_path: Path) -> None:
    settings = _supervisor_settings(tmp_path, restart_limit=3)
    environment = dict(os.environ)
    environment["FAKE_CODEX_SCENARIO"] = "plugin_eof"
    ready: list[int] = []
    supervisor: AppServerSupervisor

    def factory(_generation: int) -> SubprocessJsonlTransport:
        return SubprocessJsonlTransport(
            (str(_FIXTURE), "app-server", "--stdio"),
            environment=environment,
            shutdown_timeout_seconds=0.5,
        )

    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    async def on_ready(generation: int) -> None:
        ready.append(generation)
        if generation == 2:
            with pytest.raises(AppServerDisconnectedError):
                await supervisor.client.call("plugin/list", {"cwds": []}, owner="recovery")

    async def version() -> str:
        return "0.146.0"

    supervisor = AppServerSupervisor(
        settings,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
        ready_handler=on_ready,
        transport_factory=factory,
        version_checker=version,
    )
    try:
        await supervisor.start()
        with pytest.raises(AppServerDisconnectedError):
            await supervisor.client.call("plugin/list", {"cwds": []}, owner="initial")
        for _ in range(200):
            if supervisor.status.state is ProcessState.READY and supervisor.status.generation == 3:
                break
            await asyncio.sleep(0.02)
        assert supervisor.status.state is ProcessState.READY
        assert supervisor.status.generation == 3
        assert supervisor.status.restart_attempts == 2
        assert ready == [1, 2, 3]
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_version_mismatch_fails_without_restart_loop(tmp_path: Path) -> None:
    settings = _supervisor_settings(tmp_path)

    async def noop_notification(_generation: int, _method: str, _params: JsonObject) -> None:
        return None

    async def noop_request(
        _generation: int, _request_id: JsonScalar, _method: str, _params: JsonObject
    ) -> None:
        return None

    async def noop_disconnect(_generation: int, _error: BaseException) -> None:
        return None

    async def noop_ready(_generation: int) -> None:
        return None

    async def incompatible_version() -> str:
        return "0.147.0"

    supervisor = AppServerSupervisor(
        settings,
        notification_handler=noop_notification,
        server_request_handler=noop_request,
        disconnect_handler=noop_disconnect,
        ready_handler=noop_ready,
        version_checker=incompatible_version,
    )
    await supervisor.start()
    assert supervisor.status.state is ProcessState.FAILED
    assert supervisor.status.error_code == "unsupported_version"
    assert supervisor.status.generation == 0
    assert supervisor.status.restart_attempts == 0
    await supervisor.stop()
