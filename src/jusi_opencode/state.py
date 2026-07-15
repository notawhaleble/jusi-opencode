from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PLUGIN_NAME = "opencode"


@dataclass(frozen=True)
class ProjectState:
    root: Path
    project_path: Path
    project_key: str

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / _safe_segment(session_id)


def default_jusi_state_home() -> Path:
    override = os.environ.get("JUSI_STATE_HOME", "").strip()
    if override:
        return Path(os.path.expanduser(override)).resolve()
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg_state:
        return (Path(os.path.expanduser(xdg_state)) / "jusi").resolve()
    return (Path.home() / ".local" / "state" / "jusi").resolve()


def project_state(project_path: Path, *, state_home: Path | None = None) -> ProjectState:
    resolved = project_path.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    key = f"{_slug(resolved.name or 'project')}-{digest}"
    root = (state_home or default_jusi_state_home()) / "plugins" / PLUGIN_NAME / key
    return ProjectState(root=root, project_path=resolved, project_key=key)


def ensure_project_state(state: ProjectState) -> None:
    state.sessions_dir.mkdir(parents=True, exist_ok=True)
    write_json(state.root / "project.json", {"plugin": PLUGIN_NAME, "project_key": state.project_key, "project_path": str(state.project_path)})


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def next_turn_dir(session_dir: Path) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    numbers: list[int] = []
    for path in session_dir.iterdir():
        if path.is_dir() and path.name.startswith("turn-"):
            try:
                numbers.append(int(path.name.split("-", 1)[1]))
            except (IndexError, ValueError):
                pass
    turn_dir = session_dir / f"turn-{((max(numbers) + 1) if numbers else 1):04d}"
    turn_dir.mkdir(parents=True, exist_ok=False)
    return turn_dir


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._") or "project"


def _safe_segment(value: str) -> str:
    return _slug(value)[:120] or "unknown"
