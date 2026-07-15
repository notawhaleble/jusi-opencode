from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class OpenCodeTarget:
    name: str
    path: Path
    executable: str = "opencode"
    model: str = ""
    variant: str = ""
    agent: str = ""
    auto: bool = False


def opencode_config_from_session(session_config: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(session_config or {}).get("opencode", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def target_config_from_opencode_config(opencode_config: Mapping[str, Any], name: str) -> dict[str, Any]:
    raw = dict(opencode_config).get(name, {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def resolve_target(name: str, target_config: Mapping[str, Any] | None = None) -> OpenCodeTarget:
    raw_config = dict(target_config or {})
    if raw_config:
        target_path = _required_path(raw_config, "path", base=Path.cwd())
        return OpenCodeTarget(
            name=name,
            path=target_path,
            executable=str(raw_config.get("executable", "opencode")).strip() or "opencode",
            model=str(raw_config.get("model", "")).strip(),
            variant=str(raw_config.get("variant", "")).strip(),
            agent=str(raw_config.get("agent", "")).strip(),
            auto=_bool_value(raw_config.get("auto", False)),
        )
    raw_path = Path(os.path.expanduser(name))
    if raw_path.exists() or raw_path.is_absolute() or "/" in name:
        return OpenCodeTarget(name=raw_path.name or "project", path=raw_path.resolve())
    raise KeyError(f"unknown opencode target {name!r}")


def _required_path(raw: Mapping[str, Any], key: str, *, base: Path) -> Path:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise ValueError(f"opencode target is missing required {key!r}")
    return _coerce_path(value, base=base)


def _coerce_path(value: object, *, base: Path) -> Path:
    path = Path(os.path.expanduser(str(value))).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
