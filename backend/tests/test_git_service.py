from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.deploy_keys import DeployKeyService
from codex_workbench.git_service import (
    BranchLocalNameConflictError,
    BranchTargetUnavailableError,
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


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _configure_identity(repository: Path) -> None:
    _git(repository, "config", "user.name", "Workbench Test")
    _git(repository, "config", "user.email", "workbench@example.test")


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
async def test_status_preserves_special_paths_and_git_add_uses_exact_path(
    tmp_path: Path,
) -> None:
    service, root = _service(tmp_path)
    repository_path = root / "special-paths"
    _git(root, "init", "--initial-branch=main", str(repository_path))
    _configure_identity(repository_path)
    tracked = ("space name.txt", "unicodé.txt", "both.txt", "delete.txt")
    for name in tracked:
        (repository_path / name).write_text("base\n", encoding="utf-8")
    _git(repository_path, "add", "--", *tracked)
    _git(repository_path, "commit", "-m", "base")

    (repository_path / "space name.txt").write_text("unstaged\n", encoding="utf-8")
    (repository_path / "unicodé.txt").write_text("staged\n", encoding="utf-8")
    _git(repository_path, "add", "--", "unicodé.txt")
    (repository_path / "both.txt").write_text("staged\n", encoding="utf-8")
    _git(repository_path, "add", "--", "both.txt")
    (repository_path / "both.txt").write_text("both\n", encoding="utf-8")
    (repository_path / "delete.txt").unlink()
    (repository_path / "-leading.txt").write_text("new\n", encoding="utf-8")
    if os.name != "nt":
        (repository_path / "tab\tname.txt").write_text("tab\n", encoding="utf-8")

    repository_id = GitService.repository_id_for_name("special-paths")
    status = await service.status(repository_id)
    changes = {str(item["path"]): item for item in cast(list[dict[str, object]], status["changes"])}

    expected_paths = {*tracked, "-leading.txt"}
    if os.name != "nt":
        expected_paths.add("tab\tname.txt")
    assert set(changes) == expected_paths
    assert changes["space name.txt"] == {
        "path": "space name.txt", "staged": False, "unstaged": True, "kind": "modified",
    }
    assert changes["unicodé.txt"]["staged"] is True
    assert changes["both.txt"]["staged"] is True
    assert changes["both.txt"]["unstaged"] is True
    if os.name != "nt":
        assert changes["tab\tname.txt"]["kind"] == "untracked"
    assert changes["delete.txt"]["kind"] == "deleted"
    assert changes["-leading.txt"]["kind"] == "untracked"
    assert status["staged"] == 2
    assert status["modified"] == 3
    assert status["untracked"] == (1 if os.name == "nt" else 2)

    added = await service.run(repository_id, "add", files=("-leading.txt",))
    assert added.exit_code == 0
    after_add = await service.status(repository_id)
    leading = next(
        item
        for item in cast(list[dict[str, object]], after_add["changes"])
        if item["path"] == "-leading.txt"
    )
    assert leading["staged"] is True


@pytest.mark.asyncio
async def test_status_projects_conflict_path_without_whitespace_parsing(tmp_path: Path) -> None:
    service, root = _service(tmp_path)
    repository_path = root / "conflict"
    _git(root, "init", "--initial-branch=main", str(repository_path))
    _configure_identity(repository_path)
    path = "conflict name.txt"
    (repository_path / path).write_text("base\n", encoding="utf-8")
    _git(repository_path, "add", "--", path)
    _git(repository_path, "commit", "-m", "base")
    _git(repository_path, "switch", "-c", "other")
    (repository_path / path).write_text("other\n", encoding="utf-8")
    _git(repository_path, "commit", "-am", "other")
    _git(repository_path, "switch", "main")
    (repository_path / path).write_text("main\n", encoding="utf-8")
    _git(repository_path, "commit", "-am", "main")
    _git(repository_path, "merge", "other", check=False)

    status = await service.status(GitService.repository_id_for_name("conflict"))
    assert status["conflicts"] == 1
    assert status["clean"] is False
    assert cast(list[dict[str, object]], status["changes"])[0]["path"] == path
    assert cast(list[dict[str, object]], status["changes"])[0]["kind"] == "conflict"


@pytest.mark.asyncio
async def test_remote_targets_create_explicit_tracking_branches_from_fresh_inventory(
    tmp_path: Path,
) -> None:
    service, root = _service(tmp_path)
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "--bare", str(upstream))
    _git(tmp_path, "init", "--initial-branch=main", str(seed))
    _configure_identity(seed)
    (seed / "README.md").write_text("main\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "main")
    _git(seed, "switch", "-c", "release")
    (seed / "release.txt").write_text("release\n", encoding="utf-8")
    _git(seed, "add", "release.txt")
    _git(seed, "commit", "-m", "release")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "remote", "add", "upstream", str(upstream))
    _git(seed, "push", "origin", "main", "release")
    _git(seed, "push", "upstream", "release")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    clone = root / "clone"
    _git(root, "clone", str(origin), str(clone))
    _git(clone, "remote", "add", "upstream", str(upstream))
    _git(clone, "fetch", "upstream")
    repository_id = GitService.repository_id_for_name("clone")
    inventory = await service.branches(repository_id)
    remote = cast(list[dict[str, object]], inventory["remote"])
    origin_target = next(item for item in remote if item["id"] == "refs/remotes/origin/release")
    upstream_target = next(
        item for item in remote if item["id"] == "refs/remotes/upstream/release"
    )
    assert origin_target["remoteName"] == "origin"
    assert origin_target["remoteBranchName"] == "release"
    assert origin_target["proposedLocalName"] == "release"
    assert upstream_target["remoteName"] == "upstream"

    switched = await service.switch_branch(repository_id, str(origin_target["id"]))
    assert switched.exit_code == 0
    assert _git(clone, "branch", "--show-current") == "release"
    assert _git(clone, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/release"

    with pytest.raises(BranchTargetUnavailableError):
        await service.switch_branch(repository_id, "refs/heads/release")
    switched_local = await service.switch_branch(repository_id, "refs/heads/main")
    assert switched_local.exit_code == 0
    assert _git(clone, "branch", "--show-current") == "main"
    with pytest.raises(BranchLocalNameConflictError):
        await service.switch_branch(repository_id, str(origin_target["id"]))

    switched_upstream = await service.switch_branch(
        repository_id,
        str(upstream_target["id"]),
        local_branch_name="upstream-release",
    )
    assert switched_upstream.exit_code == 0
    assert _git(clone, "rev-parse", "--abbrev-ref", "@{upstream}") == "upstream/release"

    _git(clone, "switch", "main")
    _git(clone, "update-ref", "-d", "refs/remotes/upstream/release")
    with pytest.raises(BranchTargetUnavailableError):
        await service.switch_branch(repository_id, str(upstream_target["id"]))
    with pytest.raises(BranchTargetUnavailableError):
        await service.switch_branch(repository_id, "refs/remotes/origin/invented")


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
    monkeypatch.setenv("LD_PRELOAD", "/usr/local/lib/libnss_wrapper.so")
    monkeypatch.setenv("NSS_WRAPPER_PASSWD", "/tmp/workbench-nss/passwd")
    monkeypatch.setenv("NSS_WRAPPER_GROUP", "/tmp/workbench-nss/group")
    monkeypatch.setenv("GH_TOKEN", "must-not-propagate")

    environment = GitService._native_env(optional_locks=False)  # noqa: SLF001

    assert environment["SSH_AUTH_SOCK"] == "/run/user/agent.sock"
    assert environment["LD_PRELOAD"] == "/usr/local/lib/libnss_wrapper.so"
    assert environment["NSS_WRAPPER_PASSWD"] == "/tmp/workbench-nss/passwd"
    assert environment["NSS_WRAPPER_GROUP"] == "/tmp/workbench-nss/group"
    assert "GH_TOKEN" not in environment
