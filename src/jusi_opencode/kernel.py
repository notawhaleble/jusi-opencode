from __future__ import annotations

import argparse
import json
import os
import shlex
from typing import Any

from IPython.core.error import UsageError

from jusi.domain.models import JUSI_HANDLER_HANDOFF_MIME
from jusi.infrastructure.runtime import JUSI_SESSION_CONFIG_ENV
from jusi_opencode.config import opencode_config_from_session, target_config_from_opencode_config


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="%%opencode", add_help=False)
    parser.add_argument("target", nargs="?", default="")
    parser.add_argument("-s", "--session", default="")
    parser.add_argument("-c", "--continue", dest="continue_last", action="store_true")
    parser.add_argument("-e", "--executable", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--variant", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--auto", action="store_true")
    return parser


def load_ipython_extension(ipython: Any) -> None:
    cell_magics = getattr(getattr(ipython, "magics_manager", None), "magics", {}).get("cell", {})
    if "opencode" in cell_magics:
        return

    def _jusi_opencode_magic(line: str, cell: str) -> None:
        from IPython.display import display

        args = _parser().parse_args(shlex.split(line))
        target = str(args.target or "").strip()
        session_config = _session_config_from_env()
        target_config = target_config_from_opencode_config(opencode_config_from_session(session_config), target)
        meta = {
            "line": line,
            "target": target,
            "target_config": target_config,
            "executable": str(args.executable or "").strip(),
            "session": str(args.session or "").strip(),
            "continue_last": bool(args.continue_last),
            "model": str(args.model or "").strip(),
            "variant": str(args.variant or "").strip(),
            "agent": str(args.agent or "").strip(),
            "auto": bool(args.auto),
        }
        payload = {"handler_id": "opencode", "magic_name": "opencode", "content": str(cell or ""), "meta": meta}
        display({JUSI_HANDLER_HANDOFF_MIME: payload}, raw=True, metadata={JUSI_HANDLER_HANDOFF_MIME: meta})

    ipython.register_magic_function(_jusi_opencode_magic, magic_kind="cell", magic_name="opencode")


def _session_config_from_env() -> dict[str, object]:
    raw = os.environ.get(JUSI_SESSION_CONFIG_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
