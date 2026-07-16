from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from jusi_opencode.debug import emit_debug_event


@dataclass(frozen=True)
class OpenCodeRunOptions:
    cwd: Path
    prompt: str
    executable: str = "opencode"
    input_format_arg: str = ""
    input_format: str = ""
    output_format_arg: str = "--format"
    output_format: str = "json"
    prompt_transport: str = "argv"
    auto_arg: str = "--auto"
    approval_arg: str = ""
    approval_mode: str = ""
    session_arg: str = "--session"
    session: str = ""
    continue_last: bool = False
    model: str = ""
    variant: str = ""
    agent: str = ""
    auto: bool = False


def build_opencode_command(options: OpenCodeRunOptions) -> list[str]:
    command = [_normalize_executable(options.executable), "run"]
    if options.input_format_arg and options.input_format:
        command.extend([options.input_format_arg, options.input_format])
    if options.output_format_arg and options.output_format:
        command.extend([options.output_format_arg, options.output_format])
    if options.session:
        if options.session_arg:
            command.extend([options.session_arg, options.session])
    elif options.continue_last:
        command.append("--continue")
    if options.model:
        command.extend(["--model", options.model])
    if options.variant:
        command.extend(["--variant", options.variant])
    if options.agent:
        command.extend(["--agent", options.agent])
    if options.approval_arg and options.approval_mode:
        command.extend([options.approval_arg, options.approval_mode])
    if options.auto and options.auto_arg:
        command.append(options.auto_arg)
    if options.prompt_transport != "stdin":
        command.append(options.prompt)
    return command


def _normalize_executable(executable: str) -> str:
    value = str(executable or "").strip() or "opencode"
    if any(item in value for item in ("\0", "\n", "\r")):
        raise ValueError("opencode executable contains an invalid control character")
    if os.path.sep in value:
        return value
    return shutil.which(value) or value


def opencode_child_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("JUSI_PLUGIN_"):
            env.pop(key, None)
    return env


class OpenCodeEventStream:
    def __init__(self, options: OpenCodeRunOptions) -> None:
        self.options = options
        self.process: subprocess.Popen[str] | None = None
        self.cancelled = False

    def __iter__(self) -> Iterator[dict[str, object]]:
        if self.cancelled:
            yield {"type": "process.aborted", "exit_code": None}
            return
        process = subprocess.Popen(
            build_opencode_command(self.options),
            cwd=str(self.options.cwd),
            env=opencode_child_env(),
            stdin=subprocess.PIPE if self.options.prompt_transport == "stdin" else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
        self.process = process
        if self.options.prompt_transport == "stdin":
            assert process.stdin is not None
            try:
                process.stdin.write(self.options.prompt)
                if not self.options.prompt.endswith("\n"):
                    process.stdin.write("\n")
                process.stdin.close()
            except BrokenPipeError:
                self.cancelled = True
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                yield {"type": "raw_output", "text": stripped}
                continue
            yield event if isinstance(event, dict) else {"type": "raw_output", "text": stripped}
        returncode = process.wait()
        if self.cancelled:
            yield {"type": "process.aborted", "exit_code": returncode}
        elif returncode != 0:
            yield {"type": "process.exited", "exit_code": returncode}

    def cancel(self) -> bool:
        self.cancelled = True
        process = self.process
        if process is None:
            return True
        if process.poll() is not None:
            return False
        self._signal_process(signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            emit_debug_event("opencode_stream.cancel.escalate", pid=process.pid)
            self._signal_process(signal.SIGKILL)
            process.wait()
        return True

    def _signal_process(self, sig: int) -> None:
        process = self.process
        if process is None:
            return
        try:
            os.killpg(process.pid, sig)
        except OSError:
            try:
                process.send_signal(sig)
            except ProcessLookupError:
                return


def session_id_from_events(events: list[dict[str, object]]) -> str:
    for event in events:
        for key in ("sessionID", "session_id", "sessionId"):
            value = str(event.get(key, "")).strip()
            if value:
                return value
        session = event.get("session")
        if isinstance(session, dict):
            for key in ("id", "sessionID", "session_id"):
                value = str(session.get(key, "")).strip()
                if value:
                    return value
    return ""


def final_message_from_events(events: list[dict[str, object]]) -> str:
    message_texts: list[str] = []
    delta_texts: list[str] = []
    raw_lines: list[str] = []
    exit_code: int | None = None
    aborted = False
    for event in events:
        if event.get("type") == "raw_output":
            raw = str(event.get("text", "")).strip()
            if raw:
                raw_lines.append(raw)
        else:
            text, is_delta = event_text(event)
            if text:
                if is_delta:
                    delta_texts.append(text)
                else:
                    message_texts.append(text)
        if event.get("type") == "process.exited":
            exit_code = _int_value(event.get("exit_code"))
        if event.get("type") == "process.aborted":
            aborted = True
            exit_code = _int_value(event.get("exit_code"))
    if message_texts:
        return message_texts[-1]
    if delta_texts:
        return "".join(delta_texts).strip()
    if raw_lines:
        prefix = f"OpenCode exited with code {exit_code}: " if exit_code not in (None, 0) else ""
        return prefix + raw_lines[-1]
    if aborted:
        return "OpenCode aborted"
    if exit_code not in (None, 0):
        return f"OpenCode exited with code {exit_code}"
    return ""


def event_text(event: dict[str, object]) -> tuple[str, bool]:
    delta = _choice_delta_text(event)
    if delta:
        return delta, True
    candidates = [event.get("text"), event.get("content")]
    error = event.get("error")
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("message"))
        candidates.extend([error.get("message"), error.get("name")])
    for key in ("message", "item", "part", "delta", "data"):
        nested = event.get(key)
        text = _text_from_value(nested)
        if text:
            return text, False
    for value in candidates:
        text = _text_from_value(value).strip()
        if text:
            return text, False
    return "", False


def _text_from_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text_from_value(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "message", "delta"):
            text = _text_from_value(value.get(key))
            if text:
                return text
        choices = value.get("choices")
        if isinstance(choices, list):
            return "".join(_text_from_value(choice) for choice in choices)
    return ""


def _choice_delta_text(event: dict[str, object]) -> str:
    choices = event.get("choices")
    if not isinstance(choices, list):
        return ""
    fragments: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            text = _text_from_value(delta.get("content"))
            if text:
                fragments.append(text)
    return "".join(fragments)


def _int_value(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
