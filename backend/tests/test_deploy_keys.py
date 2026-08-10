from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.deploy_keys import DeployKeyService, normalize_github_repository
from codex_workbench.persistence.metadata import DeployKeyRecord, MetadataRepository
from tests.helpers import build_settings


class MemoryDeployKeyMetadata:
    def __init__(self) -> None:
        self.records: dict[str, DeployKeyRecord] = {}

    def create_deploy_key(self, record: DeployKeyRecord) -> None:
        if any(value.github_slug == record.github_slug for value in self.records.values()):
            raise ValueError("duplicate slug")
        if record.repository_id is not None and any(
            value.repository_id == record.repository_id for value in self.records.values()
        ):
            raise ValueError("duplicate repository")
        self.records[record.credential_id] = record

    def deploy_key_by_credential(self, credential_id: str) -> DeployKeyRecord | None:
        return self.records.get(credential_id)

    def deploy_key_by_repository(self, repository_id: str) -> DeployKeyRecord | None:
        return next(
            (value for value in self.records.values() if value.repository_id == repository_id),
            None,
        )

    def deploy_key_by_slug(self, github_slug: str) -> DeployKeyRecord | None:
        return next(
            (value for value in self.records.values() if value.github_slug == github_slug),
            None,
        )

    def bind_deploy_key(self, credential_id: str, repository_id: str, *, updated_at: int) -> None:
        current = self.records[credential_id]
        if current.repository_id is not None or self.deploy_key_by_repository(repository_id):
            raise ValueError("cannot bind")
        self.records[credential_id] = replace(
            current, repository_id=repository_id, updated_at=updated_at
        )

    def update_deploy_key_test(
        self,
        credential_id: str,
        *,
        key_kind: str,
        succeeded: bool,
        tested_at: int,
    ) -> None:
        current = self.records[credential_id]
        self.records[credential_id] = replace(
            current,
            last_tested_at=tested_at,
            last_success_at=tested_at if succeeded else current.last_success_at,
            last_test_succeeded=succeeded,
            last_test_key=key_kind,
            updated_at=tested_at,
        )

    def begin_deploy_key_rotation(self, credential_id: str, *, created_at: int) -> None:
        current = self.records[credential_id]
        if current.candidate_created_at is not None:
            raise ValueError("already pending")
        self.records[credential_id] = replace(
            current,
            candidate_created_at=created_at,
            last_tested_at=None,
            last_test_succeeded=None,
            last_test_key=None,
            updated_at=created_at,
        )

    def promote_deploy_key_candidate(self, credential_id: str, *, promoted_at: int) -> None:
        current = self.records[credential_id]
        if current.last_test_key != "candidate" or current.last_test_succeeded is not True:
            raise ValueError("not verified")
        self.records[credential_id] = replace(
            current,
            active_created_at=cast(int, current.candidate_created_at),
            candidate_created_at=None,
            retired_created_at=current.active_created_at,
            last_tested_at=promoted_at,
            last_success_at=promoted_at,
            last_test_succeeded=True,
            last_test_key="active",
            updated_at=promoted_at,
        )

    def clear_deploy_key_retired(self, credential_id: str, *, updated_at: int) -> None:
        self.records[credential_id] = replace(
            self.records[credential_id], retired_created_at=None, updated_at=updated_at
        )

    def delete_deploy_key(self, credential_id: str) -> None:
        self.records.pop(credential_id, None)


async def service(tmp_path: Path) -> tuple[DeployKeyService, MemoryDeployKeyMetadata, Path]:
    settings = build_settings(tmp_path)
    metadata = MemoryDeployKeyMetadata()
    deploy_keys = DeployKeyService(settings, cast(MetadataRepository, metadata))
    await deploy_keys.initialize()
    assert settings.deploy_keys_dir is not None
    return deploy_keys, metadata, settings.deploy_keys_dir


@pytest.mark.parametrize(
    ("value", "slug"),
    [
        ("OWNER/Repo", "owner/repo"),
        ("https://github.com/OWNER/Repo.git", "owner/repo"),
        ("git@github.com:OWNER/Repo.git", "owner/repo"),
        ("ssh://git@github.com/OWNER/Repo.git", "owner/repo"),
    ],
)
def test_github_repository_normalization(value: str, slug: str) -> None:
    repository = normalize_github_repository(value)
    assert repository.slug == slug
    assert repository.ssh_url == f"git@github.com:{slug}.git"


@pytest.mark.parametrize(
    "value",
    [
        "https://token@github.com/owner/repo",
        "https://user:secret@github.com/owner/repo",
        "git@example.com:owner/repo.git",
        "../../owner/repo",
        "owner/repo/extra",
        "ssh://root@github.com/owner/repo",
    ],
)
def test_github_repository_normalization_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_github_repository(value)


@pytest.mark.asyncio
async def test_private_key_stays_outside_workspace_and_browser_projection(
    tmp_path: Path,
) -> None:
    deploy_keys, metadata, root = await service(tmp_path)
    view = await deploy_keys.create("owner/private", repository_id="a" * 16)

    record = next(iter(metadata.records.values()))
    slot = root / sha256(record.credential_id.encode("ascii")).hexdigest()
    private_key = slot / "active"
    public_key = slot / "active.pub"
    serialized = json.dumps(view)

    assert not private_key.is_relative_to(tmp_path / "workspace")
    assert "PRIVATE KEY" not in serialized
    assert str(private_key) not in serialized
    assert view["active"] == {
        "publicKey": public_key.read_text(encoding="ascii").strip(),
        "fingerprint": cast(dict[str, object], view["active"])["fingerprint"],
        "createdAt": record.active_created_at,
    }
    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(slot.stat().st_mode) == 0o700
        assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
        assert stat.S_IMODE(public_key.stat().st_mode) == 0o644


@pytest.mark.asyncio
async def test_connection_probe_uses_exact_bound_identity_without_agent_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy_keys, metadata, root = await service(tmp_path)
    view = await deploy_keys.create("owner/private", repository_id="b" * 16)
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def wait(self) -> None:
            return None

        def kill(self) -> None:
            raise AssertionError("successful probe must not be killed")

    async def fake_create_subprocess_exec(*args: str, **kwargs: object) -> Process:
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    result = await deploy_keys.test_access(cast(str, view["credentialId"]))
    environment = cast(dict[str, str], captured["env"])
    record = next(iter(metadata.records.values()))
    expected_slot = root / sha256(
        record.credential_id.encode("ascii")
    ).hexdigest()

    assert result["succeeded"] is True
    assert captured["args"] == (
        "git",
        "ls-remote",
        "--",
        "git@github.com:owner/private.git",
    )
    assert environment["WORKBENCH_SSH_IDENTITY"] == str((expected_slot / "active").resolve())
    assert environment["GIT_SSH_VARIANT"] == "ssh"
    assert "SSH_AUTH_SOCK" not in environment


@pytest.mark.asyncio
async def test_keys_are_never_shared_across_repositories(tmp_path: Path) -> None:
    deploy_keys, metadata, _root = await service(tmp_path)
    first = await deploy_keys.create("owner/first", repository_id="1" * 16)
    second = await deploy_keys.create("owner/second", repository_id="2" * 16)

    first_environment = await deploy_keys.repository_environment("1" * 16)
    second_environment = await deploy_keys.repository_environment("2" * 16)
    assert first["credentialId"] != second["credentialId"]
    assert first_environment is not None and second_environment is not None
    assert first_environment["WORKBENCH_SSH_IDENTITY"] != second_environment[
        "WORKBENCH_SSH_IDENTITY"
    ]
    assert await deploy_keys.repository_environment("3" * 16) is None
    with pytest.raises(ValueError):
        await deploy_keys.create("owner/first", repository_id="3" * 16)
    assert len(metadata.records) == 2


@pytest.mark.asyncio
async def test_rotation_keeps_old_key_until_verified_promotion_and_explicit_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy_keys, _metadata, root = await service(tmp_path)
    created = await deploy_keys.create("owner/repo", repository_id="c" * 16)
    credential_id = cast(str, created["credentialId"])
    rotated = await deploy_keys.rotate(credential_id)
    assert rotated["candidate"] is not None

    class Process:
        returncode = 0

        async def wait(self) -> None:
            return None

        def kill(self) -> None:
            return None

    async def successful_probe(*_args: str, **_kwargs: object) -> Process:
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", successful_probe)
    await deploy_keys.test_access(credential_id, candidate=True)
    promoted = await deploy_keys.promote_rotation(credential_id)
    assert promoted["candidate"] is None
    assert promoted["retiredKeyAvailable"] is True
    assert any((directory / "retired").exists() for directory in root.iterdir())

    cleaned = await deploy_keys.remove_retired(credential_id)
    assert cleaned["retiredKeyAvailable"] is False
