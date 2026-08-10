"""Typed authenticated repository, thread, turn, permission, approval, and usage API."""

from __future__ import annotations

import asyncio
import secrets
from http import HTTPStatus
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from codex_workbench.app_server.protocol import (
    SUPPORTED_APPROVAL_POLICIES,
    SUPPORTED_SANDBOX_MODES,
)
from codex_workbench.domain.capabilities import FullAccessConfirmationError
from codex_workbench.git_service import GitOperationError
from codex_workbench.jobs import JobType
from codex_workbench.runtime import (
    RuntimeConflictError,
    RuntimeUnavailableError,
    WorkbenchRuntime,
)
from codex_workbench.security.http import api_error
from codex_workbench.security.sessions import AuthenticatedSession
from codex_workbench.uploads import UploadUnavailableError

router = APIRouter(prefix="/api", tags=["workbench"])

type SandboxModeValue = Literal["read-only", "workspace-write", "danger-full-access"]
type ApprovalPolicyValue = Literal["untrusted", "on-request", "never"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PermissionSelection(StrictModel):
    sandboxMode: SandboxModeValue = "workspace-write"
    approvalPolicy: ApprovalPolicyValue = "on-request"
    confirmationId: Annotated[str | None, Field(max_length=200)] = None


class ThreadStartRequest(PermissionSelection):
    repositoryId: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    fullAccessScopeId: Annotated[str | None, Field(max_length=200)] = None


class ThreadResumeRequest(PermissionSelection):
    repositoryId: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class ImageReference(StrictModel):
    uploadId: Annotated[str, Field(pattern=r"^upload_[A-Za-z0-9_-]{20,100}$")]
    detail: Literal["auto", "low", "high", "original"] = "auto"


class TurnStartRequest(PermissionSelection):
    message: Annotated[str, Field(min_length=1, max_length=200_000)]
    images: Annotated[list[ImageReference], Field(max_length=32)] = Field(default_factory=list)
    idempotencyKey: Annotated[str, Field(min_length=16, max_length=100)]
    model: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    effort: Annotated[str | None, Field(pattern=r"^(none|minimal|low|medium|high|xhigh)$")] = None


class FullAccessPrepareRequest(StrictModel):
    repositoryId: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    approvalPolicy: ApprovalPolicyValue
    threadId: Annotated[str | None, Field(max_length=200)] = None


class ApprovalDecisionRequest(StrictModel):
    decision: Literal[
        "accept",
        "accept_for_session",
        "accept_with_execpolicy_amendment",
        "apply_network_policy_amendment",
        "decline",
        "cancel",
    ]


class GitOperationRequest(StrictModel):
    action: Literal[
        "status",
        "diff",
        "fetch",
        "pull",
        "push",
        "branches",
        "remotes",
        "log",
        "add",
        "commit",
        "branch-create",
        "branch-switch",
    ]
    files: Annotated[list[str], Field(max_length=500)] = Field(default_factory=list)
    message: Annotated[str | None, Field(max_length=10_000)] = None
    branch: Annotated[str | None, Field(max_length=200)] = None


class RepositoryCreateRequest(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    readme: bool = True


class RepositoryCloneRequest(RepositoryCreateRequest):
    url: Annotated[str, Field(min_length=1, max_length=2048)]


class RepositoryPreferencesRequest(StrictModel):
    displayName: Annotated[str | None, Field(max_length=80)] = None
    model: Annotated[str | None, Field(max_length=200)] = None
    effort: Annotated[str | None, Field(pattern=r"^(none|minimal|low|medium|high|xhigh)$")] = None


class GitHubPublishRequest(StrictModel):
    owner: Annotated[str, Field(min_length=1, max_length=39)]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    visibility: Literal["private", "public"] = "private"
    remote: Literal["origin"] = "origin"
    initialPush: bool = True
    publicConfirmation: Annotated[str | None, Field(max_length=64)] = None


def _runtime(request: Request) -> WorkbenchRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, WorkbenchRuntime):
        raise RuntimeUnavailableError("workbench runtime is unavailable")
    return runtime


def _session(request: Request) -> AuthenticatedSession:
    session = getattr(request.state, "authenticated_session", None)
    if not isinstance(session, AuthenticatedSession):
        raise RuntimeUnavailableError("authenticated session is unavailable")
    return session


def _mutation_allowed(runtime: WorkbenchRuntime, session_id: str) -> JSONResponse | None:
    decision = runtime.consume_mutation(session_id)
    if decision.allowed:
        return None
    return api_error(
        HTTPStatus.TOO_MANY_REQUESTS,
        "operation_rate_limited",
        "Zu viele Aktionen. Bitte kurz warten.",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _safe_error(exc: BaseException) -> JSONResponse:
    if isinstance(exc, RuntimeUnavailableError):
        return api_error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "app_server_unavailable",
            "Der Codex App Server ist momentan nicht verfügbar.",
        )
    if isinstance(exc, RuntimeConflictError):
        return api_error(HTTPStatus.CONFLICT, "operation_conflict", str(exc))
    if isinstance(exc, GitOperationError):
        return api_error(
            HTTPStatus.BAD_GATEWAY,
            "native_operation_failed",
            "Die native Git- oder GitHub-Operation ist fehlgeschlagen.",
        )
    if isinstance(exc, FullAccessConfirmationError):
        return api_error(
            HTTPStatus.FORBIDDEN,
            "full_access_confirmation_rejected",
            "Die Full-Access-Bestätigung ist ungültig oder abgelaufen.",
        )
    if isinstance(exc, ValueError | UploadUnavailableError):
        return api_error(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "operation_rejected",
            "Die Aktion konnte mit diesen Angaben nicht ausgeführt werden.",
        )
    return api_error(
        HTTPStatus.BAD_GATEWAY,
        "app_server_request_failed",
        "Die Anfrage an Codex ist fehlgeschlagen.",
    )


@router.get("/repositories")
async def repositories(request: Request) -> JSONResponse:
    try:
        values = await _runtime(request).list_repositories()
        return JSONResponse(
            {
                "repositories": list(
                    await asyncio.gather(
                        *(_runtime(request).repository_browser_dict(item) for item in values)
                    )
                )
            }
        )
    except Exception as exc:
        return _safe_error(exc)


@router.get("/capabilities")
async def capabilities(
    request: Request, repositoryId: str | None = None, refresh: bool = False
) -> JSONResponse:
    try:
        return JSONResponse(
            await _runtime(request).capabilities(
                repositoryId, _session(request).id, refresh=refresh
            )
        )
    except Exception as exc:
        return _safe_error(exc)


@router.get("/github/capability")
async def github_capability(request: Request, repositoryId: str | None = None) -> JSONResponse:
    try:
        _session(request)
        return JSONResponse(await _runtime(request).git.github_capability(repositoryId))
    except Exception as exc:
        return _safe_error(exc)


@router.post("/repositories/create")
async def create_repository(payload: RepositoryCreateRequest, request: Request) -> JSONResponse:
    runtime, session = _runtime(request), _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        result = await runtime.git.create(payload.name, readme=payload.readme)
        return JSONResponse(result.browser_dict(), status_code=HTTPStatus.CREATED)
    except Exception as exc:
        return _safe_error(exc)


@router.post("/repositories/clone")
async def clone_repository(payload: RepositoryCloneRequest, request: Request) -> JSONResponse:
    runtime, session = _runtime(request), _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        job = await runtime.jobs.start(
            owner_session_id=session.id,
            repository_id=None,
            repository_name=payload.name,
            job_type="git_clone",
            operation=lambda: runtime.git.clone(payload.name, payload.url),
        )
        return JSONResponse(job.browser_dict(), status_code=HTTPStatus.ACCEPTED)
    except Exception as exc:
        return _safe_error(exc)


@router.post("/repositories/{repository_id}/git")
async def git_operation(
    repository_id: str, payload: GitOperationRequest, request: Request
) -> JSONResponse:
    runtime, session = _runtime(request), _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        if payload.action in {"fetch", "pull", "push"}:
            summary, _path = await asyncio.to_thread(
                runtime.repositories.resolve_repository_id, repository_id
            )
            job = await runtime.jobs.start(
                owner_session_id=session.id,
                repository_id=repository_id,
                repository_name=summary.name,
                job_type=cast(JobType, f"git_{payload.action}"),
                operation=lambda: runtime.git.run(repository_id, payload.action),
            )
            return JSONResponse(job.browser_dict(), status_code=HTTPStatus.ACCEPTED)
        result = await runtime.git.run(
            repository_id,
            payload.action,
            files=tuple(payload.files),
            message=payload.message,
            branch=payload.branch,
        )
        return JSONResponse(result.browser_dict())
    except Exception as exc:
        return _safe_error(exc)


@router.get("/repositories/{repository_id}/git/status")
async def git_status(repository_id: str, request: Request) -> JSONResponse:
    try:
        _session(request)
        return JSONResponse(await _runtime(request).git.status(repository_id))
    except Exception as exc:
        return _safe_error(exc)


@router.patch("/repositories/{repository_id}")
async def update_repository(
    repository_id: str, payload: RepositoryPreferencesRequest, request: Request
) -> JSONResponse:
    runtime, session = _runtime(request), _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        return JSONResponse(
            await runtime.update_repository_preferences(
                repository_id,
                display_name=payload.displayName,
                model=payload.model,
                effort=payload.effort,
            )
        )
    except Exception as exc:
        return _safe_error(exc)


@router.get("/jobs")
async def jobs(request: Request) -> JSONResponse:
    try:
        session = _session(request)
        return JSONResponse(
            {"jobs": list(await _runtime(request).jobs.list_for_session(session.id))}
        )
    except Exception as exc:
        return _safe_error(exc)


@router.post("/repositories/{repository_id}/github/publish")
async def publish_github(
    repository_id: str, payload: GitHubPublishRequest, request: Request
) -> JSONResponse:
    runtime, session = _runtime(request), _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        summary, _path = await asyncio.to_thread(
            runtime.repositories.resolve_repository_id, repository_id
        )
        job = await runtime.jobs.start(
            owner_session_id=session.id,
            repository_id=repository_id,
            repository_name=summary.name,
            job_type="github_publish",
            operation=lambda: runtime.git.publish_github(
                repository_id,
                owner=payload.owner,
                name=payload.name,
                visibility=payload.visibility,
                remote=payload.remote,
                initial_push=payload.initialPush,
                public_confirmation=payload.publicConfirmation,
            ),
        )
        return JSONResponse(job.browser_dict(), status_code=HTTPStatus.ACCEPTED)
    except Exception as exc:
        return _safe_error(exc)


@router.get("/threads")
async def threads(repositoryId: str, request: Request) -> JSONResponse:
    try:
        session = _session(request)
        values = await _runtime(request).list_threads(repositoryId, session.id)
        return JSONResponse({"threads": list(values)})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/threads")
async def start_thread(payload: ThreadStartRequest, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    session = _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        thread = await runtime.start_thread(
            repository_id=payload.repositoryId,
            session_id=session.id,
            sandbox_mode=payload.sandboxMode,
            approval_policy=payload.approvalPolicy,
            confirmation_id=payload.confirmationId,
            full_access_scope_id=payload.fullAccessScopeId or "",
        )
        return JSONResponse({"thread": thread}, status_code=HTTPStatus.CREATED)
    except Exception as exc:
        return _safe_error(exc)


@router.post("/threads/{thread_id}/resume")
async def resume_thread(
    thread_id: str, payload: ThreadResumeRequest, request: Request
) -> JSONResponse:
    runtime = _runtime(request)
    session = _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        thread = await runtime.resume_thread(
            thread_id=thread_id,
            repository_id=payload.repositoryId,
            session_id=session.id,
            sandbox_mode=payload.sandboxMode,
            approval_policy=payload.approvalPolicy,
            confirmation_id=payload.confirmationId,
        )
        return JSONResponse({"thread": thread})
    except Exception as exc:
        return _safe_error(exc)


@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str, request: Request) -> JSONResponse:
    try:
        thread = await _runtime(request).read_thread(thread_id, _session(request).id)
        return JSONResponse({"thread": thread})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/threads/{thread_id}/turns")
async def start_turn(thread_id: str, payload: TurnStartRequest, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    session = _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        result = await runtime.start_turn(
            thread_id=thread_id,
            session_id=session.id,
            text=payload.message.strip(),
            images=tuple((image.uploadId, image.detail) for image in payload.images),
            sandbox_mode=payload.sandboxMode,
            approval_policy=payload.approvalPolicy,
            confirmation_id=payload.confirmationId,
            idempotency_key=payload.idempotencyKey,
            model=payload.model,
            effort=payload.effort,
        )
        return JSONResponse(result, status_code=HTTPStatus.ACCEPTED)
    except Exception as exc:
        return _safe_error(exc)


@router.post("/threads/{thread_id}/interrupt")
async def interrupt_thread(thread_id: str, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    session = _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        return JSONResponse(await runtime.interrupt_turn(thread_id, session.id))
    except Exception as exc:
        return _safe_error(exc)


@router.post("/permissions/full-access/prepare")
async def prepare_full_access(payload: FullAccessPrepareRequest, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    session = _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        repository, _path = await asyncio.to_thread(
            runtime.repositories.resolve_repository_id, payload.repositoryId
        )
        scope_id = payload.threadId or "new_" + secrets.token_urlsafe(18)
        confirmation = await runtime.full_access.issue(
            session_id=session.id,
            repository_id=repository.id,
            scope_id=scope_id,
            approval_policy=payload.approvalPolicy,
        )
        return JSONResponse(
            {
                "confirmationId": confirmation.id,
                "scopeId": scope_id,
                "expiresAt": confirmation.expires_at_iso,
                "repository": repository.name,
                "sandboxMode": "danger-full-access",
                "approvalPolicy": payload.approvalPolicy,
            }
        )
    except Exception as exc:
        return _safe_error(exc)


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str, payload: ApprovalDecisionRequest, request: Request
) -> JSONResponse:
    runtime = _runtime(request)
    session = _session(request)
    limited = _mutation_allowed(runtime, session.id)
    if limited is not None:
        return limited
    try:
        return JSONResponse(
            await runtime.decide_approval(
                approval_id, session_id=session.id, decision=payload.decision
            )
        )
    except Exception as exc:
        return _safe_error(exc)


@router.get("/usage/rate-limits")
async def rate_limits(request: Request) -> JSONResponse:
    try:
        return JSONResponse(await _runtime(request).read_rate_limits(_session(request).id))
    except Exception as exc:
        return _safe_error(exc)


assert {"read-only", "workspace-write", "danger-full-access"} == SUPPORTED_SANDBOX_MODES
assert {"untrusted", "on-request", "never"} == SUPPORTED_APPROVAL_POLICIES
