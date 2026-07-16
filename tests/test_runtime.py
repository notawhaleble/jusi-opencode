from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from jusi.visidata_support import install_visidata_runtime_hooks

from jusi_opencode.runner import OpenCodeRuntime
from jusi_opencode.sheet import (
    bind_opencode_runtime,
    install_opencode_base_sheet_api,
    queue_opencode_turn,
    queue_visidata_action,
    run_pending_opencode_turns,
    run_pending_visidata_actions,
)
from jusi_opencode.state import append_jsonl, project_state, write_text


class FakeOpenCodeEventStream:
    def __init__(self, options, iterator):  # type: ignore[no-untyped-def]
        self.options = options
        self.iterator = iterator
        self.cancelled = False

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield from self.iterator(self.options)

    def cancel(self) -> bool:
        self.cancelled = True
        return True


def test_normal_followup_runs_opencode_with_current_options(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = project_state(repo, state_home=tmp_path / "state")
    runtime = OpenCodeRuntime(
        state=state,
        target_name="demo",
        target_path=repo,
        executable="myorgcode",
        input_format_arg="--input-format",
        input_format="text",
        output_format_arg="--output-format",
        output_format="stream-json",
        prompt_transport="stdin",
        auto_arg="",
        approval_arg="--approval-mode",
        approval_mode="auto-edit",
        current_model="anthropic/claude-sonnet-4",
        variant="high",
        agent="build",
        auto=True,
    )
    runtime.open_live_events_sheet = lambda: None  # type: ignore[method-assign]
    runtime.queue_focus_turns_sheet = lambda: None  # type: ignore[method-assign]
    runtime.live_rows = []
    seen = {}

    def fake_iter(options):  # type: ignore[no-untyped-def]
        seen["options"] = options
        yield {"type": "session.updated", "sessionID": "ses_1"}
        yield {"type": "message.part", "part": {"type": "text", "text": "done"}}

    with patch("jusi_opencode.runner.OpenCodeEventStream", lambda options: FakeOpenCodeEventStream(options, fake_iter)), patch(
        "jusi.visidata_support.set_plugin_execution_status"
    ):
        response = runtime.run_opencode_turn("do work")

    assert response["status"] == "done"
    assert seen["options"].model == "anthropic/claude-sonnet-4"
    assert seen["options"].executable == "myorgcode"
    assert seen["options"].input_format_arg == "--input-format"
    assert seen["options"].input_format == "text"
    assert seen["options"].output_format_arg == "--output-format"
    assert seen["options"].output_format == "stream-json"
    assert seen["options"].prompt_transport == "stdin"
    assert seen["options"].auto_arg == ""
    assert seen["options"].approval_arg == "--approval-mode"
    assert seen["options"].approval_mode == "auto-edit"
    assert seen["options"].variant == "high"
    assert seen["options"].agent == "build"
    assert seen["options"].auto is True
    assert seen["options"].cwd == repo
    assert runtime.session_id == "ses_1"
    assert runtime.live_rows[0]["type"] == "session.updated"
    assert (state.session_dir("ses_1") / "turn-0001" / "prompt.md").read_text(encoding="utf-8") == "do work"
    assert (state.session_dir("ses_1") / "turn-0001" / "final.md").read_text(encoding="utf-8") == "done"
    assert (state.session_dir("ses_1") / "turn-0001" / "opencode-events.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_interrupt_aborts_active_opencode_turn(tmp_path: Path) -> None:
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )
    stream = FakeOpenCodeEventStream(None, lambda _options: iter(()))
    runtime.active_event_stream = stream

    response = runtime.handle_control_request({"message_type": "interrupt", "payload": {}})

    assert response == {"handled": True, "active": True, "aborted": True}
    assert stream.cancelled is True


def test_action_result_is_forwarded_to_jusi_visidata_support(tmp_path: Path) -> None:
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )

    with patch("jusi.visidata_support.handle_plugin_runtime_control_request", return_value={"handled": True}) as handler:
        response = runtime.handle_control_request({"message_type": "action_result", "payload": {"request_id": "req-1"}})

    assert response == {"handled": True}
    handler.assert_called_once_with({"message_type": "action_result", "payload": {"request_id": "req-1"}})


def test_resume_slash_command_sets_session_id(tmp_path: Path) -> None:
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )

    response = runtime.handle_followup({"cell_text": "/resume ses_123"})

    assert response == {"handled": True, "command": "resume", "session_id": "ses_123"}
    assert runtime.session_id == "ses_123"
    assert runtime.continue_last is False


def test_resume_sheet_lists_first_prompt_and_turn_count(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = project_state(repo, state_home=tmp_path / "state")
    session_dir = state.session_dir("ses_1")
    first_turn = session_dir / "turn-0001"
    second_turn = session_dir / "turn-0002"
    write_text(first_turn / "prompt.md", "first prompt\nmore")
    write_text(first_turn / "final.md", "first reply")
    write_text(first_turn / "opencode-events.jsonl", "")
    write_text(first_turn / "files.json", '{"files":[]}\n')
    write_text(first_turn / "turn.json", "{}")
    write_text(second_turn / "prompt.md", "second prompt")
    write_text(second_turn / "final.md", "second reply")
    write_text(second_turn / "opencode-events.jsonl", "")
    write_text(second_turn / "files.json", '{"files":[]}\n')
    write_text(second_turn / "turn.json", "{}")
    append_jsonl(
        session_dir / "turns.jsonl",
        [
            {"session_id": "ses_1", "turn_dir": str(first_turn)},
            {"session_id": "ses_1", "turn_dir": str(second_turn), "executable": "myorgcode"},
        ],
    )
    runtime = OpenCodeRuntime(state=state, target_name="demo", target_path=repo)

    rows = runtime.list_resumable_sessions()

    assert rows[0]["session_id"] == "ses_1"
    assert rows[0]["first_prompt"] == "first prompt"
    assert rows[0]["turns"] == 2
    assert rows[0]["executable"] == "myorgcode"


def test_resume_session_updates_existing_turns_sheet_rows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = project_state(repo, state_home=tmp_path / "state")
    session_dir = state.session_dir("ses_1")
    turn_dir = session_dir / "turn-0001"
    write_text(turn_dir / "prompt.md", "previous prompt")
    write_text(turn_dir / "final.md", "previous reply")
    write_text(turn_dir / "opencode-events.jsonl", "")
    write_text(turn_dir / "files.json", '{"files":[]}\n')
    append_jsonl(session_dir / "turns.jsonl", [{"session_id": "ses_1", "turn_dir": str(turn_dir)}])
    runtime = OpenCodeRuntime(state=state, target_name="demo", target_path=repo)
    runtime.turns_sheet = type("FakeSheet", (), {"rows": [], "name": "", "recalc": lambda self: None})()
    runtime.push_turns_sheet = lambda: None  # type: ignore[method-assign]

    response = runtime.resume_session("ses_1")

    assert response == {"handled": True, "command": "resume", "session_id": "ses_1"}
    assert runtime.turns_sheet.rows[0]["prompt"] == "previous prompt"


def test_runtime_option_slash_commands_update_future_turns(tmp_path: Path) -> None:
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
        current_model="old",
        variant="low",
        agent="build",
        auto=False,
    )

    assert runtime.handle_followup({"cell_text": "/model anthropic/claude-sonnet-4"}) == {
        "handled": True,
        "command": "model",
        "model": "anthropic/claude-sonnet-4",
    }
    assert runtime.handle_followup({"cell_text": "/variant high"}) == {"handled": True, "command": "variant", "variant": "high"}
    assert runtime.handle_followup({"cell_text": "/agent review"}) == {"handled": True, "command": "agent", "agent": "review"}
    assert runtime.handle_followup({"cell_text": "/approval-mode auto-edit"}) == {
        "handled": True,
        "command": "approval-mode",
        "approval_mode": "auto-edit",
    }
    assert runtime.handle_followup({"cell_text": "/auto on"}) == {"handled": True, "command": "auto", "auto": True}

    assert runtime.current_model == "anthropic/claude-sonnet-4"
    assert runtime.variant == "high"
    assert runtime.agent == "review"
    assert runtime.approval_mode == "auto-edit"
    assert runtime.auto is True


def test_initial_resume_prompt_opens_resume_sheet(tmp_path: Path) -> None:
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )
    resume_sheet = object()
    runtime.make_resume_sheet = lambda: resume_sheet  # type: ignore[method-assign]
    runtime.queue_opencode_turn = lambda prompt: (_ for _ in ()).throw(AssertionError(prompt))  # type: ignore[method-assign]

    assert runtime.initial_sheet_for_prompt("/resume") is resume_sheet


def test_bind_opencode_runtime_sets_plugin_specific_visidata_refs(tmp_path: Path) -> None:
    from visidata import Sheet, vd

    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )
    sheet = Sheet("demo")

    bind_opencode_runtime(sheet, runtime)

    assert sheet.jusi_opencode_runtime is runtime
    assert vd._jusi_opencode_runtime is runtime


def test_followup_queue_runs_pending_turn(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )
    seen: list[str] = []
    queued_commands: list[str] = []
    runtime.start_opencode_turn = lambda prompt: seen.append(prompt)  # type: ignore[method-assign]
    install_visidata_runtime_hooks()
    install_opencode_base_sheet_api()
    monkeypatch.setattr("visidata.vd.queueCommand", queued_commands.append)
    monkeypatch.setattr("jusi_opencode.sheet.wake_visidata", lambda: None)

    with patch("jusi_opencode.sheet.set_plugin_execution_status"):
        queue_opencode_turn(runtime, "do work")
        run_pending_opencode_turns()

    assert queued_commands == ["jusi-opencode-run-pending-turn"]
    assert seen == ["do work"]


def test_resume_without_args_queues_resume_sheet_action(tmp_path: Path) -> None:
    runtime = OpenCodeRuntime(
        state=project_state(tmp_path / "repo", state_home=tmp_path / "state"),
        target_name="demo",
        target_path=tmp_path / "repo",
    )
    seen: list[str] = []
    runtime.open_resume_sheet = lambda: seen.append("opened")  # type: ignore[method-assign]

    response = runtime.handle_followup({"cell_text": "/resume"})
    run_pending_visidata_actions()

    assert response == {"handled": True, "command": "resume", "queued": True}
    assert seen == ["opened"]


def test_visidata_action_queue_uses_visidata_command(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    queued_commands: list[str] = []
    actions: list[str] = []
    install_opencode_base_sheet_api()
    monkeypatch.setattr("visidata.vd.queueCommand", queued_commands.append)
    monkeypatch.setattr("jusi_opencode.sheet.wake_visidata", lambda: None)

    response = queue_visidata_action(lambda: actions.append("ran") or {"handled": True})
    run_pending_visidata_actions()

    assert response == {"queued": True}
    assert queued_commands == ["jusi-opencode-run-pending-action"]
    assert actions == ["ran"]
