from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitSnapshot:
    status_porcelain: bytes
    status_paths: tuple[str, ...]
    diff_names: tuple[str, ...]
    staged_diff_names: tuple[str, ...]
    diff: str = ""
    staged_diff: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_porcelain": self.status_porcelain.decode("utf-8", "replace"),
            "status_paths": list(self.status_paths),
            "diff_names": list(self.diff_names),
            "staged_diff_names": list(self.staged_diff_names),
            "diff": self.diff,
            "staged_diff": self.staged_diff,
        }


@dataclass(frozen=True)
class FileState:
    exists: bool
    content: bytes = b""


def read_git_snapshot(cwd: Path) -> GitSnapshot:
    status = _git_bytes(cwd, "status", "--porcelain=v1", "-z")
    return GitSnapshot(
        status_porcelain=status,
        status_paths=_status_paths(status),
        diff_names=tuple(_git_text(cwd, "diff", "--name-only").splitlines()),
        staged_diff_names=tuple(_git_text(cwd, "diff", "--cached", "--name-only").splitlines()),
        diff=_git_text(cwd, "diff", "--binary"),
        staged_diff=_git_text(cwd, "diff", "--cached", "--binary"),
    )


def read_git_diff(cwd: Path) -> str:
    return _git_text(cwd, "diff", "--binary")


def capture_worktree_file_states(cwd: Path, paths: set[str]) -> dict[str, FileState]:
    return {path: read_worktree_file_state(cwd, path) for path in paths}


def read_worktree_file_state(cwd: Path, path: str) -> FileState:
    target = (cwd / path).resolve()
    try:
        target.relative_to(cwd.resolve())
    except ValueError:
        return FileState(False)
    if not target.is_file():
        return FileState(False)
    try:
        return FileState(True, target.read_bytes())
    except OSError:
        return FileState(False)


def _git_text(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    return result.stdout if result.returncode == 0 else ""


def _git_bytes(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=str(cwd), check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.stdout if result.returncode == 0 else b""


def _status_paths(status_porcelain: bytes) -> tuple[str, ...]:
    fields = [item for item in status_porcelain.decode("utf-8", "surrogateescape").split("\0") if item]
    paths: list[str] = []
    index = 0
    while index < len(fields):
        item = fields[index]
        path = item[3:] if len(item) > 3 else ""
        if path:
            paths.append(path)
        if item[:2].startswith(("R", "C")):
            index += 1
            if index < len(fields):
                paths.append(fields[index])
        index += 1
    return tuple(dict.fromkeys(paths))
