from __future__ import annotations

from pathlib import Path

import pytest

from codex_workbench.git_service import GitService
from codex_workbench.repositories import RepositoryDiscovery


def _service(tmp_path: Path) -> tuple[GitService, Path]:
    root = tmp_path / "repositories"
    root.mkdir()
    discovery = RepositoryDiscovery(root)
    return GitService(discovery, root, output_limit=4096), root


@pytest.mark.asyncio
async def test_create_and_status_use_native_git_without_codex(tmp_path: Path) -> None:
    service, root = _service(tmp_path)
    created = await service.create("sample", readme=True)
    assert created.exit_code == 0
    repositories = await service._repositories.list_repositories()  # noqa: SLF001
    status = await service.run(repositories[0].id, "status")
    assert status.command.startswith("git status")
    assert "README.md" in status.output
    assert (root / "sample" / "README.md").is_file()


@pytest.mark.parametrize("name", ["../escape", "-option", "bad/name", ".git", "repo.git"])
def test_repository_names_reject_traversal_and_injection(name: str) -> None:
    with pytest.raises(ValueError):
        GitService.validate_name(name)


@pytest.mark.parametrize(
    "url", ["file:///tmp/repo", "/tmp/repo", "../repo", "-u", "http://host/repo"]
)
def test_clone_urls_reject_local_and_unsupported_values(url: str) -> None:
    with pytest.raises(ValueError):
        GitService.validate_clone_url(url)


def test_clone_url_redacts_credentials() -> None:
    value = GitService.validate_clone_url("https://user:secret@example.test/org/repo.git")
    assert GitService.redact_url(value) == "https://example.test/org/repo.git"


@pytest.mark.asyncio
async def test_file_arguments_are_repository_relative(tmp_path: Path) -> None:
    service, _root = _service(tmp_path)
    await service.create("sample", readme=False)
    repository = (await service._repositories.list_repositories())[0]  # noqa: SLF001
    with pytest.raises(ValueError):
        await service.run(repository.id, "add", files=("../../outside",))
