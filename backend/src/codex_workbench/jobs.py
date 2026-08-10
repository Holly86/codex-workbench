"""Closed, backend-owned background jobs for privileged native integrations."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from codex_workbench.app_server.transport import JsonObject
from codex_workbench.git_service import GitResult

type JobType = Literal["git_clone", "git_fetch", "git_pull", "git_push", "github_publish"]
type JobStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass(slots=True)
class BackgroundJob:
    id: str
    owner_session_id: str
    repository_id: str | None
    repository_name: str
    type: JobType
    status: JobStatus
    started_at: int
    ended_at: int | None = None
    exit_code: int | None = None
    output: str = ""
    error: str | None = None
    command: str = ""
    duration_ms: int | None = None

    def browser_dict(self) -> JsonObject:
        return {
            "id": self.id,
            "repositoryId": self.repository_id,
            "repositoryName": self.repository_name,
            "type": self.type,
            "status": self.status,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "exitCode": self.exit_code,
            "output": self.output,
            "error": self.error,
            "command": self.command,
            "durationMs": self.duration_ms,
        }


class BackgroundJobRegistry:
    """Own tasks independently from browser/WebSocket lifetimes."""

    def __init__(self, *, history_limit: int = 100) -> None:
        self._history_limit = history_limit
        self._jobs: dict[str, BackgroundJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        owner_session_id: str,
        repository_id: str | None,
        repository_name: str,
        job_type: JobType,
        operation: Callable[[], Awaitable[GitResult]],
    ) -> BackgroundJob:
        job = BackgroundJob(
            id="job_" + secrets.token_urlsafe(18),
            owner_session_id=owner_session_id,
            repository_id=repository_id,
            repository_name=repository_name,
            type=job_type,
            status="queued",
            started_at=int(time.time()),
        )
        async with self._lock:
            self._jobs[job.id] = job
            self._trim()
        task = asyncio.create_task(self._run(job, operation), name=f"workbench-{job_type}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def list_for_session(self, session_id: str) -> tuple[JsonObject, ...]:
        async with self._lock:
            values = [job for job in self._jobs.values() if job.owner_session_id == session_id]
        values.sort(key=lambda job: job.started_at, reverse=True)
        return tuple(job.browser_dict() for job in values)

    async def stop(self) -> None:
        # Application shutdown is the only lifecycle boundary allowed to cancel jobs.
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job: BackgroundJob, operation: Callable[[], Awaitable[GitResult]]) -> None:
        job.status = "running"
        try:
            result = await operation()
            job.command = result.command
            job.exit_code = result.exit_code
            job.output = result.output
            job.duration_ms = result.duration_ms
            job.status = "succeeded" if result.exit_code == 0 else "failed"
            if result.exit_code != 0:
                job.error = "Die native Operation ist fehlgeschlagen."
        except Exception:
            job.status = "failed"
            job.error = "Die native Operation konnte nicht abgeschlossen werden."
        finally:
            job.ended_at = int(time.time())

    def _trim(self) -> None:
        if len(self._jobs) <= self._history_limit:
            return
        completed = sorted(
            (job for job in self._jobs.values() if job.status in {"succeeded", "failed"}),
            key=lambda job: job.started_at,
        )
        for job in completed[: len(self._jobs) - self._history_limit]:
            self._jobs.pop(job.id, None)
