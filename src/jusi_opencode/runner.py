from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jusi.visidata_support import append_plugin_frontend_action
from jusi_opencode.commands import parse_slash_command
from jusi_opencode.config import resolve_target
from jusi_opencode.git_artifacts import FileState, capture_worktree_file_states, read_git_diff, read_git_snapshot, read_worktree_file_state
from jusi_opencode.opencode_cli import OpenCodeEventStream, OpenCodeRunOptions, event_text, final_message_from_events, session_id_from_events
from jusi_opencode.payload import OPENCODE_BOOTSTRAP_COMMAND, strip_opencode_header
from jusi_opencode.sheet import bind_opencode_runtime, cancel_pending_opencode_turns, install_opencode_base_sheet_api, queue_opencode_turn, queue_visidata_action, wake_visidata
from jusi_opencode.state import ProjectState, append_jsonl, ensure_project_state, next_turn_dir, project_state, read_jsonl, read_text, write_json, write_text


def run_opencode_runner() -> int:
    payload = _payload_from_env()
    content = strip_opencode_header(str(payload.get("content", ""))).strip()
    meta = payload.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    target_name = str(meta.get("target", "")).strip()
    if not target_name:
        sys.stderr.write("%%opencode requires a target name or path\n")
        return 2
    target_config = meta.get("target_config", {})
    if not isinstance(target_config, dict):
        target_config = {}
    try:
        target = resolve_target(target_name, target_config)
    except Exception as exc:
        sys.stderr.write(f"failed to resolve opencode target: {exc}\n")
        return 2
    state = project_state(target.path)
    ensure_project_state(state)
    runtime = OpenCodeRuntime(
        state=state,
        target_name=target.name,
        target_path=target.path,
        executable=str(meta.get("executable", "")).strip() or target.executable,
        input_format_arg=target.input_format_arg,
        input_format=target.input_format,
        output_format_arg=target.output_format_arg,
        output_format=target.output_format,
        prompt_transport=target.prompt_transport,
        current_model=str(meta.get("model", "")).strip() or target.model,
        variant=str(meta.get("variant", "")).strip() or target.variant,
        agent=str(meta.get("agent", "")).strip() or target.agent,
        auto=bool(meta.get("auto", False)) or target.auto,
        session_id=str(meta.get("session", "")).strip(),
        continue_last=bool(meta.get("continue_last", False)),
        initial_prompt=content,
    )
    from jusi.infrastructure.plugin_runtime import set_plugin_control_handler

    set_plugin_control_handler(runtime.handle_control_request)
    return runtime.run()


@dataclass
class OpenCodeRuntime:
    state: ProjectState
    target_name: str
    target_path: Path
    executable: str = "opencode"
    input_format_arg: str = ""
    input_format: str = ""
    output_format_arg: str = "--format"
    output_format: str = "json"
    prompt_transport: str = "argv"
    current_model: str = ""
    variant: str = ""
    agent: str = ""
    auto: bool = False
    session_id: str = ""
    continue_last: bool = False
    initial_prompt: str = ""
    local_session_id: str = ""
    rows: list[dict[str, object]] | None = None
    turns_sheet: object | None = None
    live_rows: list[dict[str, object]] | None = None
    live_sheet: object | None = None
    active_event_stream: OpenCodeEventStream | None = None

    def __post_init__(self) -> None:
        if not self.local_session_id:
            self.local_session_id = f"run-{uuid4().hex}"

    def run(self) -> int:
        try:
            from visidata import ColumnItem, Sheet, vd
        except ModuleNotFoundError:
            sys.stderr.write("VisiData is not available for %%opencode\n")
            return 2
        vd.timeouts_before_idle = -1
        vd._jusi_opencode_runtime = self
        install_opencode_base_sheet_api()
        self.rows = self.load_persisted_turn_rows()

        runtime = self

        class OpenCodeTurnsSheet(Sheet):  # type: ignore[misc, valid-type]
            def __init__(self, *names, **kwargs):  # type: ignore[no-untyped-def]
                super().__init__(*names, **kwargs)
                bind_opencode_runtime(self, runtime)

            def openRow(self, row, rowidx=None):  # type: ignore[no-untyped-def]
                _ = rowidx
                cursor_col = getattr(self, "cursorCol", None)
                if str(getattr(cursor_col, "name", "")) == "changed_files":
                    return runtime.make_touched_files_sheet(row)
                return runtime.open_turn_events_sheet(row)

        self.turns_sheet = OpenCodeTurnsSheet(
            "opencode_turns",
            rows=self.rows,
            columns=[
                ColumnItem("turn", width=8),
                ColumnItem("prompt", width=36),
                ColumnItem("reply", width=60),
                ColumnItem("changed_files", width=14),
                ColumnItem("model", width=24),
                ColumnItem("executable", width=18),
                ColumnItem("session_id", width=28),
            ],
        )
        self.turns_sheet.addCommand("", "jusi-opencode-resume", "vd.push(vd._jusi_opencode_runtime.make_resume_sheet())", "open stored OpenCode sessions")
        self.turns_sheet.addCommand("", "opencode-meta", "vd._jusi_opencode_runtime.open_meta_sheet()", "open OpenCode session metadata")
        prompt = self.initial_prompt.strip()
        initial_sheet = self.initial_sheet_for_prompt(prompt)
        if initial_sheet is not None:
            vd.run(self.turns_sheet, initial_sheet)
        else:
            if prompt and prompt != f"/{OPENCODE_BOOTSTRAP_COMMAND}" and parse_slash_command(prompt) is None:
                self.queue_opencode_turn(prompt)
            vd.run(self.turns_sheet)
        return 0

    def initial_sheet_for_prompt(self, prompt: str):  # type: ignore[no-untyped-def]
        prompt = prompt.strip()
        if not prompt:
            return None
        command = parse_slash_command(prompt)
        if command is None:
            return None
        if command.name == OPENCODE_BOOTSTRAP_COMMAND:
            return None
        if command.name == "resume":
            if command.args:
                self.resume_session(command.args[0])
                return None
            return self.make_resume_sheet()
        if command.name == "session" and command.args:
            self.resume_session(command.args[0])
            return None
        if command.name == "continue":
            self.session_id = ""
            self.continue_last = True
            return None
        if command.name == "model" and command.args:
            self.current_model = command.args[0].strip()
            return None
        if command.name == "variant" and command.args:
            self.variant = command.args[0].strip()
            return None
        if command.name == "agent" and command.args:
            self.agent = command.args[0].strip()
            return None
        if command.name == "auto":
            if command.args:
                self.auto = command.args[0].strip().lower() in {"1", "true", "yes", "on"}
            else:
                self.auto = not self.auto
            return None
        return None

    def handle_control_request(self, request: dict[str, object]) -> dict[str, object]:
        message_type = str(request.get("message_type", "")).strip()
        if message_type == "action_result":
            from jusi.visidata_support import handle_plugin_runtime_control_request

            return handle_plugin_runtime_control_request(request)
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if message_type == "followup":
            return self.handle_followup(payload)
        if message_type in {"interrupt", "interrupt_request", "abort", "abort_request", "stop"}:
            return self.handle_interrupt()
        return {}

    def handle_followup(self, payload: dict[str, object]) -> dict[str, object]:
        text = strip_opencode_header(str(payload.get("cell_text", ""))).strip()
        command = parse_slash_command(text)
        if command is None:
            self.queue_opencode_turn(text)
            return {"handled": True, "queued": True}
        if command.name == OPENCODE_BOOTSTRAP_COMMAND:
            return {"handled": True, "command": OPENCODE_BOOTSTRAP_COMMAND}
        if command.name == "resume":
            if command.args:
                return self.resume_session(command.args[0])
            response = queue_visidata_action(self.open_resume_sheet)
            return {"handled": True, "command": "resume", **(response if isinstance(response, dict) else {})}
        if command.name == "session":
            if not command.args:
                return {"handled": False, "command": "session", "error": "usage: /session SESSION_ID"}
            return self.resume_session(command.args[0])
        if command.name == "continue":
            self.session_id = ""
            self.continue_last = True
            return {"handled": True, "command": "continue", "continue_last": True}
        if command.name == "model":
            if command.args:
                self.current_model = command.args[0].strip()
            return {"handled": True, "command": "model", "model": self.current_model}
        if command.name == "variant":
            if command.args:
                self.variant = command.args[0].strip()
            return {"handled": True, "command": "variant", "variant": self.variant}
        if command.name == "agent":
            if command.args:
                self.agent = command.args[0].strip()
            return {"handled": True, "command": "agent", "agent": self.agent}
        if command.name == "auto":
            if command.args:
                self.auto = command.args[0].strip().lower() in {"1", "true", "yes", "on"}
            else:
                self.auto = not self.auto
            return {"handled": True, "command": "auto", "auto": self.auto}
        return {"handled": False, "error": f"unsupported opencode command: /{command.name}"}

    def handle_interrupt(self) -> dict[str, object]:
        cancelled_pending = cancel_pending_opencode_turns(self)
        stream = self.active_event_stream
        if stream is None:
            return {"handled": bool(cancelled_pending), "active": False, "queued": cancelled_pending, "aborted": bool(cancelled_pending)}
        response = {"handled": True, "active": True, "aborted": stream.cancel()}
        if cancelled_pending:
            response["queued"] = cancelled_pending
        return response

    def queue_opencode_turn(self, prompt: str) -> None:
        queue_opencode_turn(self, prompt)

    def start_opencode_turn(self, prompt: str) -> None:
        prompt = strip_opencode_header(prompt).strip()
        if not prompt:
            return
        self.open_live_events_sheet()
        try:
            from visidata import vd

            vd.execAsync(self.run_opencode_turn, prompt, sheet=self.live_sheet)
        except Exception:
            self.run_opencode_turn(prompt)

    def run_opencode_turn(self, prompt: str) -> dict[str, object]:
        try:
            return self._run_opencode_turn_active(prompt)
        finally:
            from jusi.visidata_support import set_plugin_execution_status

            set_plugin_execution_status("follow-up")

    def _run_opencode_turn_active(self, prompt: str) -> dict[str, object]:
        before = read_git_snapshot(self.target_path)
        before_names = set(before.status_paths) | set(before.diff_names) | set(before.staged_diff_names)
        before_file_states = capture_worktree_file_states(self.target_path, before_names)
        options = OpenCodeRunOptions(
            cwd=self.target_path,
            prompt=prompt,
            executable=self.executable,
            input_format_arg=self.input_format_arg,
            input_format=self.input_format,
            output_format_arg=self.output_format_arg,
            output_format=self.output_format,
            prompt_transport=self.prompt_transport,
            session=self.session_id,
            continue_last=(self.continue_last and not self.session_id),
            model=self.current_model,
            variant=self.variant,
            agent=self.agent,
            auto=self.auto,
        )
        events: list[dict[str, object]] = []
        turn_dir: Path | None = None
        session_dir: Path | None = None
        events_path: Path | None = None
        stream = OpenCodeEventStream(options)
        self.active_event_stream = stream
        try:
            for event in stream:
                events.append(event)
                found_session = session_id_from_events([event])
                if found_session:
                    self.session_id = found_session
                if turn_dir is None:
                    session_dir = self.state.session_dir(self.session_id or self.local_session_id)
                    turn_dir = next_turn_dir(session_dir)
                    events_path = turn_dir / "opencode-events.jsonl"
                append_jsonl(events_path, [event])
                self.append_live_event(event, len(events))
        finally:
            self.active_event_stream = None
        if turn_dir is None or session_dir is None:
            session_dir = self.state.session_dir(self.session_id or self.local_session_id)
            turn_dir = next_turn_dir(session_dir)
        after = read_git_snapshot(self.target_path)
        diff = read_git_diff(self.target_path)
        final_message = final_message_from_events(events)
        write_text(turn_dir / "prompt.md", prompt)
        write_text(turn_dir / "final.md", final_message)
        write_text(turn_dir / "diff.patch", diff)
        write_json(turn_dir / "before.json", before.to_dict())
        write_json(turn_dir / "after.json", after.to_dict())
        file_records = _persist_turn_file_artifacts(turn_dir, self.target_path, before, after, before_file_states)
        metadata = {
            "session_id": self.session_id,
            "model": self.current_model,
            "variant": self.variant,
            "executable": self.executable,
            "input_format": self.input_format,
            "output_format": self.output_format,
            "prompt_transport": self.prompt_transport,
            "event_count": len(events),
            "touched_files": [str(item["path"]) for item in file_records],
            "turn_dir": str(turn_dir),
        }
        write_json(turn_dir / "turn.json", metadata)
        append_jsonl(session_dir / "turns.jsonl", [metadata])
        row = self._row_from_turn_record(metadata)
        if row is not None:
            self._append_turn_row(row)
        self.queue_focus_turns_sheet()
        return {"handled": True, "status": "done", **metadata}

    def load_persisted_turn_rows(self) -> list[dict[str, object]]:
        if not self.session_id:
            return []
        return [row for record in read_jsonl(self.state.session_dir(self.session_id) / "turns.jsonl") if (row := self._row_from_turn_record(record)) is not None]

    def resume_session(self, session_id: str) -> dict[str, object]:
        normalized = str(session_id or "").strip()
        if not normalized:
            return {"handled": False, "command": "resume", "error": "usage: /resume SESSION_ID"}
        self.session_id = normalized
        self.continue_last = False
        self.rows = self.load_persisted_turn_rows()
        if self.turns_sheet is not None:
            try:
                self.turns_sheet.rows = self.rows  # type: ignore[attr-defined]
                self.turns_sheet.name = f"opencode_turns:{self.session_id}"  # type: ignore[attr-defined]
                self.turns_sheet.recalc()  # type: ignore[attr-defined]
                wake_visidata()
            except Exception:
                pass
        return {"handled": True, "command": "resume", "session_id": self.session_id}


    def _row_from_turn_record(self, record: dict[str, object]) -> dict[str, object] | None:
        turn_dir = Path(str(record.get("turn_dir", "")).strip())
        if not str(turn_dir):
            return None
        files = _read_turn_file_records(turn_dir)
        touched = [str(item.get("path", "")) for item in files if str(item.get("path", ""))]
        return {
            "turn": turn_dir.name.rsplit("-", 1)[-1],
            "prompt": read_text(turn_dir / "prompt.md"),
            "reply": read_text(turn_dir / "final.md"),
            "changed_files": len(touched),
            "model": _model_label(str(record.get("model", "")).strip(), str(record.get("variant", "")).strip()),
            "executable": str(record.get("executable", "opencode")).strip() or "opencode",
            "session_id": str(record.get("session_id", "")).strip(),
            "turn_dir": str(turn_dir),
            "events_path": str(turn_dir / "opencode-events.jsonl"),
            "files_path": str(turn_dir / "files.json"),
            "touched_files": touched,
        }

    def open_live_events_sheet(self) -> None:
        from visidata import ColumnItem, Sheet, vd

        self.live_rows = []
        self.live_sheet = Sheet("opencode_events_live", rows=self.live_rows, columns=_event_sheet_columns(ColumnItem))
        bind_opencode_runtime(self.live_sheet, self)
        vd.push(self.live_sheet)

    def append_live_event(self, event: dict[str, object], index: int) -> None:
        if self.live_rows is None:
            return
        self.live_rows.append(_event_row(event, index))
        wake_visidata()

    def open_turn_events_sheet(self, row: object):  # type: ignore[no-untyped-def]
        from visidata import ColumnItem, Sheet, vd

        if not isinstance(row, dict):
            vd.status("no OpenCode turn selected")
            return None
        rows = [_event_row(event, index) for index, event in enumerate(read_jsonl(Path(str(row.get("events_path", "")))), start=1)]
        sheet = Sheet(f"opencode_events_{row.get('turn', '')}", rows=rows, columns=_event_sheet_columns(ColumnItem))
        bind_opencode_runtime(sheet, self)
        return sheet

    def make_touched_files_sheet(self, row: object):  # type: ignore[no-untyped-def]
        from visidata import ColumnItem, Sheet, vd

        if not isinstance(row, dict):
            vd.status("no OpenCode turn selected")
            return None
        records = _read_turn_file_records(Path(str(row.get("turn_dir", ""))))
        runtime = self

        class OpenCodeFilesSheet(Sheet):  # type: ignore[misc, valid-type]
            def openRow(self, row, rowidx=None):  # type: ignore[no-untyped-def]
                _ = rowidx
                runtime.open_file_diff(row)
                return None

        sheet = OpenCodeFilesSheet(
            f"opencode_files_{row.get('turn', '')}",
            rows=records,
            columns=[
                ColumnItem("path", width=80),
                ColumnItem("status", width=12),
                ColumnItem("before_exists", width=12),
                ColumnItem("after_exists", width=12),
            ],
        )
        bind_opencode_runtime(sheet, self)
        return sheet

    def open_file_diff(self, row: object) -> None:
        if not isinstance(row, dict):
            return
        append_plugin_frontend_action(
            "diff_show",
            {
                "path": str(row.get("path", "")),
                "before_path": str(row.get("before_path", "")),
                "after_path": str(row.get("after_path", "")),
                "before_exists": bool(row.get("before_exists", False)),
                "after_exists": bool(row.get("after_exists", False)),
                "open_in": "split",
                "layout": "below",
            },
        )

    def open_resume_sheet(self) -> None:
        from visidata import vd

        vd.push(self.make_resume_sheet())
        wake_visidata()

    def make_resume_sheet(self):  # type: ignore[no-untyped-def]
        from visidata import ColumnItem, Sheet, vd

        runtime = self

        class OpenCodeResumeSheet(Sheet):  # type: ignore[misc, valid-type]
            def openRow(self, row, rowidx=None):  # type: ignore[no-untyped-def]
                _ = rowidx
                if isinstance(row, dict):
                    runtime.resume_session(str(row.get("session_id", "")).strip())
                    runtime.push_turns_sheet()
                return runtime.turns_sheet

        return OpenCodeResumeSheet(
            "opencode_sessions",
            rows=self.list_resumable_sessions(),
            columns=[
                ColumnItem("session_id", width=36),
                ColumnItem("first_prompt", width=60),
                ColumnItem("turns", width=8),
                ColumnItem("executable", width=18),
                ColumnItem("last_updated", width=22),
            ],
        )

    def list_resumable_sessions(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        try:
            candidates = [path for path in self.state.sessions_dir.iterdir() if path.is_dir()]
        except FileNotFoundError:
            return rows
        for session_dir in candidates:
            records = read_jsonl(session_dir / "turns.jsonl")
            first_prompt = ""
            last_updated = ""
            if records:
                first = records[0]
                raw_turn_dir = str(first.get("turn_dir", "")).strip()
                if raw_turn_dir:
                    lines = read_text(Path(raw_turn_dir) / "prompt.md").strip().splitlines()
                    first_prompt = lines[0] if lines else ""
                last = records[-1]
                raw_last_turn_dir = str(last.get("turn_dir", "")).strip()
                stamp_source = Path(raw_last_turn_dir) / "turn.json" if raw_last_turn_dir else session_dir / "turns.jsonl"
            else:
                stamp_source = session_dir
            try:
                import datetime

                last_updated = datetime.datetime.fromtimestamp(stamp_source.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                last_updated = ""
            rows.append(
                {
                    "session_id": str(records[-1].get("session_id", "")).strip() if records else session_dir.name,
                    "first_prompt": first_prompt,
                    "turns": len(records),
                    "executable": str(records[-1].get("executable", "opencode")).strip() if records else "opencode",
                    "last_updated": last_updated,
                }
            )
        return sorted(rows, key=lambda row: str(row.get("last_updated", "")).strip(), reverse=True)

    def open_meta_sheet(self) -> None:
        from visidata import ColumnItem, Sheet, vd

        rows = [
            {"key": "target", "value": self.target_name},
            {"key": "path", "value": str(self.target_path)},
            {"key": "state", "value": str(self.state.root)},
            {"key": "executable", "value": self.executable},
            {"key": "model", "value": _model_label(self.current_model, self.variant)},
            {"key": "session", "value": self.session_id or "(new)"},
            {"key": "agent", "value": self.agent},
        ]
        vd.push(Sheet("opencode_meta", rows=rows, columns=[ColumnItem("key", width=18), ColumnItem("value", width=100)]))

    def push_turns_sheet(self) -> None:
        if self.turns_sheet is None:
            return
        try:
            from visidata import vd

            vd.push(self.turns_sheet)
            wake_visidata()
        except Exception:
            return

    def queue_focus_turns_sheet(self) -> None:
        try:
            from visidata import vd

            vd.queueCommand("jusi-opencode-focus-turns")
            wake_visidata()
        except Exception:
            return

    def _append_turn_row(self, row: dict[str, object]) -> None:
        if self.rows is None:
            self.rows = []
        self.rows.append(row)
        if self.turns_sheet is not None:
            self.turns_sheet.rows = self.rows  # type: ignore[attr-defined]


def _payload_from_env() -> dict[str, object]:
    try:
        payload = json.loads(os.environ.get("JUSI_OPENCODE_PAYLOAD_JSON", "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _event_sheet_columns(column_item):  # type: ignore[no-untyped-def]
    return [
        column_item("n", width=6),
        column_item("type", width=28),
        column_item("text", width=80),
        column_item("session_id", width=28),
        column_item("exit_code", width=10),
    ]


def _event_row(event: dict[str, object], index: int) -> dict[str, object]:
    return {
        "n": index,
        "type": str(event.get("type", "")),
        "text": event_text(event)[0],
        "session_id": session_id_from_events([event]),
        "exit_code": str(event.get("exit_code", "")),
    }


def _persist_turn_file_artifacts(turn_dir: Path, cwd: Path, before, after, before_file_states: dict[str, FileState]) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    paths = sorted(set(before.status_paths) | set(before.diff_names) | set(after.status_paths) | set(after.diff_names) | set(after.staged_diff_names))
    records: list[dict[str, object]] = []
    files_dir = turn_dir / "files"
    for path in paths:
        before_state = before_file_states.get(path, FileState(False))
        after_state = read_worktree_file_state(cwd, path)
        if before_state.exists == after_state.exists and before_state.content == after_state.content:
            continue
        safe_name = path.replace("/", "__")
        before_path = files_dir / f"{safe_name}.before"
        after_path = files_dir / f"{safe_name}.after"
        write_text(before_path, before_state.content.decode("utf-8", "replace") if before_state.exists else "")
        write_text(after_path, after_state.content.decode("utf-8", "replace") if after_state.exists else "")
        records.append(
            {
                "path": path,
                "status": _file_status(before_state, after_state),
                "before_path": str(before_path),
                "after_path": str(after_path),
                "before_exists": before_state.exists,
                "after_exists": after_state.exists,
            }
        )
    write_json(turn_dir / "files.json", {"files": records})
    return records


def _read_turn_file_records(turn_dir: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads((turn_dir / "files.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    files = payload.get("files", []) if isinstance(payload, dict) else []
    return [dict(item) for item in files if isinstance(item, dict)]


def _file_status(before: FileState, after: FileState) -> str:
    if before.exists and after.exists:
        return "modified"
    if after.exists:
        return "added"
    return "deleted"


def _model_label(model: str, variant: str) -> str:
    if not model:
        return "(opencode default)"
    return f"{model} ({variant})" if variant else model
