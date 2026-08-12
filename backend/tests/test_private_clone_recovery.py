from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

import codex_workbench.api.workbench as workbench_api
from codex_workbench.deploy_keys import DeployKeyAlreadyBoundError
from codex_workbench.git_service import (
    BranchLocalNameConflictError,
    BranchTargetUnavailableError,
    GitOperationTimeoutError,
    RepositoryNameConflictError,
)
from codex_workbench.runtime import WorkbenchRuntime
from codex_workbench.security.sessions import AuthenticatedSession


@pytest.mark.asyncio
async def test_existing_private_clone_target_returns_conflict_without_starting_a_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConflictingGit:
        @staticmethod
        def reserve_clone_target(_name: str) -> None:
            raise RepositoryNameConflictError("sensitive absolute path")

    class Jobs:
        started = False

        async def start(self, **_kwargs: object) -> None:
            self.started = True

    jobs = Jobs()
    runtime = cast(
        WorkbenchRuntime,
        SimpleNamespace(git=ConflictingGit(), jobs=jobs),
    )
    session = cast(AuthenticatedSession, SimpleNamespace(id="session"))
    monkeypatch.setattr(workbench_api, "_runtime", lambda _request: runtime)
    monkeypatch.setattr(workbench_api, "_session", lambda _request: session)
    monkeypatch.setattr(workbench_api, "_mutation_allowed", lambda *_args: None)
    payload = workbench_api.PrivateRepositoryCloneRequest(
        name="existing",
        credentialId="deploy_" + "a" * 32,
        readme=False,
    )

    response = await workbench_api.clone_private_repository(
        payload, cast(Request, object())
    )
    body = json.loads(bytes(response.body))

    assert response.status_code == 409
    assert body == {
        "error": {
            "code": "repository_name_conflict",
            "message": "Ein Repository mit diesem lokalen Namen existiert bereits.",
        }
    }
    assert jobs.started is False
    assert "path" not in bytes(response.body).decode("utf-8")


def test_bound_deploy_key_has_a_stable_browser_safe_conflict() -> None:
    response = workbench_api._safe_error(  # noqa: SLF001
        DeployKeyAlreadyBoundError("sensitive credential and path")
    )

    assert response.status_code == 409
    assert json.loads(bytes(response.body)) == {
        "error": {
            "code": "deploy_key_already_bound",
            "message": "Der Deploy Key ist bereits an ein lokales Repository gebunden.",
        }
    }
    assert "sensitive" not in bytes(response.body).decode("utf-8")


def test_git_timeout_has_a_stable_browser_safe_error() -> None:
    response = workbench_api._safe_error(  # noqa: SLF001
        GitOperationTimeoutError("sensitive URL, path, and credential")
    )

    assert response.status_code == 504
    assert json.loads(bytes(response.body)) == {
        "error": {
            "code": "git_operation_timeout",
            "message": "Der Git-Vorgang hat das zulässige Zeitlimit überschritten.",
        }
    }
    assert "sensitive" not in bytes(response.body).decode("utf-8")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (BranchLocalNameConflictError("sensitive branch details"), "branch_local_name_conflict"),
        (BranchTargetUnavailableError("sensitive ref details"), "branch_target_unavailable"),
    ],
)
def test_branch_switch_conflicts_have_stable_browser_safe_codes(
    error: Exception, code: str
) -> None:
    response = workbench_api._safe_error(error)  # noqa: SLF001
    body = json.loads(bytes(response.body))

    assert response.status_code == 409
    assert body["error"]["code"] == code
    assert "sensitive" not in bytes(response.body).decode("utf-8")
