"""Where public capture may write. Never mutates an existing data directory."""

from __future__ import annotations

import shutil
from pathlib import Path

# Prefixes match the name and variants such as ext2-run or local-fresh-data-copy.
FORBIDDEN_PREFIXES = (
    "local-fresh-data",
    "ext2",
    "external-data",
    "fly-era",
    "fly-data",
)

APP_TREE = Path("/app")
OUR_TOP_NAMES = frozenset({"raw", "norm", "index.journal", "health.journal"})
_SEGMENT_DIRS = ("raw/book", "raw/trades", "norm/book", "norm/trades")


def _forbidden_component(part: str) -> str | None:
    folded = part.casefold()
    for prefix in FORBIDDEN_PREFIXES:
        if folded == prefix or folded.startswith(prefix + "-") or folded.startswith(prefix):
            return prefix
    return None


def output_refusal(
    path: Path,
    *,
    repo_root: Path | None,
    allow_root: Path | None = None,
    resume: bool = False,
) -> str | None:
    """Return a refusal code, or None when `path` may be used.

    The output must sit inside `allow_root`. A fresh start requires the
    directory to be absent. This function does not create, rename, or delete.
    """
    if not path.is_absolute():
        return "ROOT_NOT_ABSOLUTE"
    resolved = path.resolve()
    for part in resolved.parts:
        hit = _forbidden_component(part)
        if hit:
            return f"FORBIDDEN_OUTPUT:{hit}"
    if resolved == APP_TREE or APP_TREE in resolved.parents:
        return "FORBIDDEN_OUTPUT:app-tree"
    if repo_root is not None:
        repo = repo_root.resolve()
        if resolved == repo or repo in resolved.parents:
            return "FORBIDDEN_OUTPUT:inside-checkout"
    if allow_root is None:
        return "ALLOW_ROOT_REQUIRED"
    allow = Path(allow_root).resolve()
    if resolved == allow or allow not in resolved.parents:
        return "OUTPUT_OUTSIDE_ALLOW_ROOT" if resolved != allow else "OUTPUT_IS_ALLOW_ROOT"
    if not resume and path.exists():
        return "OUTPUT_EXISTS"
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
