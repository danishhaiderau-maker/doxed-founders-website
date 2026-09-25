"""Windows long-path I/O without changing canonical receipt identities.

Callers still perform confinement and reparse checks on logical paths. This
adapter only changes the spelling passed to filesystem APIs, never the scope.
"""
from __future__ import annotations

import os
from pathlib import Path


def logical_path(path) -> Path:
    value = os.fspath(path)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        if value.startswith("\\\\.\\"):
            raise ValueError("device namespace is not a research filesystem path")
    return Path(value)


def io_path(path) -> Path:
    path = logical_path(path)
    if os.name != "nt":
        return path
    value = os.path.abspath(os.fspath(path))
    # Preserve ordinary Win32 spelling/semantics for existing short paths.
    # 248 also covers the older directory-creation limit, not just MAX_PATH.
    if len(value) < 248:
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def resolved_path(path, *, strict=False) -> Path:
    return logical_path(io_path(path).resolve(strict=strict))
