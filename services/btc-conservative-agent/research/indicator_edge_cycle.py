"""Indicator Edge analyzer cycle (laptop): score -> combination grid -> daily summary -> weekly report -> labels.

Run by scripts/run-genome-grid.ps1 after the genome study (every 2 h; the daily paragraph is appended once per
UTC day, the weekly markdown once per completed week). Read-only on the mirror and the tape; writes only under
the analyzer-exports indicator-edge folder, the pre-registration chain (combination freezes, append-only) and
the weekly diagnostics file. Never touches Fly, trading, tiles, the relay or Bitfinex. It also writes the per-bar
forward labels (``indicator_edge_labels.jsonl``) from the outcomes the scorer already computed, so single-feature
and combination studies can run on one table.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import indicator_edge_combos as combos  # noqa: E402
from research import indicator_edge_labels as labels  # noqa: E402
from research import indicator_edge_prereg as prereg  # noqa: E402
from research import indicator_edge_weekly as weekly  # noqa: E402
from research import indicator_forward_scorer as sc  # noqa: E402


def run(data_dir: str, out_dir: str, prereg_root: Path, diag_dir: Path, *, now: float | None = None,
        tape="load", rows=None) -> dict:
    now = float(now if now is not None else time.time())
    ctx: dict = {}
    report = sc.score(data_dir, out_dir, prereg_root=prereg_root, now=now, tape=tape, rows=rows, context=ctx)
    report["combinations"] = combos.evaluate(report, ctx, prereg_root=prereg_root, out_dir=out_dir, now=now)
    res = sc.write_outputs(report, out_dir)
    try:
        lab = labels.write_from_context(ctx, out_dir)
    except (OSError, ValueError, KeyError) as exc:  # the scoreboard is already written; labels are additive
        lab = {"labels": None, "label_rows": 0, "labels_error": f"{type(exc).__name__}: {exc}"}
    frozen_at = (report.get("prereg") or {}).get("frozen_at")
    week_path = None
    if frozen_at:
        week = weekly.completed_week(report, float(frozen_at), now)
        if week:
            week_path = weekly.write_week(report, out_dir, diag_dir, frozen_at=float(frozen_at), week=week, now=now)
    return {"status": report["status"], "eligible_rows": report["inputs"]["eligible_rows"],
            "scored_days": report["scored_days"], "label_counts": report["label_counts"],
            "combinations": report["combinations"]["status"], "weekly": str(week_path) if week_path else None,
            "compute_sec": report["compute_sec"]} | res | lab


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=sc.DEFAULT_DATA)
    ap.add_argument("--out-dir", default=sc.DEFAULT_OUT)
    ap.add_argument("--prereg-root", default=str(prereg.DEFAULT_ROOT))
    ap.add_argument("--diag-dir", default=str(weekly.REPO_ROOT / "diagnostics"))
    args = ap.parse_args(argv)
    for p in (args.data_dir, args.out_dir, args.prereg_root, args.diag_dir):
        if "\\onedrive\\" in str(p).lower():
            raise SystemExit(f"refusing a OneDrive path: {p}")
    print(json.dumps(run(args.data_dir, args.out_dir, Path(args.prereg_root), Path(args.diag_dir)), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
