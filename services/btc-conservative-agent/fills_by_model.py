"""Per-tile closed-row counts by pricing model and category (read-only; GET /api/monitor/fills/by-model).

Only ``countable`` rows (paper_fill.row_fresh_strict: epoch, every entry/exit leg BOOK10L, valid, no fallback) may
count toward the 20-per-tile clean count. Everything else is shown in its own category, never merged into it.
"""
from __future__ import annotations

import paper_fill

SCHEMA = "fills_by_model_v1"
CATEGORIES = ("countable", "not_fresh_book10l", "pre_epoch", "no_book_exit", "tp1_unpriced_leg",
              "fill_model_invalid", "other_fill_model", "legacy_pricing")


def classify(row: dict) -> str:
    if not isinstance(row, dict):
        return "legacy_pricing"
    if row.get("non_fill_category") == "NO_BOOK_EXIT" or row.get("exit_fallback") == "LEGACY_BBO_FALLBACK_NO_BOOK":
        return "no_book_exit"
    if row.get("non_fill_category") == "TP1_UNPRICED_LEG":
        return "tp1_unpriced_leg"
    if paper_fill.as_bool(row.get("fill_model_invalid"), False):
        return "fill_model_invalid"
    model = str(row.get("fill_model") or "")
    if not model or row.get("fill_model_tag") == paper_fill.LEGACY_TAG or paper_fill.as_bool(row.get("legacy_pricing"), False):
        return "legacy_pricing"
    if model not in paper_fill.ALLOWED_FILL_MODELS:
        return "other_fill_model"
    if paper_fill.row_before_epoch(row):
        return "pre_epoch"
    return "countable" if paper_fill.row_fresh_strict(row) else "not_fresh_book10l"


def summarise(rows, lanes, *, refused: dict | None = None, now: float | None = None) -> dict:
    tiles = {lane: {"by_category": {c: 0 for c in CATEGORIES}, "by_fill_model": {}} for lane in lanes}
    for row in rows or ():
        lane = str((row or {}).get("research_lane") or "").upper()
        if lane not in tiles:
            continue
        cat = classify(row)
        t = tiles[lane]
        t["by_category"][cat] += 1
        model = str(row.get("fill_model") or "NONE")
        t["by_fill_model"][model] = t["by_fill_model"].get(model, 0) + 1
    for t in tiles.values():
        t["countable"] = t["by_category"]["countable"]
        t["rows"] = sum(t["by_category"].values())
    return {"schema": SCHEMA, "generated_at": now, "fresh_epoch_ts": paper_fill.FRESH_EPOCH_TS,
            "rule": "countable = paper_fill.row_fresh_strict (same rule as monitors and analyzers)",
            "non_paper_fills_refused": dict(refused or {}), "tiles": tiles}
