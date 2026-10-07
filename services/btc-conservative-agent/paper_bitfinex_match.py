"""Paper-vs-Bitfinex twin matching and diff (Phase 5 observability).

Pairs each paper trade with its real Bitfinex twin using the stable intent
identity (``intent_id`` first, then ``client_order_id``, then the signed
``policy_signature`` + side/entry-time fallback) and flags any divergence in
entry / exit / price / size / timing.

The module is pure: it takes plain dicts and returns plain dicts. It never
places orders, never reads exchange credentials, and never copies historical
paper state to live.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

SCHEMA = "paper_bitfinex_match_v1"

# Tolerances used for the diff. Anything beyond these is flagged.
PRICE_TOL_PCT = 0.0005   # 5 bp
SIZE_TOL_PCT = 0.005     # 0.5%
TIME_TOL_SEC = 5.0

MATCH_INTENT = "intent_id"
MATCH_CLIENT_ORDER = "client_order_id"
MATCH_SIGNATURE = "policy_signature"
UNMATCHED = "UNMATCHED"


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # reject NaN


def _diff_numeric(label: str, paper: Any, live: Any, tol_pct: float) -> dict | None:
    p = _num(paper)
    l = _num(live)
    if p is None and l is None:
        return None
    if p is None or l is None:
        return {"field": label, "kind": "MISSING_ONE_SIDE", "paper": paper, "live": live}
    if p == 0 and l == 0:
        return None
    base = abs(p) or abs(l) or 1.0
    pct = abs(p - l) / base
    if pct > tol_pct:
        return {"field": label, "kind": "VALUE_DIVERGENCE", "paper": p, "live": l,
                "diff": round(p - l, 8), "diff_pct": round(pct, 8)}
    return None


def _diff_time(label: str, paper: Any, live: Any, tol_sec: float) -> dict | None:
    p = _num(paper)
    l = _num(live)
    if p is None and l is None:
        return None
    if p is None or l is None:
        return {"field": label, "kind": "MISSING_ONE_SIDE", "paper": paper, "live": live}
    if abs(p - l) > tol_sec:
        return {"field": label, "kind": "TIMING_DIVERGENCE", "paper": p, "live": l,
                "diff_sec": round(p - l, 3)}
    return None


def match_paper_to_live(
    paper_trades: Iterable[dict],
    live_twins: Iterable[dict],
) -> list[dict]:
    """Return a list of ``{paper, live, match_key, matched}`` rows.

    ``paper_trades`` are paper intent/trade rows; ``live_twins`` are Bitfinex
    order/fill rows. Matching is by ``intent_id``, then ``client_order_id``,
    then ``policy_signature`` (with side + entry-time proximity).
    """
    live = [dict(t) for t in (live_twins or [])]
    by_intent: dict[str, list[dict]] = {}
    by_client: dict[str, list[dict]] = {}
    by_sig: dict[str, list[dict]] = {}
    for row in live:
        if row.get("intent_id"):
            by_intent.setdefault(str(row["intent_id"]), []).append(row)
        if row.get("client_order_id"):
            by_client.setdefault(str(row["client_order_id"]), []).append(row)
        if row.get("policy_signature"):
            by_sig.setdefault(str(row["policy_signature"]), []).append(row)

    used: set[int] = set()
    out: list[dict] = []
    for paper in (paper_trades or []):
        paper = dict(paper)
        match_key = None
        candidate = None
        candidates: list[dict] = []
        iid = paper.get("intent_id") or paper.get("trade_id")
        cid = paper.get("client_order_id")
        sig = paper.get("policy_signature")
        if iid and iid in by_intent:
            candidates = by_intent[iid]
            match_key = MATCH_INTENT
        elif cid and cid in by_client:
            candidates = by_client[cid]
            match_key = MATCH_CLIENT_ORDER
        elif sig and sig in by_sig:
            candidates = by_sig[sig]
            match_key = MATCH_SIGNATURE

        for cand in candidates:
            if id(cand) not in used:
                candidate = cand
                used.add(id(cand))
                break

        if candidate is None:
            out.append({"paper": paper, "live": None, "match_key": None,
                        "matched": False, "diffs": [{"field": "*", "kind": UNMATCHED}]})
            continue
        out.append({"paper": paper, "live": candidate, "match_key": match_key,
                    "matched": True, "diffs": diff_twin(paper, candidate)})
    return out


def diff_twin(paper: dict, live: dict) -> list[dict]:
    """Flag entry/exit/price/size/timing divergence between a paper row and its live twin."""
    diffs: list[dict] = []
    side_p = (paper.get("side") or paper.get("final_direction") or "").upper()
    side_l = (live.get("side") or "").upper()
    if side_p and side_l and side_p != side_l:
        diffs.append({"field": "side", "kind": "SIDE_MISMATCH", "paper": side_p, "live": side_l})

    for field, tol in (("entry_price", PRICE_TOL_PCT), ("exit_price", PRICE_TOL_PCT),
                       ("price", PRICE_TOL_PCT)):
        d = _diff_numeric(field, paper.get(field), live.get(field), tol)
        if d:
            diffs.append(d)
    d = _diff_numeric("size", paper.get("qty") if paper.get("qty") is not None else paper.get("size"),
                      live.get("qty") if live.get("qty") is not None else live.get("size"), SIZE_TOL_PCT)
    if d:
        diffs.append(d)
    for field in ("entry_ts", "open_ts", "fill_ts"):
        d = _diff_time(field, paper.get(field), live.get(field), TIME_TOL_SEC)
        if d:
            diffs.append(d)
    d = _diff_time("exit_ts", paper.get("exit_ts") or paper.get("close_ts"),
                   live.get("exit_ts") or live.get("close_ts"), TIME_TOL_SEC)
    if d:
        diffs.append(d)
    return diffs


def match_report(paper_trades: Iterable[dict], live_twins: Iterable[dict]) -> dict:
    """Aggregate match + diff summary for an endpoint."""
    matches = match_paper_to_live(paper_trades, live_twins)
    diverged = [m for m in matches if m["matched"] and m["diffs"]]
    unmatched = [m for m in matches if not m["matched"]]
    return {
        "schema": SCHEMA,
        "paper_count": len(matches),
        "matched_count": sum(1 for m in matches if m["matched"]),
        "unmatched_count": len(unmatched),
        "diverged_count": len(diverged),
        "diverged": diverged,
        "unmatched": unmatched,
        "matches": matches,
    }
