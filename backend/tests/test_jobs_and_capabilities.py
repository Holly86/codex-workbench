from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.transport import JsonObject
from codex_workbench.git_service import GitResult, GitService
from codex_workbench.host_capabilities import detect_sandbox_capabilities
from codex_workbench.jobs import BackgroundJobRegistry
from codex_workbench.repositories import RepositoryDiscovery


@pytest.mark.asyncio
async def test_background_job_survives_caller_lifetime_and_is_reconnect_projected() -> None:
    updates: list[JsonObject] = []

    async def updated(_session_id: str, payload: JsonObject) -> None:
        updates.append(payload)

    registry = BackgroundJobRegistry(update_handler=updated)
    release = asyncio.Event()

    async def operation() -> GitResult:
        await release.wait()
        return GitResult("git fetch --prune", 0, "done", 12, False)

    job = await registry.start(
        owner_session_id="session",
        repository_id="0123456789abcdef",
        repository_name="repo",
        job_type="git_fetch",
        operation=operation,
    )
    assert (await registry.list_for_session("session"))[0]["status"] in {"queued", "running"}
    # No subscription/browser object is retained or required by the job.
    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if (await registry.list_for_session("session"))[0]["status"] == "succeeded":
            break
    snapshot = (await registry.list_for_session("session"))[0]
    assert snapshot["id"] == job.id
    assert snapshot["status"] == "succeeded"
    assert snapshot["output"] == "done"
    assert [update["status"] for update in updates] == ["queued", "running", "succeeded"]


@pytest.mark.asyncio
async def test_git_status_projects_branch_counts_and_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    service = GitService(RepositoryDiscovery(root), root, output_limit=4096)
    await service.create("sample", readme=True)
    repository = (await service._repositories.list_repositories())[0]  # noqa: SLF001
    status = await service.status(repository.id)
    assert status["branch"] == "main"
    assert status["untracked"] == 1
    assert status["clean"] is False
    assert status["upstreamMissing"] is True


@pytest.mark.asyncio
async def test_public_github_publish_requires_exact_repository_confirmation(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    service = GitService(RepositoryDiscovery(root), root, output_limit=4096)
    await service.create("sample", readme=False)
    repository = (await service._repositories.list_repositories())[0]  # noqa: SLF001
    with pytest.raises(ValueError, match="confirmation"):
        await service.publish_github(
            repository.id,
            owner="owner",
            name="sample",
            visibility="public",
            remote="origin",
            initial_push=False,
            public_confirmation="wrong",
        )


def test_github_web_url_discards_credentials_and_rejects_non_github() -> None:
    assert GitService.github_web_url("https://user:secret@github.com/owner/repo.git") == (
        "https://github.com/owner/repo"
    )
    assert GitService.github_web_url("ssh://git@example.test/owner/repo.git") is None


@pytest.mark.asyncio
async def test_missing_bwrap_disables_linux_sandboxes_but_never_full_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("codex_workbench.host_capabilities.shutil.which", lambda _name: None)
    capability = await detect_sandbox_capabilities()
    sandbox = capability["sandbox"]
    assert isinstance(sandbox, dict)
    projected = cast(dict[str, dict[str, object]], sandbox)
    assert projected["workspaceWrite"]["available"] is False
    assert projected["readOnly"]["available"] is False
    assert projected["fullAccess"]["available"] is True
