"""Per-tile closed-row counts by pricing model and category (read-only; GET /api/monitor/fills/by-model).

Only ``countable`` rows (paper_fill.row_fresh_strict: epoch, every entry/exit leg BOOK10L, valid, no fallback) may
count toward the 20-per-tile clean count. Everything else is shown in its own category, never merged into it.
"""
from __future__ import annotations

import paper_fill

SCHEMA = "fills_by_model_v1"
CATEGORIES = ("countable", "r17_unproven", "not_fresh_book10l", "pre_epoch", "unstamped", "no_book_exit", "tp1_unpriced_leg",
              "fill_model_invalid", "other_fill_model", "legacy_pricing", "identity_quarantined")

# HM 18 Oct: trades of episodes whose decision and execution minted different policy signatures (B1/B2 TTL 1200 vs 660,
# fixed by #713). FIXED list of concrete trade ids (no pattern, no time cutoff); mirrors the analyzer-side episode
# quarantine (research/legacy_policy_quarantine.py). They are shown here, never counted. Cleared with the next reset.
IDENTITY_QUARANTINED_TRADE_IDS = frozenset({
    "gb1-c50a30ac77d9", "gb2-b5f0bc54af36",   # episode-1ff6cd183c4b011958be (10:54 AEDT)
    "gb1-746896eb658e", "gb2-c78f44f6c178",   # episode-cfacc140c836a55a140c (15:30 AEDT)
})


_TS_KEYS = ("entry_ts", "exit_ts", "ts_epoch", "entry_time_ts")  # same keys paper_fill.row_before_epoch reads


def _has_timestamp(row: dict) -> bool:
    return any(paper_fill._f(row.get(k)) is not None for k in _TS_KEYS)


def classify(row: dict) -> str:
    if not isinstance(row, dict):
        return "legacy_pricing"
    if str(row.get("trade_id") or "") in IDENTITY_QUARANTINED_TRADE_IDS:
        return "identity_quarantined"
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
        # row_before_epoch fails closed for a row with no usable timestamp; that is "unstamped", not "pre-epoch"
        # (the row may well be post-epoch). It is still never countable.
        return "pre_epoch" if _has_timestamp(row) else "unstamped"
    if paper_fill.row_fresh_strict(row):
        return "countable"
    import r17_exclusion as _r17
    return "r17_unproven" if _r17.non_countable_reason(row) else "not_fresh_book10l"


def uses_carried_book(row: dict) -> bool:
    """True when any entry/exit pricing snapshot of the row came from a collector-carried book second."""
    if not isinstance(row, dict):
        return False
    for key in ("entry_provenance", "exit_provenance", "entry_fill_sim", "exit_fill_sim"):
        d = row.get(key)
        if not isinstance(d, dict):
            continue
        for sk in ("depth_snapshot_fill", "depth_snapshot_signal"):
            snap = d.get(sk)
            if isinstance(snap, dict) and snap.get("carried"):
                return True
    for sk in ("exit_depth_snapshot_fill",):
        snap = row.get(sk)
        if isinstance(snap, dict) and snap.get("carried"):
            return True
    return False


def summarise(rows, lanes, *, refused: dict | None = None, now: float | None = None) -> dict:
    tiles = {lane: {"by_category": {c: 0 for c in CATEGORIES}, "by_fill_model": {},
                         "carried_book_rows": 0, "carried_by_fill_model": {},
                         "repriced_exit_rows": 0, "repriced_exit_countable": 0} for lane in lanes}
    for row in rows or ():
        lane = str((row or {}).get("research_lane") or "").upper()
        if lane not in tiles:
            continue
        cat = classify(row)
        t = tiles[lane]
        if cat == "r17_unproven":
            import r17_exclusion as _r17
            for k in str(_r17.non_countable_reason(row) or "").split(":", 1)[-1].split("|"):
                t.setdefault("r17_reason_counts", {})[k] = t.setdefault("r17_reason_counts", {}).get(k, 0) + 1
        t["by_category"][cat] += 1
        model = str(row.get("fill_model") or "NONE")
        t["by_fill_model"][model] = t["by_fill_model"].get(model, 0) + 1
        if row.get("exit_pricing_basis") == "REPRICED_CURRENT_TIME":
            t["repriced_exit_rows"] += 1
            t["repriced_exit_countable"] += 1 if cat == "countable" else 0
        if uses_carried_book(row):
            t["carried_book_rows"] += 1
            t["carried_by_fill_model"][model] = t["carried_by_fill_model"].get(model, 0) + 1
    for t in tiles.values():
        t["countable"] = t["by_category"]["countable"]
        t["rows"] = sum(t["by_category"].values())
        t.setdefault("r17_reason_counts", {})
        n17 = t["by_category"]["r17_unproven"]
        t["r17_label"] = (f"R17_UNPROVEN: {n17} of {t['rows']} rows excluded" if n17 else None)
        t["repriced_exit_share"] = round(t["repriced_exit_rows"] / t["rows"], 4) if t["rows"] else None
        t["carried_book_share"] = round(t["carried_book_rows"] / t["rows"], 4) if t["rows"] else None
    return {"schema": SCHEMA, "generated_at": now, "fresh_epoch_ts": paper_fill.FRESH_EPOCH_TS,
            "rule": "countable = paper_fill.row_fresh_strict (same rule as monitors and analyzers)",
            "non_paper_fills_refused": dict(refused or {}), "tiles": tiles}
