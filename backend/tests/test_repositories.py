from pathlib import Path

import pytest

from codex_workbench.repositories import RepositoryDiscovery


@pytest.mark.asyncio
async def test_discovers_only_direct_non_symlink_git_repositories(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    repo = root / "alpha"
    nested = root / "group" / "nested"
    plain = root / "notes"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    plain.mkdir()
    (root / "linked").symlink_to(repo, target_is_directory=True)
    unsafe_metadata = root / "unsafe-metadata"
    unsafe_metadata.mkdir()
    (unsafe_metadata / ".git").symlink_to(repo / ".git", target_is_directory=True)

    results = await RepositoryDiscovery(root).list_repositories()

    assert [(item.name, item.relative_path) for item in results] == [("alpha", "alpha")]
    assert len(results[0].id) == 16
