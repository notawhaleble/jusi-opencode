from __future__ import annotations

from pathlib import Path

from jusi_opencode.opencode_cli import (
    OpenCodeEventStream,
    OpenCodeRunOptions,
    build_opencode_command,
    final_message_from_events,
    event_text,
    opencode_child_env,
    session_id_from_events,
)


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


def test_build_orgcode_style_command_uses_stdin_and_output_format() -> None:
    command = build_opencode_command(
        OpenCodeRunOptions(
            cwd=Path("/repo"),
            prompt="hello",
            executable="orgcode",
            input_format_arg="--input-format",
            input_format="text",
            output_format_arg="--output-format",
            output_format="stream-json",
            prompt_transport="stdin",
        )
    )

    assert command[0].endswith("orgcode") or command[0] == "orgcode"
    assert command[1:] == ["run", "--input-format", "text", "--output-format", "stream-json"]


def test_event_stream_writes_prompt_to_stdin_for_stdin_transport(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    popen_kwargs = {}
    writes: list[str] = []

    class FakeStdin:
        def write(self, text: str) -> None:
            writes.append(text)

        def close(self) -> None:
            writes.append("<closed>")

    class FakeProcess:
        stdin = FakeStdin()
        stdout = iter(())

        def wait(self) -> int:
            return 0

    def fake_popen(*_args, **kwargs):  # type: ignore[no-untyped-def]
        popen_kwargs.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("jusi_opencode.opencode_cli.subprocess.Popen", fake_popen)

    list(OpenCodeEventStream(OpenCodeRunOptions(cwd=Path("/repo"), prompt="hello", prompt_transport="stdin")))

    assert popen_kwargs["stdin"] is not None
    assert writes == ["hello", "\n", "<closed>"]


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


def test_event_text_extracts_nested_message_content_without_stringifying_json() -> None:
    event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}]}}

    assert event_text(event) == ("hello world", False)


def test_final_message_concatenates_streaming_choice_deltas() -> None:
    events = [
        {"type": "chunk", "choices": [{"delta": {"content": "hello"}}]},
        {"type": "chunk", "choices": [{"delta": {"content": " world"}}]},
    ]

    assert event_text(events[0]) == ("hello", True)
    assert final_message_from_events(events) == "hello world"


def test_final_message_reports_error_output() -> None:
    events = [{"type": "raw_output", "text": "failed"}, {"type": "process.exited", "exit_code": 1}]

    assert final_message_from_events(events) == "OpenCode exited with code 1: failed"


def test_final_message_extracts_opencode_error_payload() -> None:
    events = [
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "provider validation failed"}}},
        {"type": "process.exited", "exit_code": 1},
    ]

    assert final_message_from_events(events) == "provider validation failed"
