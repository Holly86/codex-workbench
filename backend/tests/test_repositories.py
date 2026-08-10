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


def test_delete_repository_removes_only_the_resolved_direct_child(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    repository = root / "sample"
    sibling = root / "keep"
    (repository / ".git").mkdir(parents=True)
    (repository / "work.txt").write_text("keep scope narrow", encoding="utf-8")
    (sibling / ".git").mkdir(parents=True)
    discovery = RepositoryDiscovery(root)
    summary = next(item for item in discovery._list_repositories_sync() if item.name == "sample")

    deleted = discovery.delete_repository(summary.id)

    assert deleted.name == "sample"
    assert not repository.exists()
    assert sibling.is_dir()


def test_delete_repository_rejects_unknown_ids_and_preserves_root(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    (root / "sample" / ".git").mkdir(parents=True)
    discovery = RepositoryDiscovery(root)

    with pytest.raises(ValueError, match="unavailable"):
        discovery.delete_repository("0000000000000000")

    assert root.is_dir()
    assert (root / "sample").is_dir()
