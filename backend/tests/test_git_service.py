from __future__ import annotations

from pathlib import Path

import pytest

from codex_workbench.git_service import GitResult, GitService
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


def test_clone_url_rejects_embedded_credentials_and_redacts_defensively() -> None:
    value = "https://user:secret@example.test/org/repo.git"
    with pytest.raises(ValueError, match="credentials"):
        GitService.validate_clone_url(value)
    assert GitService.redact_url(value) == "https://example.test/org/repo.git"


@pytest.mark.asyncio
async def test_file_arguments_are_repository_relative(tmp_path: Path) -> None:
    service, _root = _service(tmp_path)
    await service.create("sample", readme=False)
    repository = (await service._repositories.list_repositories())[0]  # noqa: SLF001
    with pytest.raises(ValueError):
        await service.run(repository.id, "add", files=("../../outside",))


@pytest.mark.asyncio
async def test_clone_preflight_accepts_reachable_https_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _root = _service(tmp_path)

    async def reachable(*_args: object, **_kwargs: object) -> GitResult:
        return GitResult("git ls-remote https://github.com/owner/repo.git", 0, "", 2, False)

    monkeypatch.setattr(service, "_execute", reachable)
    result = await service.clone_preflight("https://github.com/owner/repo.git")

    assert result == {"allowed": True, "mode": "public_https", "message": "Zugriff geprüft."}


def test_native_environment_allows_configured_agents_but_not_token_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/agent.sock")
    monkeypatch.setenv("GH_TOKEN", "must-not-propagate")

    environment = GitService._native_env(optional_locks=False)  # noqa: SLF001

    assert environment["SSH_AUTH_SOCK"] == "/run/user/agent.sock"
    assert "GH_TOKEN" not in environment
