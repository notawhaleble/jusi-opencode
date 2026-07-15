from __future__ import annotations

from typing import Any


SUPPORTED_MAGIC_PREFIXES = ("%%opencode",)
OPENCODE_BOOTSTRAP_COMMAND = "__jusi_opencode_bootstrap__"
OPENCODE_BOOTSTRAP_BODY = f"/{OPENCODE_BOOTSTRAP_COMMAND}"


def strip_opencode_header(cell_text: str) -> str:
    lines = cell_text.splitlines()
    if lines and any(lines[0].lstrip().startswith(prefix) for prefix in SUPPORTED_MAGIC_PREFIXES):
        return "\n".join(lines[1:]).lstrip("\n")
    return cell_text


def normalize_followup_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["cell_text"] = strip_opencode_header(str(normalized.get("cell_text", "")))
    cell = normalized.get("cell")
    if isinstance(cell, dict):
        normalized_cell = dict(cell)
        main_lines = normalized_cell.get("main_lines")
        if isinstance(main_lines, list) and all(isinstance(item, str) for item in main_lines):
            normalized_cell["main_lines"] = strip_opencode_header("\n".join(main_lines)).splitlines()
        normalized["cell"] = normalized_cell
    return normalized
