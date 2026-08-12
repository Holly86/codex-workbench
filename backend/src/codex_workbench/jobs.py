"""Closed, backend-owned background jobs for privileged native integrations."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from codex_workbench.app_server.transport import JsonObject
from codex_workbench.git_service import GitOperationTimeoutError, GitResult

type JobType = Literal["git_clone", "git_fetch", "git_pull", "git_push", "github_publish"]
type JobStatus = Literal[
    "queued", "running", "succeeded", "failed", "interrupted", "timed_out"
]
type JobUpdateHandler = Callable[[str, JsonObject], Awaitable[None]]

logger = logging.getLogger("codex_workbench.jobs")


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
    error_code: str | None = None
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
            "error": self.error,
            "errorCode": self.error_code,
            "retryable": self.type in {"git_fetch", "git_pull"}
            and self.status in {"failed", "timed_out", "interrupted"},
            "operationLabel": {
                "git_clone": "Repository klonen",
                "git_fetch": "Remote-Stand abrufen",
                "git_pull": "Fast-Forward Pull",
                "git_push": "Änderungen pushen",
                "github_publish": "Repository veröffentlichen",
            }[self.type],
            "durationMs": self.duration_ms,
        }


class BackgroundJobRegistry:
    """Own tasks independently from browser/WebSocket lifetimes."""

    def __init__(
        self, *, history_limit: int = 100, update_handler: JobUpdateHandler | None = None
    ) -> None:
        self._history_limit = history_limit
        self._update_handler = update_handler
        self._jobs: dict[str, BackgroundJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._blocked_repositories: set[str] = set()
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
            if repository_id is not None and repository_id in self._blocked_repositories:
                raise RuntimeError("repository operations are temporarily blocked")
            self._jobs[job.id] = job
            self._trim()
        await self._notify(job)
        task = asyncio.create_task(self._run(job, operation), name=f"workbench-{job_type}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def list_for_session(self, session_id: str) -> tuple[JsonObject, ...]:
        async with self._lock:
            values = [job for job in self._jobs.values() if job.owner_session_id == session_id]
        values.sort(key=lambda job: job.started_at, reverse=True)
        return tuple(job.browser_dict() for job in values)

    async def has_active_repository_job(self, repository_id: str) -> bool:
        async with self._lock:
            return any(
                job.repository_id == repository_id and job.status in {"queued", "running"}
                for job in self._jobs.values()
            )

    async def status_counts(self) -> tuple[int, int, int]:
        """Return aggregate active, queued, and failed counts without job content."""

        async with self._lock:
            active = sum(job.status == "running" for job in self._jobs.values())
            queued = sum(job.status == "queued" for job in self._jobs.values())
            failed = sum(
                job.status in {"failed", "interrupted", "timed_out"}
                for job in self._jobs.values()
            )
        return active, queued, failed

    async def reserve_repository_removal(self, repository_id: str) -> bool:
        async with self._lock:
            if repository_id in self._blocked_repositories:
                return False
            if any(
                job.repository_id == repository_id and job.status in {"queued", "running"}
                for job in self._jobs.values()
            ):
                return False
            self._blocked_repositories.add(repository_id)
            return True

    async def release_repository_removal(self, repository_id: str) -> None:
        async with self._lock:
            self._blocked_repositories.discard(repository_id)

    async def stop(self) -> None:
        # Application shutdown is the only lifecycle boundary allowed to cancel jobs.
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job: BackgroundJob, operation: Callable[[], Awaitable[GitResult]]) -> None:
        job.status = "running"
        await self._notify(job)
        try:
            result = await operation()
            job.command = result.command
            job.exit_code = result.exit_code
            job.output = result.output
            job.duration_ms = result.duration_ms
            job.status = "succeeded" if result.exit_code == 0 else "failed"
            if result.exit_code != 0:
                job.error = "Die native Operation ist fehlgeschlagen."
                job.error_code = "native_operation_failed"
        except GitOperationTimeoutError:
            job.status = "timed_out"
            job.error = "Der Vorgang hat das zulässige Zeitlimit überschritten."
            job.error_code = "git_operation_timeout"
            logger.warning(
                "background_job_failed",
                extra={"job_type": job.type, "error_type": "GitOperationTimeoutError"},
            )
        except asyncio.CancelledError:
            job.status = "interrupted"
            job.error = "Der Vorgang wurde beim Herunterfahren kontrolliert beendet."
            job.error_code = "service_shutdown"
            logger.warning(
                "background_job_interrupted",
                extra={"job_type": job.type, "state": "interrupted"},
            )
            raise
        except Exception as exc:
            job.status = "failed"
            job.error = "Die native Operation konnte nicht abgeschlossen werden."
            job.error_code = "native_operation_failed"
            logger.warning(
                "background_job_failed",
                extra={"job_type": job.type, "error_type": type(exc).__name__},
            )
        finally:
            job.ended_at = int(time.time())
            await self._notify(job)

    async def _notify(self, job: BackgroundJob) -> None:
        if self._update_handler is not None:
            try:
                await self._update_handler(job.owner_session_id, job.browser_dict())
            except Exception as exc:
                logger.warning(
                    "job_realtime_update_failed",
                    extra={"error_type": type(exc).__name__},
                )

    def _trim(self) -> None:
        if len(self._jobs) <= self._history_limit:
            return
        completed = sorted(
            (
                job
                for job in self._jobs.values()
                if job.status in {"succeeded", "failed", "interrupted", "timed_out"}
            ),
            key=lambda job: job.started_at,
        )
        for job in completed[: len(self._jobs) - self._history_limit]:
            self._jobs.pop(job.id, None)
