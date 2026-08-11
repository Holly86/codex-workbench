from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.deploy_keys import DeployKeyService
from codex_workbench.git_service import (
    GitOperationTimeoutError,
    GitResult,
    GitService,
    RepositoryNameConflictError,
)
from codex_workbench.repositories import RepositoryDiscovery


def _service(tmp_path: Path) -> tuple[GitService, Path]:
    root = tmp_path / "repositories"
    root.mkdir()
    discovery = RepositoryDiscovery(root)
    return GitService(discovery, root, output_limit=4096), root


class FakeDeployKeys:
    def __init__(self) -> None:
        self.bound: list[tuple[str, str]] = []
        self.clone_environment_calls = 0

    async def clone_environment(self, credential_id: str) -> tuple[str, dict[str, str]]:
        assert credential_id == "deploy_test_credential"
        self.clone_environment_calls += 1
        return "git@github.com:owner/private.git", {"PATH": "/usr/bin:/bin"}

    async def bind_after_clone(self, credential_id: str, repository_id: str) -> None:
        self.bound.append((credential_id, repository_id))


def _private_clone_service(tmp_path: Path) -> tuple[GitService, FakeDeployKeys, Path]:
    root = tmp_path / "repositories"
    root.mkdir()
    deploy_keys = FakeDeployKeys()
    service = GitService(
        RepositoryDiscovery(root),
        root,
        output_limit=4096,
        deploy_keys=cast(DeployKeyService, deploy_keys),
    )
    return service, deploy_keys, root


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


def test_clone_target_conflict_is_synchronous_and_never_removes_preexisting_target(
    tmp_path: Path,
) -> None:
    service, _root = _service(tmp_path)
    existing = tmp_path / "repositories" / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(RepositoryNameConflictError):
        service.reserve_clone_target("existing")

    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_failed_private_clone_cleans_only_its_reserved_target_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, deploy_keys, root = _private_clone_service(tmp_path)
    attempts = 0

    async def execute(
        _cwd: Path, _args: tuple[str, ...], **_kwargs: object
    ) -> GitResult:
        nonlocal attempts
        attempts += 1
        target = root / ("first" if attempts == 1 else "second")
        if attempts == 1:
            (target / "partial").write_text("partial", encoding="utf-8")
            return GitResult("git clone -- github.com:repository first", 128, "", 2, False)
        (target / ".git").mkdir()
        return GitResult("git clone -- github.com:repository second", 0, "", 3, False)

    monkeypatch.setattr(service, "_execute", execute)
    first = service.reserve_clone_target("first")
    failed = await service.clone_with_deploy_key(first, "deploy_test_credential")

    assert failed.exit_code == 128
    assert not (root / "first").exists()
    assert deploy_keys.bound == []

    second = service.reserve_clone_target("second")
    succeeded = await service.clone_with_deploy_key(second, "deploy_test_credential")

    assert succeeded.exit_code == 0
    assert (root / "second" / ".git").is_dir()
    assert deploy_keys.bound == [
        ("deploy_test_credential", GitService.repository_id_for_name("second"))
    ]


@pytest.mark.asyncio
async def test_private_clone_timeout_cleans_reservation_and_allows_credential_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, deploy_keys, root = _private_clone_service(tmp_path)
    timeouts: list[float] = []

    async def execute(
        _cwd: Path, _args: tuple[str, ...], **kwargs: object
    ) -> GitResult:
        timeouts.append(cast(float, kwargs["timeout_seconds"]))
        target = root / ("timed-out" if len(timeouts) == 1 else "retry")
        if len(timeouts) == 1:
            (target / "partial").write_text("partial", encoding="utf-8")
            raise GitOperationTimeoutError("sensitive remote and credential")
        (target / ".git").mkdir()
        return GitResult("git clone -- github.com:repository retry", 0, "", 3, False)

    monkeypatch.setattr(service, "_execute", execute)
    first = service.reserve_clone_target("timed-out")
    with pytest.raises(GitOperationTimeoutError):
        await service.clone_with_deploy_key(first, "deploy_test_credential")

    assert not (root / "timed-out").exists()
    assert deploy_keys.bound == []

    retried = service.reserve_clone_target("retry")
    result = await service.clone_with_deploy_key(retried, "deploy_test_credential")

    assert result.exit_code == 0
    assert timeouts == [900.0, 900.0]
    assert deploy_keys.clone_environment_calls == 2
    assert deploy_keys.bound == [
        ("deploy_test_credential", GitService.repository_id_for_name("retry"))
    ]


def test_replaced_reserved_target_is_not_removed(tmp_path: Path) -> None:
    service, root = _service(tmp_path)
    reservation = service.reserve_clone_target("reserved")
    original = root / "original-reservation"
    reservation.target.rename(original)
    reservation.target.mkdir()
    marker = reservation.target / "preexisting.txt"
    marker.write_text("do not remove", encoding="utf-8")

    with pytest.raises(Exception, match="reservation is unsafe"):
        service.release_clone_target(reservation)

    assert marker.read_text(encoding="utf-8") == "do not remove"


def test_missing_reserved_target_is_released_for_retry(tmp_path: Path) -> None:
    service, root = _service(tmp_path)
    reservation = service.reserve_clone_target("retry")
    reservation.target.rmdir()

    service.release_clone_target(reservation)
    retried = service.reserve_clone_target("retry")

    assert retried.target == root / "retry"
    service.release_clone_target(retried)


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


def _repository_for_timeout_tests(root: Path) -> str:
    target = root / "sample"
    target.mkdir()
    (target / ".git").mkdir()
    return GitService.repository_id_for_name("sample")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_timeout"),
    [("status", 11.0), ("fetch", 22.0), ("pull", 22.0), ("push", 22.0)],
)
async def test_git_actions_use_semantic_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expected_timeout: float,
) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    service = GitService(
        RepositoryDiscovery(root),
        root,
        output_limit=4096,
        command_timeout_seconds=11,
        network_timeout_seconds=22,
        clone_timeout_seconds=33,
    )
    repository_id = _repository_for_timeout_tests(root)
    observed: list[float] = []

    async def execute(
        _cwd: Path, args: tuple[str, ...], **kwargs: object
    ) -> GitResult:
        observed.append(cast(float, kwargs["timeout_seconds"]))
        return GitResult("git " + " ".join(args), 0, "", 1, False)

    monkeypatch.setattr(service, "_execute", execute)
    await service.run(repository_id, action)

    assert observed == [expected_timeout]


@pytest.mark.asyncio
async def test_https_clone_and_probe_use_clone_and_command_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    service = GitService(
        RepositoryDiscovery(root),
        root,
        output_limit=4096,
        command_timeout_seconds=11,
        network_timeout_seconds=22,
        clone_timeout_seconds=33,
    )
    observed: list[tuple[str, float]] = []

    async def execute(
        _cwd: Path, args: tuple[str, ...], **kwargs: object
    ) -> GitResult:
        observed.append((args[0], cast(float, kwargs["timeout_seconds"])))
        return GitResult("git " + " ".join(args), 0, "", 1, False)

    monkeypatch.setattr(service, "_execute", execute)
    result = await service.clone("sample", "https://github.com/owner/repo.git")

    assert result.exit_code == 0
    assert observed == [("ls-remote", 11.0), ("clone", 33.0)]


@pytest.mark.asyncio
async def test_github_cli_clone_uses_clone_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    service = GitService(
        RepositoryDiscovery(root),
        root,
        output_limit=4096,
        command_timeout_seconds=11,
        network_timeout_seconds=22,
        clone_timeout_seconds=33,
    )
    observed: list[float] = []

    async def preflight(_url: str) -> dict[str, object]:
        return {"allowed": True, "mode": "github_cli"}

    async def execute_external(
        args: tuple[str, ...], **kwargs: object
    ) -> GitResult:
        observed.append(cast(float, kwargs["timeout_seconds"]))
        return GitResult("gh " + " ".join(args[1:]), 0, "", 1, False)

    monkeypatch.setattr(service, "clone_preflight", preflight)
    monkeypatch.setattr(service, "_execute_external", execute_external)
    monkeypatch.setattr("codex_workbench.git_service.shutil.which", lambda _name: "gh")
    result = await service.clone("sample", "https://github.com/owner/private.git")

    assert result.exit_code == 0
    assert observed == [33.0]


@pytest.mark.asyncio
async def test_execute_timeout_raises_specific_error_without_long_wait(
    tmp_path: Path,
) -> None:
    service, _root = _service(tmp_path)

    with pytest.raises(GitOperationTimeoutError):
        await service._execute_external(  # noqa: SLF001
            (sys.executable, "-c", "import time; time.sleep(60)"),
            timeout_seconds=0.01,
        )


def test_posix_timeout_signals_only_the_owned_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX process groups are not available on Windows")
    service, _root = _service(tmp_path)
    signals: list[tuple[int, signal.Signals]] = []

    class Process:
        pid = 424242

    monkeypatch.setattr(
        "codex_workbench.git_service.os.kill",
        lambda pid, selected_signal: signals.append((pid, selected_signal)),
    )
    process = cast(asyncio.subprocess.Process, Process())

    service._signal_process(process, hard=False)  # noqa: SLF001
    service._signal_process(process, hard=True)  # noqa: SLF001

    assert signals == [(-424242, signal.SIGTERM), (-424242, 9)]


def test_native_environment_allows_configured_agents_but_not_token_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/agent.sock")
    monkeypatch.setenv("GH_TOKEN", "must-not-propagate")

    environment = GitService._native_env(optional_locks=False)  # noqa: SLF001

    assert environment["SSH_AUTH_SOCK"] == "/run/user/agent.sock"
    assert "GH_TOKEN" not in environment
