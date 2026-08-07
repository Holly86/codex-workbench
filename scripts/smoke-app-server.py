#!/usr/bin/env python3
"""Repository-contained Codex 0.146.0 runtime smoke; never uses the real Codex home."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from codex_workbench.app_server.client import AppServerClient
from codex_workbench.app_server.correlation import AppServerRequestError
from codex_workbench.app_server.protocol import ProtocolAdapter
from codex_workbench.app_server.supervisor import APP_SERVER_ARGUMENTS, check_codex_version
from codex_workbench.app_server.transport import (
    JsonObject,
    JsonScalar,
    SubprocessJsonlTransport,
)


async def run_checked(*arguments: str, capture_stdout: bool = False) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("runtime smoke subprocess timed out") from None
    if process.returncode != 0:
        raise RuntimeError("runtime smoke subprocess failed")
    return stdout


async def smoke() -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[1]
    codex = Path("/usr/local/bin/codex")
    if not codex.exists():
        raise RuntimeError("runtime smoke requires /usr/local/bin/codex")

    with tempfile.TemporaryDirectory(prefix=".runtime-smoke-", dir=repository_root) as temporary:
        root = Path(temporary)
        fixture_repository = root / "repository"
        fixture_repository.mkdir(mode=0o700)
        await run_checked("git", "init", "--quiet", str(fixture_repository))
        codex_home = root / "codex-home"
        home = root / "home"
        temporary_dir = root / "tmp"
        for directory in (codex_home, home, temporary_dir):
            directory.mkdir(mode=0o700)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "LANG",
                "LC_ALL",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            }
        }
        environment.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "TMPDIR": str(temporary_dir),
            }
        )
        version = await check_codex_version(
            str(codex),
            expected_version="0.146.0",
            environment=environment,
            timeout_seconds=15,
        )
        notifications: list[str] = []
        completed = asyncio.Event()

        async def notification(_generation: int, method: str, _params: JsonObject) -> None:
            notifications.append(method)
            if method == "turn/completed":
                completed.set()

        async def server_request(
            _generation: int,
            request_id: JsonScalar,
            _method: str,
            _params: JsonObject,
        ) -> None:
            await client.respond_error(
                request_id, code=-32000, message="Runtime smoke refuses server requests"
            )

        async def disconnected(_generation: int, _error: BaseException) -> None:
            return None

        transport = SubprocessJsonlTransport(
            (str(codex), *APP_SERVER_ARGUMENTS),
            environment=environment,
            shutdown_timeout_seconds=3,
        )
        await transport.start()
        client = AppServerClient(
            transport,
            generation=1,
            application_version="0.1.0-smoke",
            request_timeout_seconds=15,
            notification_handler=notification,
            server_request_handler=server_request,
            disconnect_handler=disconnected,
        )
        adapter = ProtocolAdapter()
        account_available = False
        rate_limits_available = False
        thread_started = False
        turn_result = "not_attempted"
        try:
            await client.initialize()
            try:
                account = await client.call(
                    "account/read", {"refreshToken": False}, owner="runtime-smoke"
                )
                account_available = isinstance(account, dict) and account.get("account") is not None
            except AppServerRequestError:
                account_available = False
            try:
                limits = await client.call("account/rateLimits/read", None, owner="runtime-smoke")
                rate_limits_available = isinstance(limits, dict)
            except AppServerRequestError:
                rate_limits_available = False
            started = await client.call(
                "thread/start",
                adapter.thread_start_params(str(fixture_repository), "read-only", "never"),
                owner="runtime-smoke",
            )
            thread = started.get("thread") if isinstance(started, dict) else None
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            thread_started = isinstance(thread_id, str)
            if isinstance(thread_id, str):
                try:
                    await client.call(
                        "turn/start",
                        adapter.turn_start_params(
                            thread_id=thread_id,
                            cwd=str(fixture_repository),
                            text="Reply exactly SMOKE_OK. Do not call tools.",
                            image_paths=(),
                            sandbox_mode="read-only",
                            approval_policy="never",
                            client_message_id="repository-contained-runtime-smoke",
                        ),
                        owner="runtime-smoke",
                    )
                    await asyncio.wait_for(completed.wait(), timeout=30)
                    turn_result = "completed"
                except (AppServerRequestError, TimeoutError):
                    turn_result = "unavailable_without_repository_local_auth"
        finally:
            await client.close()
            await transport.stop()
        return {
            "version": f"codex-cli {version}",
            "initialized": True,
            "repositoryContainedCodexHome": True,
            "accountAvailable": account_available,
            "rateLimitsAvailable": rate_limits_available,
            "threadStarted": thread_started,
            "turnResult": turn_result,
            "notificationMethods": sorted(set(notifications)),
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(smoke()), sort_keys=True))
