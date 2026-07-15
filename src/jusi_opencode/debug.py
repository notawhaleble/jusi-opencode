from __future__ import annotations

from typing import Any


def emit_debug_event(event: str, **fields: Any) -> None:
    try:
        from jusi.infrastructure.debug_timing import emit_timing
    except Exception:
        return
    emit_timing(f"jusi_opencode.{event}", **fields)
