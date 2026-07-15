from __future__ import annotations

import curses
import threading
from collections import deque
from typing import Any, Callable

from jusi.visidata_support import bind_visidata_runtime, set_plugin_execution_status


_PATCHED = False
_PENDING_TURNS: deque[tuple[Any, str]] = deque()
_PENDING_ACTIONS: deque[Callable[[], object]] = deque()


def bind_opencode_runtime(sheet: Any, runtime: Any) -> None:
    bind_visidata_runtime(sheet, runtime)
    sheet.jusi_opencode_runtime = runtime
    sheet._jusi_opencode_runtime = runtime
    try:
        from visidata import BaseSheet, vd

        BaseSheet.jusi_opencode_runtime = runtime
        BaseSheet._jusi_opencode_runtime = runtime
        vd._jusi_opencode_runtime = runtime
    except Exception:
        return


def queue_opencode_turn(runtime: Any, prompt: str) -> None:
    prompt = str(prompt).strip()
    if not prompt:
        return
    set_plugin_execution_status("busy")
    _PENDING_TURNS.append((runtime, prompt))
    try:
        from visidata import vd

        vd.queueCommand("jusi-opencode-run-pending-turn")
    except Exception:
        pass
    wake_visidata()


def cancel_pending_opencode_turns(runtime: Any) -> int:
    kept: deque[tuple[Any, str]] = deque()
    cancelled = 0
    while _PENDING_TURNS:
        pending_runtime, prompt = _PENDING_TURNS.popleft()
        if pending_runtime is runtime:
            cancelled += 1
        else:
            kept.append((pending_runtime, prompt))
    _PENDING_TURNS.extend(kept)
    if cancelled:
        set_plugin_execution_status("follow-up")
    return cancelled


def run_pending_opencode_turns() -> None:
    while _PENDING_TURNS:
        runtime, prompt = _PENDING_TURNS.popleft()
        runtime.start_opencode_turn(prompt)


def queue_visidata_action(action: Callable[[], object], *, wait: bool = False, timeout: float = 1.5) -> object:
    done = threading.Event() if wait else None
    result: dict[str, object] = {}

    def wrapped_action() -> None:
        try:
            result["value"] = action()
        except BaseException as exc:
            result["error"] = exc
        finally:
            if done is not None:
                done.set()

    _PENDING_ACTIONS.append(wrapped_action)
    try:
        from visidata import vd

        vd.queueCommand("jusi-opencode-run-pending-action")
    except Exception:
        pass
    wake_visidata()
    if done is None:
        return {"queued": True}
    if not done.wait(timeout):
        return {"queued": True, "pending": True}
    error = result.get("error")
    if isinstance(error, BaseException):
        raise error
    return result.get("value", {})


def run_pending_visidata_actions() -> None:
    while _PENDING_ACTIONS:
        _PENDING_ACTIONS.popleft()()


def install_opencode_base_sheet_api() -> None:
    global _PATCHED
    if _PATCHED:
        return
    from visidata import BaseSheet

    @BaseSheet.api
    def jusi_opencode_run_pending_turns(sheet: Any) -> None:
        _ = sheet
        run_pending_opencode_turns()

    @BaseSheet.command("", "jusi-opencode-run-pending-turn", "run pending Jusi OpenCode turns", replay=False)
    def _run_pending_turn(sheet: Any) -> None:
        sheet.jusi_opencode_run_pending_turns()

    @BaseSheet.api
    def jusi_opencode_run_pending_action(sheet: Any) -> None:
        _ = sheet
        run_pending_visidata_actions()

    @BaseSheet.command("", "jusi-opencode-run-pending-action", "run pending Jusi OpenCode VisiData action", replay=False)
    def _run_pending_action(sheet: Any) -> None:
        sheet.jusi_opencode_run_pending_action()

    @BaseSheet.command("", "jusi-opencode-focus-turns", "focus Jusi OpenCode turns sheet", replay=False)
    def _focus_turns(sheet: Any) -> None:
        runtime = _opencode_runtime(sheet)
        if runtime is not None:
            runtime.push_turns_sheet()

    _PATCHED = True


def _opencode_runtime(sheet: Any) -> Any:
    from visidata import BaseSheet

    current = sheet
    seen: set[int] = set()
    while current is not None:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        runtime = getattr(current, "jusi_opencode_runtime", None)
        if runtime is not None:
            return runtime
        current = getattr(current, "source", None)
    return getattr(BaseSheet, "jusi_opencode_runtime", None)


def wake_visidata() -> None:
    try:
        curses.ungetch(curses.KEY_RESIZE)
    except Exception:
        return
