from __future__ import annotations

from pathlib import Path

from jusi_opencode.opencode_cli import OpenCodeRunOptions, build_opencode_command, final_message_from_events, opencode_child_env, session_id_from_events


def test_build_opencode_run_command() -> None:
    command = build_opencode_command(OpenCodeRunOptions(cwd=Path("/repo"), prompt="hello"))

    assert command[0].endswith("opencode") or command[0] == "opencode"
    assert command[1:] == ["run", "--format", "json", "hello"]


def test_build_opencode_run_command_with_session_options() -> None:
    command = build_opencode_command(
        OpenCodeRunOptions(
            cwd=Path("/repo"),
            prompt="hello",
            executable="myorgcode",
            session="ses_1",
            model="anthropic/claude-sonnet-4",
            variant="high",
            agent="build",
            auto=True,
        )
    )

    assert command[0].endswith("myorgcode") or command[0] == "myorgcode"
    assert command[1:] == [
        "run",
        "--format",
        "json",
        "--session",
        "ses_1",
        "--model",
        "anthropic/claude-sonnet-4",
        "--variant",
        "high",
        "--agent",
        "build",
        "--auto",
        "hello",
    ]


def test_build_continue_last_command() -> None:
    command = build_opencode_command(OpenCodeRunOptions(cwd=Path("/repo"), prompt="hello", continue_last=True))

    assert command[0].endswith("opencode") or command[0] == "opencode"
    assert command[1:] == ["run", "--format", "json", "--continue", "hello"]


def test_opencode_child_env_strips_jusi_plugin_runtime_vars(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JUSI_PLUGIN_RUNTIME_EVENTS_SOCKET", "/tmp/events.sock")
    monkeypatch.setenv("JUSI_SESSION_ID", "sess-1")

    env = opencode_child_env()

    assert "JUSI_PLUGIN_RUNTIME_EVENTS_SOCKET" not in env
    assert env["JUSI_SESSION_ID"] == "sess-1"


def test_extract_session_id_and_final_message() -> None:
    events = [
        {"type": "session.updated", "sessionID": "ses_1"},
        {"type": "message.part", "part": {"type": "text", "text": "first"}},
        {"type": "message.part", "part": {"type": "text", "text": "final"}},
    ]

    assert session_id_from_events(events) == "ses_1"
    assert final_message_from_events(events) == "final"


def test_final_message_reports_error_output() -> None:
    events = [{"type": "raw_output", "text": "failed"}, {"type": "process.exited", "exit_code": 1}]

    assert final_message_from_events(events) == "OpenCode exited with code 1: failed"


def test_final_message_extracts_opencode_error_payload() -> None:
    events = [
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "provider validation failed"}}},
        {"type": "process.exited", "exit_code": 1},
    ]

    assert final_message_from_events(events) == "provider validation failed"
