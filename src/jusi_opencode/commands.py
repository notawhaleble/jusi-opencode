from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    args: tuple[str, ...] = ()


def parse_slash_command(text: str) -> SlashCommand | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    first_line = stripped.splitlines()[0].strip()
    parts = shlex.split(first_line)
    if not parts:
        return None
    name = parts[0].removeprefix("/").strip()
    return SlashCommand(name=name, args=tuple(parts[1:])) if name else None
