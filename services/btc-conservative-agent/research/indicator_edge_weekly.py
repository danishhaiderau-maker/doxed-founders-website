"""Weekly Indicator Edge report: renders diagnostics/INDICATOR-EDGE-WEEK-<n>.md from the scorer report.

Week n covers forward-scoring days [7(n-1), 7n) after the feature-set freeze. The cycle writes a week once it has
ended (never overwrites); ``--week`` / ``--force`` render on demand.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import indicator_forward_scorer as sc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "diagnostics" / "INDICATOR-EDGE-WEEK-TEMPLATE.md"
WEEK_SEC = 7 * 86400


def week_number(frozen_at: float, now: float) -> int:
    return int(max(0.0, now - frozen_at) // WEEK_SEC) + 1


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:+.2f}" if abs(v) < 1000 else f"{v:.0f}"
    return str(v).replace("|", "/")


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "_none_"
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def render(report: Mapping[str, Any], week: int, daily: Sequence[Mapping[str, Any]], *,
           template: str | None = None, now: float | None = None) -> str:
    now = float(now if now is not None else time.time())
    pre = report.get("prereg") or {}
    inv = report.get("status_inventory") or {}
    by_status: dict[str, list] = {}
    for ind in inv.get("indicators") or []:
        by_status.setdefault(ind["status"], []).append(f"{ind['num']} {ind['id']}")
    availability = "\n".join(f"- **{k}** ({len(v)}): {', '.join(v)}" for k, v in sorted(by_status.items())) or "_none_"
    ranking = _table(
        ["#", "Feature", "Family", "Label", "Window", "Signals/day", "Hit", "Net bp", "9 s net", "Rank IC",
         "Top-bottom bp", "q", "Best regime", "Independent"],
        [[i, f["feature"], f["family"], f["label"], f"+{f['best_window_min']}m", f.get("signals_per_day"),
          f["best"].get("hit_rate"), f["best"].get("mean_net_bp"), f["best"].get("mean_net_bp_9s"),
          f["best"].get("rank_ic"), f["best"].get("top_bottom_spread_bp"), f["best"].get("q_value"),
          f.get("best_regime"), "yes" if f.get("independent") else f"-> {f.get('cluster_rep')}"]
         for i, f in enumerate(report.get("features") or [], 1)])
    cl = report.get("clusters") or {}
    clusters = _table(["Representative", "Members"],
                      [[c["representative"], ", ".join(m for m in c["members"] if m != c["representative"])]
                       for c in cl.get("clusters") or []])
    combos = report.get("combinations") or {}
    if combos.get("rows"):
        combo_txt = _table(["Combo", "Family", "Label", "Fwd days", "Signals", "Net bp", "q"],
                           [[r["combo_id"], r["family"], r["label"], r["forward_days"], r["stats"]["signals"],
                             r["stats"]["mean_net_bp"], r["stats"]["q_value"]] for r in combos["rows"]])
    else:
        combo_txt = f"{combos.get('status', 'GATED')}: {combos.get('gate_reason', 'not evaluated')}"
    if combos.get("tile_proposals"):
        combo_txt += "\n\nTile-package proposals (not registered): " + ", ".join(combos["tile_proposals"])
    daily_txt = "\n".join(f"- **{d['date']}**: {d['paragraph']}" for d in daily) or "_none_"
    period = (f"{daily[0]['date']} to {daily[-1]['date']}" if daily else "no scored days")
    summary = (report.get("daily_summary") or {}).get("paragraph") or "No summary yet."
    fill = {"week": week, "period": period, "generated_at": sc._utc(now), "plain_english": summary,
            "prereg_id": pre.get("prereg_id"), "line_sha": pre.get("line_sha"), "frozen_at": pre.get("frozen_at_utc"),
            "feature_set_version": report.get("feature_set_version"), "feature_set_sha": report.get("feature_set_sha"),
            "trial_count": report.get("trial_count"), "scored_days": report.get("scored_days"),
            "eligible_rows": (report.get("inputs") or {}).get("eligible_rows"),
            "label_counts": ", ".join(f"{k} {v}" for k, v in (report.get("label_counts") or {}).items()),
            "availability": availability, "ranking": ranking, "clusters": clusters, "combinations": combo_txt,
            "daily": daily_txt}
    text = template if template is not None else TEMPLATE.read_text(encoding="utf-8")
    for key, value in fill.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def daily_entries(out_dir: str, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
    path = Path(out_dir) / sc.DAILY_FILE
    if not path.exists():
        return []
    lo, hi = sc._day(start_ts), sc._day(end_ts - 1)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if lo <= d.get("date", "") <= hi:
            out.append(d)
    return out


def write_week(report: Mapping[str, Any], out_dir: str, diag_dir: Path, *, frozen_at: float, week: int,
               now: float | None = None, force: bool = False) -> Path | None:
    path = Path(diag_dir) / f"INDICATOR-EDGE-WEEK-{week}.md"
    if path.exists() and not force:
        return None
    start = frozen_at + (week - 1) * WEEK_SEC
    text = render(report, week, daily_entries(out_dir, start, start + WEEK_SEC), now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def completed_week(report: Mapping[str, Any], frozen_at: float, now: float) -> int | None:
    """The most recent fully elapsed week, if any."""
    n = week_number(frozen_at, now) - 1
    return n if n >= 1 and report.get("status") == "OK" else None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=sc.DEFAULT_OUT)
    ap.add_argument("--diag-dir", default=str(REPO_ROOT / "diagnostics"))
    ap.add_argument("--week", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    report = json.loads((Path(args.out_dir) / sc.REPORT_FILE).read_text(encoding="utf-8"))
    pre = report.get("prereg") or {}
    if not pre.get("frozen_at"):
        raise SystemExit("not pre-registered yet")
    frozen_at = float(pre["frozen_at"])
    week = args.week or week_number(frozen_at, time.time())
    path = write_week(report, args.out_dir, Path(args.diag_dir), frozen_at=frozen_at, week=week, force=args.force)
    print(json.dumps({"week": week, "written": str(path) if path else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
