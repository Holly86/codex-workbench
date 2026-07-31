"""Authenticated bounded image upload API."""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from starlette.responses import JSONResponse

from codex_workbench.runtime import RuntimeUnavailableError, WorkbenchRuntime
from codex_workbench.security.http import api_error
from codex_workbench.security.sessions import AuthenticatedSession
from codex_workbench.uploads import UploadUnavailableError, UploadValidationError

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _context(request: Request) -> tuple[WorkbenchRuntime, AuthenticatedSession]:
    runtime = getattr(request.app.state, "runtime", None)
    session = getattr(request.state, "authenticated_session", None)
    if not isinstance(runtime, WorkbenchRuntime) or not isinstance(session, AuthenticatedSession):
        raise RuntimeUnavailableError("upload runtime is unavailable")
    return runtime, session


@router.post("")
async def upload_image(request: Request, file: Annotated[UploadFile, File()]) -> JSONResponse:
    try:
        runtime, session = _context(request)
        decision = runtime.consume_mutation(session.id)
        if not decision.allowed:
            return api_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "operation_rate_limited",
                "Zu viele Uploads. Bitte kurz warten.",
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        record = await runtime.uploads.store(file, session_id=session.id)
        return JSONResponse(
            {
                "upload": {
                    "id": record.id,
                    "displayName": record.display_name,
                    "detectedMime": record.detected_mime,
                    "sizeBytes": record.size_bytes,
                    "bytesReceived": record.bytes_received,
                    "state": record.state,
                    "createdAt": record.created_at,
                    "expiresAt": record.expires_at,
                }
            },
            status_code=HTTPStatus.CREATED,
        )
    except UploadValidationError as exc:
        return api_error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
    except Exception:
        return api_error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "upload_failed",
            "Der Upload konnte nicht verarbeitet werden.",
        )


@router.delete("/{upload_id}")
async def remove_upload(upload_id: str, request: Request) -> JSONResponse:
    try:
        runtime, session = _context(request)
        decision = runtime.consume_mutation(session.id)
        if not decision.allowed:
            return api_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "operation_rate_limited",
                "Zu viele Aktionen. Bitte kurz warten.",
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        await runtime.uploads.remove(upload_id, session_id=session.id)
        return JSONResponse({"uploadId": upload_id, "state": "expired"})
    except UploadUnavailableError:
        return api_error(
            HTTPStatus.NOT_FOUND, "upload_unavailable", "Der Upload ist nicht verfügbar."
        )
    except Exception:
        return api_error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "upload_failed",
            "Der Upload konnte nicht entfernt werden.",
        )
