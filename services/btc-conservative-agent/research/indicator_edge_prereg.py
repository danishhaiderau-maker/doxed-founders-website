"""Indicator Edge pre-registration: a hash-chained, append-only freeze file in the forward tracker.

Every line carries ``prev_sha`` = sha256 of the previous line ("GENESIS" first), the same chain rule the genome
forward tracker and the self-aware contract check use. The feature-set freeze (all 52 indicators, their parameters
and the scoring rules, identified by ``indicator_edge_spec.feature_set_sha()``) must be appended before the first
scored bar; the scorer only scores bars whose ``feature_set_sha`` matches a freeze and whose close is after it.
A parameter change is a new feature set: a new freeze with a fresh clock, never an edit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import indicator_edge_spec as spec  # noqa: E402

SCHEMA = "indicator_edge_prereg_v1"
FROZEN_FILE = "indicator_edge_frozen.jsonl"
FORWARD_TRACKER_FILE = "frozen_candidates.jsonl"
DEFAULT_ROOT = Path(r"C:\DoxxedCrypto\analyzer-exports\genome-grid\forward-tracker")
KIND_FEATURE_SET = "FEATURE_SET"
KIND_COMBINATION = "COMBINATION"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def load_chain(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = Path(path)
    rows, prev, broken = [], "GENESIS", []
    if path.exists():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("prev_sha") != prev:
                broken.append(i)
            prev = _sha(line)
            rows.append(row | {"_line_sha": prev})
    return rows, {"file": str(path), "lines": len(rows), "chain_ok": not broken, "broken_at_lines": broken[:20],
                  "head_sha": prev}


def freeze_intact(row: Mapping[str, Any]) -> bool:
    """The frozen spec document still hashes to its ``feature_set_sha`` (catches an edited head line, which the
    prev_sha chain alone cannot)."""
    doc = row.get("spec_document")
    return isinstance(doc, dict) and _sha(spec.canonical_json(doc)) == row.get("feature_set_sha")


def feature_set_freeze(rows: Sequence[Mapping[str, Any]], feature_set_sha: str | None = None) -> dict | None:
    sha = feature_set_sha or spec.feature_set_sha()
    for row in rows:
        if row.get("kind") == KIND_FEATURE_SET and row.get("feature_set_sha") == sha and freeze_intact(row):
            return dict(row)
    return None


def _append(path: Path, records: Sequence[Mapping[str, Any]], head: str) -> list[str]:
    prev, lines, shas = head, [], []
    for rec in records:
        line = json.dumps(dict(rec) | {"prev_sha": prev}, sort_keys=True, default=str, separators=(",", ":"))
        prev = _sha(line)
        lines.append(line)
        shas.append(prev)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return shas


def freeze_feature_set(root: Path = DEFAULT_ROOT, *, now: float | None = None,
                       code_revision: str | None = None) -> dict[str, Any]:
    """Freeze the current feature set once. Idempotent; refuses a broken chain or an invalid spec."""
    root = Path(root)
    now = float(now if now is not None else time.time())
    path = root / FROZEN_FILE
    problems = spec.validate_spec()
    if problems:
        return {"status": "REFUSED_SPEC_INVALID", "problems": problems}
    rows, chain = load_chain(path)
    if not chain["chain_ok"]:
        return {"status": "REFUSED_CHAIN_BROKEN", "chain": chain}
    sha = spec.feature_set_sha()
    if any(r.get("kind") == KIND_FEATURE_SET and r.get("feature_set_sha") == sha and not freeze_intact(r)
           for r in rows):
        return {"status": "REFUSED_FREEZE_TAMPERED", "chain": chain}
    existing = feature_set_freeze(rows, sha)
    if existing:
        return {"status": "ALREADY_FROZEN", "prereg_id": existing["prereg_id"], "line_sha": existing["_line_sha"],
                "frozen_at_utc": existing.get("frozen_at_utc"), "chain": chain}
    _, anchor = load_chain(root / FORWARD_TRACKER_FILE)
    doc = spec.spec_document()
    rec = {"schema": SCHEMA, "kind": KIND_FEATURE_SET, "prereg_id": f"IE-FS-{sha[:12]}",
           "feature_set_version": spec.FEATURE_SET_VERSION, "feature_set_sha": sha,
           "bar_schema": spec.BAR_SCHEMA, "indicators": len(spec.INDICATORS),
           "feature_ids": len(spec.feature_ids()), "scored_feature_ids": len(spec.scored_feature_ids()),
           "trial_count": spec.trial_count(), "scoring_rules_id": spec.SCORING_RULES["id"],
           "scoring_rules_sha": _sha(spec.canonical_json(spec.SCORING_RULES)),
           "combination_grid_id": spec.COMBINATION_GRID["id"], "spec_document": doc,
           "frozen_at": now, "frozen_at_utc": _utc(now), "code_revision": code_revision,
           "anchor_forward_tracker": {"file": FORWARD_TRACKER_FILE, "lines": anchor["lines"],
                                      "head_sha": anchor["head_sha"], "chain_ok": anchor["chain_ok"]}}
    line_sha = _append(path, [rec], chain["head_sha"])[0]
    return {"status": "FROZEN", "prereg_id": rec["prereg_id"], "line_sha": line_sha,
            "frozen_at_utc": rec["frozen_at_utc"], "feature_set_sha": sha}


def freeze_combinations(root: Path, combos: Sequence[Mapping[str, Any]], *, now: float | None = None,
                        batch_id: str | None = None) -> dict[str, Any]:
    """Freeze week-2 combination variants (each scored forward on fresh data from ``frozen_at``)."""
    root = Path(root)
    now = float(now if now is not None else time.time())
    path = root / FROZEN_FILE
    rows, chain = load_chain(path)
    if not chain["chain_ok"]:
        return {"status": "REFUSED_CHAIN_BROKEN", "chain": chain}
    batch_id = batch_id or time.strftime("IE-C-%Y-%m-%d", time.gmtime(now))
    if any(r.get("batch_id") == batch_id for r in rows):
        return {"status": "ALREADY_FROZEN_TODAY", "batch_id": batch_id, "frozen": 0}
    if not combos:
        return {"status": "NO_COMBINATIONS", "batch_id": batch_id, "frozen": 0}
    recs = [{"schema": SCHEMA, "kind": KIND_COMBINATION, "batch_id": batch_id, "batch_size": len(combos),
             "prereg_id": f"{batch_id}:{c['combo_id']}", "combo_id": c["combo_id"], "family": c["family"],
             "variant": c["variant"], "feature_set_sha": spec.feature_set_sha(), "frozen_at": now,
             "frozen_at_utc": _utc(now), "forward_days": spec.COMBINATION_GRID["forward_days"]} for c in combos]
    _append(path, recs, chain["head_sha"])
    return {"status": "FROZEN", "batch_id": batch_id, "frozen": len(recs)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--freeze", action="store_true", help="append the feature-set freeze (idempotent)")
    ap.add_argument("--code-revision")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if str(root).lower().find("\\onedrive\\") >= 0:
        raise SystemExit("refusing a OneDrive path")
    if args.freeze:
        out = freeze_feature_set(root, code_revision=args.code_revision)
    else:
        rows, chain = load_chain(root / FROZEN_FILE)
        hit = feature_set_freeze(rows)
        out = {"status": "FROZEN" if hit else "NOT_FROZEN", "chain": chain, "feature_set_sha": spec.feature_set_sha(),
               "prereg_id": hit and hit["prereg_id"], "line_sha": hit and hit["_line_sha"]}
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["status"] in ("FROZEN", "ALREADY_FROZEN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
