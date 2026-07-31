"""Constrained repository discovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from codex_workbench.security.paths import resolve_path_within


def _is_repository_directory(candidate: Path) -> bool:
    if candidate.is_symlink() or not candidate.is_dir():
        return False
    git_marker = candidate / ".git"
    return not git_marker.is_symlink() and (git_marker.is_dir() or git_marker.is_file())


@dataclass(frozen=True, slots=True)
class RepositorySummary:
    """A browser-safe repository identity without an absolute host path."""

    id: str
    name: str
    relative_path: str


class RepositoryDiscovery:
    """Find direct-child Git repositories below one configured root."""

    def __init__(self, repositories_root: Path) -> None:
        self._root = repositories_root

    async def list_repositories(self) -> tuple[RepositorySummary, ...]:
        return await asyncio.to_thread(self._list_repositories_sync)

    def resolve_repository(self, repository_relative_path: str) -> Path:
        candidate = resolve_path_within(self._root, repository_relative_path)
        if candidate.parent != self._root.resolve(strict=True):
            raise ValueError("only direct-child repositories are selectable")
        if not _is_repository_directory(candidate):
            raise ValueError("selection is not a discovered Git repository")
        return candidate

    def _list_repositories_sync(self) -> tuple[RepositorySummary, ...]:
        root = self._root.resolve(strict=True)
        repositories: list[RepositorySummary] = []
        for candidate in sorted(root.iterdir(), key=lambda entry: entry.name.casefold()):
            if not _is_repository_directory(candidate):
                continue
            resolved = resolve_path_within(root, candidate.name)
            relative = resolved.relative_to(root).as_posix()
            repositories.append(
                RepositorySummary(
                    id=sha256(relative.encode("utf-8")).hexdigest()[:16],
                    name=candidate.name,
                    relative_path=relative,
                )
            )
        return tuple(repositories)
