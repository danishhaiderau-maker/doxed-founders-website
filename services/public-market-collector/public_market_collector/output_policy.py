"""Where public capture may write. Never mutates an existing data directory."""

from __future__ import annotations

import shutil
from pathlib import Path

# Exact directory-name matches. A new output directory must not sit inside these.
FORBIDDEN_COMPONENTS = (
    "local-fresh-data",
    "ext2",
    "fly-era",
    "fly-data",
)

FLY_DATA_ROOT = Path("/app/data")
OUR_TOP_NAMES = frozenset({"raw", "norm", "index.journal", "health.journal"})
_SEGMENT_DIRS = ("raw/book", "raw/trades", "norm/book", "norm/trades")


def output_refusal(path: Path, *, repo_root: Path | None) -> str | None:
    """Return a refusal code, or None when `path` is a safe new output root.

    This function does not create, rename, or delete anything.
    """
    if not path.is_absolute():
        return "ROOT_NOT_ABSOLUTE"
    resolved = path.resolve()
    folded = {part.casefold() for part in resolved.parts}
    for name in FORBIDDEN_COMPONENTS:
        if name.casefold() in folded:
            return f"FORBIDDEN_OUTPUT:{name}"
    if resolved == FLY_DATA_ROOT or FLY_DATA_ROOT in resolved.parents:
        return "FORBIDDEN_OUTPUT:fly-app-data"
    if repo_root is not None:
        repo = repo_root.resolve()
        if resolved == repo or repo in resolved.parents:
            return "FORBIDDEN_OUTPUT:inside-checkout"
    return None


def foreign_entries(root: Path) -> list[str]:
    """Names that are not this collector's journals or segment files."""
    if not root.exists():
        return []
    bad: list[str] = []
    for child in root.iterdir():
        if child.name not in OUR_TOP_NAMES:
            bad.append(child.name)
    for bucket in ("raw", "norm"):
        directory = root / bucket
        if not directory.exists():
            continue
        if not directory.is_dir():
            bad.append(bucket)
            continue
        for child in directory.iterdir():
            if child.name not in {"book", "trades"}:
                bad.append(f"{bucket}/{child.name}")
    for relative in _SEGMENT_DIRS:
        directory = root / relative
        if not directory.exists():
            continue
        if not directory.is_dir():
            bad.append(relative)
            continue
        for item in directory.iterdir():
            stem = item.name[:-3] if item.name.endswith(".gz") else ""
            plain = "".join(ch for ch in stem if ch != "-")
            if not (item.is_file() and stem and plain.isalnum()):
                bad.append(f"{relative}/{item.name}")
    return sorted(bad)


def disk_free_bytes(path: Path) -> int | None:
    probe = path if path.exists() else path.parent
    if not probe.exists():
        return None
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None
