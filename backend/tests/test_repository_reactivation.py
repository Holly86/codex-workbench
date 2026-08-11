from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from codex_workbench.deploy_keys import DeployKeyService
from codex_workbench.git_service import (
    GitOperationError,
    GitOperationTimeoutError,
    GitResult,
    GitService,
)
from codex_workbench.persistence.database import Database
from codex_workbench.persistence.metadata import MetadataRepository
from codex_workbench.runtime import WorkbenchRuntime
from tests.helpers import build_settings


def _metadata(tmp_path: Path) -> MetadataRepository:
    database = Database(tmp_path / "metadata.sqlite3")
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE repository_registrations (
                repository_id TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('removed', 'deleted')),
                updated_at INTEGER NOT NULL
            )
            """
        )
    return MetadataRepository(database)


def _runtime(tmp_path: Path) -> tuple[WorkbenchRuntime, MetadataRepository]:
    metadata = _metadata(tmp_path)
    return WorkbenchRuntime(build_settings(tmp_path), metadata), metadata


def _tombstone(metadata: MetadataRepository, name: str, state: str) -> str:
    repository_id = GitService.repository_id_for_name(name)
    metadata.mark_repository_unavailable(
        repository_id,
        name,
        state=state,
        updated_at=1,
    )
    return repository_id


def _git_result(name: str, *, exit_code: int = 0) -> GitResult:
    return GitResult(f"git clone -- repository {name}", exit_code, "", 1, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["removed", "deleted"])
async def test_absent_tombstoned_repository_remains_hidden(
    tmp_path: Path, state: str
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", state)

    assert await runtime.list_repositories() == ()
    assert metadata.hidden_repository_ids() == frozenset({repository_id})
    assert metadata.repository_registration_state(repository_id) == state


@pytest.mark.asyncio
async def test_historical_tombstone_hides_recreated_physical_repository_until_reactivated(
    tmp_path: Path,
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", "deleted")
    (runtime.settings.repositories_root / "sample" / ".git").mkdir(parents=True)

    assert await runtime.repositories.list_repositories()
    assert await runtime.list_repositories() == ()
    assert metadata.hidden_repository_ids() == frozenset({repository_id})


@pytest.mark.asyncio
async def test_successful_recreate_clears_only_its_tombstone_and_survives_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, metadata = _runtime(tmp_path)
    recreated_id = _tombstone(metadata, "sample", "removed")
    unrelated_id = _tombstone(metadata, "unrelated", "deleted")

    async def create(name: str, *, readme: bool) -> GitResult:
        assert readme is True
        (runtime.settings.repositories_root / name / ".git").mkdir(parents=True)
        return _git_result(name)

    monkeypatch.setattr(runtime.git, "create", create)
    result = await runtime.create_repository("sample", readme=True)

    assert result.exit_code == 0
    assert metadata.repository_registration_state(recreated_id) is None
    assert metadata.repository_registration_state(unrelated_id) == "deleted"
    assert [item.id for item in await runtime.list_repositories()] == [recreated_id]

    reloaded = WorkbenchRuntime(runtime.settings, metadata)
    assert [item.id for item in await reloaded.list_repositories()] == [recreated_id]


@pytest.mark.asyncio
async def test_successful_normal_clone_after_remove_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", "removed")

    async def clone(name: str, _url: str) -> GitResult:
        (runtime.settings.repositories_root / name / ".git").mkdir(parents=True)
        return _git_result(name)

    monkeypatch.setattr(runtime.git, "clone", clone)
    result = await runtime.clone_repository("sample", "https://github.com/owner/repo.git")

    assert result.exit_code == 0
    assert metadata.repository_registration_state(repository_id) is None
    assert [item.id for item in await runtime.list_repositories()] == [repository_id]


class _DeployKeys:
    def __init__(self) -> None:
        self.bound: list[tuple[str, str]] = []

    async def clone_environment(self, credential_id: str) -> tuple[str, dict[str, str]]:
        assert credential_id == "deploy_test"
        return "git@github.com:owner/private.git", {"PATH": "/usr/bin:/bin"}

    async def bind_after_clone(self, credential_id: str, repository_id: str) -> None:
        self.bound.append((credential_id, repository_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["removed", "deleted"])
async def test_successful_private_clone_reactivates_and_binds_deploy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", state)
    deploy_keys = _DeployKeys()
    runtime.git._deploy_keys = cast(DeployKeyService, deploy_keys)  # noqa: SLF001

    async def execute(
        _cwd: Path, _args: tuple[str, ...], **_kwargs: object
    ) -> GitResult:
        (runtime.settings.repositories_root / "sample" / ".git").mkdir()
        return _git_result("sample")

    monkeypatch.setattr(runtime.git, "_execute", execute)
    reservation = runtime.git.reserve_clone_target("sample")
    result = await runtime.clone_private_repository(reservation, "deploy_test")

    assert result.exit_code == 0
    assert deploy_keys.bound == [("deploy_test", repository_id)]
    assert metadata.repository_registration_state(repository_id) is None
    assert [item.id for item in await runtime.list_repositories()] == [repository_id]


def test_mismatched_relative_path_cannot_clear_tombstone(tmp_path: Path) -> None:
    _runtime_instance, metadata = _runtime(tmp_path)
    repository_id = GitService.repository_id_for_name("sample")
    metadata.mark_repository_unavailable(
        repository_id,
        "different",
        state="removed",
        updated_at=1,
    )

    with pytest.raises(ValueError, match="does not match"):
        metadata.mark_repository_available(repository_id, "sample")

    assert metadata.repository_registration_state(repository_id) == "removed"


@pytest.mark.asyncio
async def test_symlink_escape_cannot_reactivate_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", "removed")
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    try:
        (runtime.settings.repositories_root / "sample").symlink_to(
            outside, target_is_directory=True
        )
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    async def clone(_name: str, _url: str) -> GitResult:
        return _git_result("sample")

    monkeypatch.setattr(runtime.git, "clone", clone)
    with pytest.raises(GitOperationError, match="validation failed"):
        await runtime.clone_repository("sample", "https://github.com/owner/repo.git")

    assert metadata.repository_registration_state(repository_id) == "removed"


@pytest.mark.asyncio
async def test_failed_clone_does_not_clear_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", "removed")

    async def clone(_name: str, _url: str) -> GitResult:
        return _git_result("sample", exit_code=128)

    monkeypatch.setattr(runtime.git, "clone", clone)
    result = await runtime.clone_repository("sample", "https://github.com/owner/repo.git")

    assert result.exit_code == 128
    assert metadata.repository_registration_state(repository_id) == "removed"


@pytest.mark.asyncio
async def test_private_clone_timeout_cleans_target_without_clearing_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, metadata = _runtime(tmp_path)
    repository_id = _tombstone(metadata, "sample", "removed")
    deploy_keys = _DeployKeys()
    runtime.git._deploy_keys = cast(DeployKeyService, deploy_keys)  # noqa: SLF001

    async def execute(
        _cwd: Path, _args: tuple[str, ...], **_kwargs: object
    ) -> GitResult:
        (runtime.settings.repositories_root / "sample" / "partial").write_text(
            "partial", encoding="utf-8"
        )
        raise GitOperationTimeoutError("sensitive")

    monkeypatch.setattr(runtime.git, "_execute", execute)
    reservation = runtime.git.reserve_clone_target("sample")
    with pytest.raises(GitOperationTimeoutError):
        await runtime.clone_private_repository(reservation, "deploy_test")

    assert not (runtime.settings.repositories_root / "sample").exists()
    assert deploy_keys.bound == []
    assert metadata.repository_registration_state(repository_id) == "removed"
