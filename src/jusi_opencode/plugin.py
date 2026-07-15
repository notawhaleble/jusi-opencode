from __future__ import annotations

import json
import os
import sys
from typing import Any

from jusi.domain.models import ExecutableCell
from jusi.plugins import BasePluginRuntimeVdHandler, DisplayHandlerSpec, HandlerContext, MagicCommand

from jusi_opencode.payload import OPENCODE_BOOTSTRAP_BODY, normalize_followup_payload


class OpenCodeHandler(BasePluginRuntimeVdHandler):
    def __init__(self) -> None:
        super().__init__()
        self._payload: dict[str, object] | None = None

    def handler_id(self) -> str:
        return "opencode"

    def handle(self, context: HandlerContext, cell: ExecutableCell) -> str:
        self.stop()
        self._mode = "ready"
        self._entry = cell.main_lines[0] if cell.main_lines else "%%opencode"
        self._payload = {
            "content": context.content,
            "meta": dict(context.meta),
            "cell_id": context.cell_id,
            "client_id": context.client_id,
        }
        context.append_event({"type": "execution_started", "cell_id": context.cell_id, "handler_id": self.handler_id()})
        context.emit_frontend_event(
            "handler_snapshot",
            {
                "handler_id": self.handler_id(),
                "mode": self._mode,
                "entry": self._entry,
                "family": "opencode",
                "target": str(context.meta.get("target", "")).strip(),
            },
        )
        self.prepare_transport(context)
        context.set_status("follow-up")
        context.append_event({"type": "execution_finished", "status": "follow-up", "handler_id": self.handler_id()})
        return "follow-up"

    def terminal_command(self) -> tuple[list[str], str]:
        self._mode = "live"
        return [sys.executable, "-m", "jusi", "plugin-runtime"], ""

    def terminal_env(self) -> dict[str, str]:
        safe = {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "SHELL", "TERM", "TMPDIR", "USER", "VIRTUAL_ENV"}
        env = {key: value for key, value in os.environ.items() if key in safe or key.startswith("JUSI_") or key.startswith("OPENCODE_") or key.startswith("XDG_")}
        env["TERM"] = os.environ.get("JUSI_OPENCODE_TERM", "").strip() or "xterm-256color"
        env["JUSI_PLUGIN_RUNTIME_CALLABLE"] = "jusi_opencode.runner:run_opencode_runner"
        env["JUSI_OPENCODE_PAYLOAD_JSON"] = json.dumps(self._payload or {"content": "", "meta": {}})
        return env

    def normalize_followup_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_followup_payload(payload)

    def stop(self) -> None:
        super().stop()
        self._payload = None


def display_handler_specs() -> tuple[DisplayHandlerSpec, ...]:
    return (
        DisplayHandlerSpec(
            handler_id="opencode",
            factory=OpenCodeHandler,
            magic_commands=(MagicCommand("opencode", bootstrap_body=OPENCODE_BOOTSTRAP_BODY),),
            kernel_extension_modules=("jusi_opencode.kernel",),
            presentation={"syntax": "markdown", "indent": "text", "followup": True, "completion": False},
        ),
    )
