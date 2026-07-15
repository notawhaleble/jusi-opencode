from __future__ import annotations

from jusi.domain.models import ExecutableCell
from jusi.plugins import DisplayHandlerRegistry

from jusi_opencode.payload import OPENCODE_BOOTSTRAP_BODY, OPENCODE_BOOTSTRAP_COMMAND, normalize_followup_payload
from jusi_opencode.plugin import display_handler_specs
from jusi_opencode.runner import OpenCodeRuntime
from jusi_opencode.state import project_state


def test_opencode_magic_advertises_blank_body_bootstrap() -> None:
    spec = display_handler_specs()[0]

    assert spec.handler_id == "opencode"
    assert spec.magic_commands[0].name == "opencode"
    assert spec.magic_commands[0].bootstrap_body == OPENCODE_BOOTSTRAP_BODY


def test_blank_body_bootstrap_only_populates_empty_opencode_body() -> None:
    registry = DisplayHandlerRegistry(display_handler_specs())
    blank_cell = ExecutableCell(cell_id=1, kind="magic", syntax="python", main_lines=["%%opencode demo"])
    prompt_cell = ExecutableCell(cell_id=2, kind="magic", syntax="python", main_lines=["%%opencode demo", "real prompt"])

    assert registry.cell_with_blank_body_bootstrap(blank_cell).main_lines == ["%%opencode demo", OPENCODE_BOOTSTRAP_BODY]
    assert registry.cell_with_blank_body_bootstrap(prompt_cell).main_lines == ["%%opencode demo", "real prompt"]


def test_normalize_followup_payload_strips_opencode_header() -> None:
    normalized = normalize_followup_payload(
        {"cell_text": "%%opencode demo\nactual prompt", "cell": {"main_lines": ["%%opencode demo", "actual prompt"]}}
    )

    assert normalized["cell_text"] == "actual prompt"
    assert normalized["cell"]["main_lines"] == ["actual prompt"]


def test_bootstrap_prompt_is_noop_for_followup(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )

    assert runtime.handle_followup({"cell_text": f"/{OPENCODE_BOOTSTRAP_COMMAND}"}) == {
        "handled": True,
        "command": OPENCODE_BOOTSTRAP_COMMAND,
    }
