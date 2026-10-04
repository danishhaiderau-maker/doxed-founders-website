"""Which shadow-tree files are retired custody copies (Fly deleted them outside custody pruning).

The laptop puller never deletes a shadow-tree file when Fly ships a TOMBSTONE:
the laptop is the long-term custodian, so a file Fly removed stays on disk.
That is right for custody-gated pruning (``research_segment_prune`` deletes a
rotation on Fly only after the laptop proved it holds and analysed it, and
ledgers each deletion in ``retention/prune_ledger.jsonl``): such a file is
still live evidence and stays an analyzer input.

It is wrong for files Fly *retired* on purpose, e.g. the clean-epoch boundary
reset deleting every pre-epoch research file: the laptop kept serving those
bytes to the promotion view, the canonical store and the analyzer as if they
were current (issue #420: 34,937 pre-epoch ``post_exit_replay`` rows, the
retired XVP stream, sealed pre-epoch rotations).

The puller records every applied TOMBSTONE in its state (``tombstoned``:
relpath -> seq) and clears the entry when a later member writes the same path
again (APPEND/REWRITE/SNAPSHOT/BASELINE, or a SEAL into it). A path that is
still tombstoned, still present in the tree and not in the Fly prune ledger is
a *retired custody copy*: kept on disk (never deleted), but excluded from the
promotion view and from the data-compatibility scan.

Pure module: no network, no bot import, safe for laptop tooling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

STATE_KEY = "tombstoned"
PRUNE_LEDGER_RELPATH = "retention/prune_ledger.jsonl"
_WRITE_KINDS = frozenset({"APPEND", "REWRITE", "SNAPSHOT", "BASELINE", "SEAL"})


def update_tombstoned(tombstoned: dict, seq: int, member: Mapping) -> None:
    """Apply one verified manifest member to the ``relpath -> tombstone seq`` map (in place)."""
    kind, path = str(member.get("kind") or ""), member.get("path")
    if not path:
        return
    if kind == "TOMBSTONE":
        tombstoned[str(path)] = int(seq)
    elif kind in _WRITE_KINDS:
        tombstoned.pop(str(path), None)
        if kind == "SEAL" and member.get("source_path"):
            # The sealed head moved to ``path``; its old name no longer exists in the tree.
            tombstoned.pop(str(member["source_path"]), None)


def rebuild_tombstoned(manifests: Iterable[tuple[int, Mapping]]) -> dict:
    """Replay ``(seq, manifest)`` pairs in order (used once to backfill pre-existing shadow trees)."""
    tombstoned: dict = {}
    for seq, manifest in sorted(manifests, key=lambda item: int(item[0])):
        for member in manifest.get("members") or []:
            update_tombstoned(tombstoned, int(seq), member)
    return tombstoned


def pruned_relpaths(tree: Path) -> set[str]:
    """Runtime relpaths Fly deleted by custody-gated pruning (laptop custody; still live evidence)."""
    ledger = Path(tree).joinpath(*PRUNE_LEDGER_RELPATH.split("/"))
    out: set[str] = set()
    try:
        handle = ledger.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return out
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") == "runtime" and row.get("relpath"):
                out.add(str(row["relpath"]).replace("\\", "/"))
    return out


def retired_custody_paths(state: Mapping | None, tree: Path) -> dict[str, int]:
    """``relpath -> tombstone seq`` for tree files Fly retired outside custody pruning."""
    tombstoned = (state or {}).get(STATE_KEY) or {}
    if not isinstance(tombstoned, Mapping) or not tombstoned:
        return {}
    tree = Path(tree)
    pruned = pruned_relpaths(tree)
    out = {}
    for relpath, seq in tombstoned.items():
        rel = str(relpath).replace("\\", "/")
        if rel in pruned or rel == PRUNE_LEDGER_RELPATH:
            continue
        if tree.joinpath(*rel.split("/")).is_file():
            out[rel] = int(seq)
    return dict(sorted(out.items()))


def load_retired_custody_paths(puller_dir: Path, tree: Path) -> dict[str, int]:
    """Same as :func:`retired_custody_paths`, reading ``<puller_dir>/state.json``; ``{}`` if unreadable."""
    try:
        state = json.loads((Path(puller_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return retired_custody_paths(state if isinstance(state, dict) else None, tree)
