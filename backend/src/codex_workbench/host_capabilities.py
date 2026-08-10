"""Abstract host capability detection without exposing host internals."""

from __future__ import annotations

import asyncio
import shutil

from codex_workbench.app_server.transport import JsonObject


async def detect_sandbox_capabilities() -> JsonObject:
    available = False
    executable = shutil.which("bwrap")
    if executable is not None:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--unshare-user",
                "--",
                "/bin/true",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=3)
            available = process.returncode == 0
        except (OSError, TimeoutError):
            available = False
    reason = None if available else "host_linux_sandbox_unavailable"
    return {
        "sandbox": {
            "workspaceWrite": {"available": available, "reason": reason},
            "readOnly": {"available": available, "reason": reason},
            "fullAccess": {"available": True, "reason": None},
        }
    }
