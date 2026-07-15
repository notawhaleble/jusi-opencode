from __future__ import annotations

from pathlib import Path

from jusi_opencode.config import opencode_config_from_session, resolve_target, target_config_from_opencode_config
from jusi_opencode.state import project_state


def test_resolve_target_uses_core_provided_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    target = resolve_target(
        "demo",
        {
            "path": str(project),
            "executable": "myorgcode",
            "model": "anthropic/claude-sonnet-4",
            "variant": "high",
            "auto": True,
        },
    )

    assert target.path == project.resolve()
    assert target.executable == "myorgcode"
    assert target.model == "anthropic/claude-sonnet-4"
    assert target.variant == "high"
    assert target.auto is True


def test_extract_target_config_from_session_config() -> None:
    session_config = {"opencode": {"demo": {"path": "/repo", "model": "model-a"}}}

    opencode_config = opencode_config_from_session(session_config)

    assert target_config_from_opencode_config(opencode_config, "demo") == {"path": "/repo", "model": "model-a"}


def test_resolve_target_accepts_direct_paths(tmp_path: Path) -> None:
    target = resolve_target(str(tmp_path))

    assert target.path == tmp_path.resolve()


def test_project_state_is_outside_project_and_stable(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    state_home = tmp_path / "state"

    state = project_state(project, state_home=state_home)

    assert state.root.parent.parent == state_home / "plugins"
    assert state.project_key.startswith("repo-")
