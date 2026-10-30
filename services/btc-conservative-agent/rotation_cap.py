"""Bounded rotation cap for rotate_log families (DISK-CAP, 23 Oct 2026).

Pure stdlib. ``rotate_log`` renames ``base`` to ``base.N`` with N = max + 1,
so ``base.1`` is always the OLDEST surviving rotation.

PR2 remaining-deleters kill-switch (R25/R34): ``enforce_cap`` **never deletes**.
At keep-N / byte / age pressure it latches stop-write + 70/85 alarms
(``retention_stop_write`` / ``disk_alert`` AMBER=0.70 RED=0.85) and records
``would_have_dropped`` telemetry. Real deletion is R21-only.

Hard rules encoded here:
* only files matching ``^<base>\\.(\\d+)$`` are ever candidates; the active
  file is never touched;
* PROTECTED bases (v3/**, research_events_v22*, ledgers, receipts,
  quarantine) and CAP_EXEMPT bases are never capped;
* R5: the cap is never gated on custody, export or ack state;
* a failed cap receipt never blocks the stop-write latch.

PRIME RULE: neutral. Retention only; no fill, price or exit input changes.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Iterable

import retention_stop_write

MB = 1024 * 1024
DEFAULT_MAX_ROTATIONS = 10
DEFAULT_MAX_BYTES = 500 * MB
DEFAULT_CAP = (DEFAULT_MAX_ROTATIONS, DEFAULT_MAX_BYTES)
MAX_UNLINKS_PER_CALL = 3  # retained name for API compat; unused (never-delete)

CAP_RECEIPTS_FILE = "cap_receipts.jsonl"
CAP_RECEIPTS_MAX_BYTES = 1 * MB  # one rotation -> at most ~2 MB on disk

# Allow-list: every family passed to rotate_log/_safe_append_jsonl that may be
# capped. The count (10 x 20 MB rotation threshold = 200 MB) is the binding
# cap; the byte bound is a backstop.
_DEFAULT_FAMILIES = (
    "adaptive_entry_decisions.jsonl",
    "ai_confidence_calibration.jsonl",
    "ai_edge_disagreement.jsonl",
    "ai_input_log.jsonl",
    "ai_reason_research.jsonl",
    "ai_shadow_challengers.jsonl",
    "ai_shadow_compact_prompt.jsonl",
    "ai_shadow_regime_prompt.jsonl",
    "approved_but_rejected.jsonl",
    "csv_write_fallback.jsonl",
    "cycle_3m_universe.jsonl",
    "decision_feature_snapshots.jsonl",
    "duplicate_intent_audit.jsonl",
    "dyn_regime_minutes.jsonl",
    "edge_census.jsonl",
    "execution_funnel.jsonl",
    "execution_settings_history.jsonl",
    "fill_markouts.jsonl",
    "fill_quality.jsonl",
    "golden_stack_rejections.jsonl",
    "lane_opportunity_capture.jsonl",
    "market_microstructure_1s.jsonl",
    "near_miss.jsonl",
    "opportunity_capture.jsonl",
    "paper_ioc_no_fill_shadow.jsonl",
    "path_replay.jsonl",
    "reversal_study.jsonl",
    "shadow_lane_outcome.jsonl",
    "shadow_outcome.jsonl",
    "shadow_runner_study.jsonl",
    "shadow_vs_live_entry.jsonl",
    "signal_snapshot.jsonl",
    "soft_reject_shadow.jsonl",
    "source_order_market_evidence.jsonl",
    "taker_signal_counterfactuals.jsonl",
    "trade_lifecycle.jsonl",
    "trade_outcome.jsonl",
    "xvl_shadow_signals.jsonl",
    "xvp_shadow_signals.jsonl",
    "xvs_shadow_signals.jsonl",
    # Retired R17 families: no new rotations, but legacy ones stay bounded.
    "signal_replay.jsonl",
    "post_exit_replay.jsonl",
    "order_multiverse.jsonl",
    "order_multiverse_entry_grid.jsonl",
    "shadow_exit_paths.jsonl",
    "counterfactual.jsonl",
)
CAP_ALLOW: dict[str, tuple[int, int]] = {name: DEFAULT_CAP for name in _DEFAULT_FAMILIES}
# The chase grid loaders read the NEWEST rotation (fixed 23 Oct; they used to
# read ".1", the oldest), so the grid can be capped like everything else.
CAP_ALLOW["chase_offset_touch_grid.jsonl"] = (10, 200 * MB)

# Days-based caps (RK binding conditions, 23 Oct 2026 ~21:12 AEDT).
# market_microstructure_1s is the 1 s tape. Its bid/ask/qty/last are
# TICKER-derived (bot.py ~L29140-29174, source_ts = max(ws_last_tick,
# rest_price_ts)), not verifiable book, so it must never price (R17) a fill,
# stop, TP or exit; only its trade aggregates are real prints. It feeds
# indicators, features and coverage. The 14-day need comes from
# tape_minute_bars (DEFAULT_RETENTION_MINUTES = 14 * 1440): GS-FADE3 prices
# only via the paper_fill book walk, never this tape. Remaining tape-pricing
# consumers (genome_grid_study ~L204, evaluate_limit_fill, side_marks) are
# tracked for retirement in SHARED-TRACKER.
# Rotations younger than ``days`` are kept; older ones go. The byte backstop is
# days x mb_per_day = 14 x 60 MB/day (Strategist's spec, RK 23 Oct ~22:05
# AEDT; the tape grows ~44 MB/day so the age rule normally binds first). Only
# past that backstop does the oldest rotation go regardless of age (R5: a cap
# never waits, and the disk never fills).
# TIED TO THE GS-FADE3-TP30 PREREG SET (frozen 17:37 AEDT 23 Oct): base
# 94d32aa1, AMEND1 rev3 2f41d3d4, AMEND2 rev3 60d175f0, AMEND3 632dcf34
# (/workspace/grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND*-20261023.md).
# When those tests CLOSE or are KILLED, shrink this back to what the remaining
# preregistered tests need (see SHARED-TRACKER).
CAP_DAYS: dict[str, tuple[float, int]] = {
    "market_microstructure_1s.jsonl": (14.0, 60 * MB),
}
for _name in CAP_DAYS:
    CAP_ALLOW[_name] = (10_000, int(CAP_DAYS[_name][0] * CAP_DAYS[_name][1]))

# Classified but never capped.
CAP_EXEMPT: dict[str, str] = {
    "pre_entry_evidence_handoffs.jsonl": "custody handoff evidence (HM)",
    "archive_receipts.jsonl": "archive custody receipts (HM)",
    CAP_RECEIPTS_FILE: "self-capped single rotation at 1 MB",
    "bfx_book_10l_1s.jsonl": "paper_fill book source; path unchanged",
    "lane_pnl_ledger_epoch_receipts.jsonl": "ledger/receipt (protected)",
    "retired_tile_boundary_receipts.jsonl": "receipt (protected)",
    "disloc_trades.jsonl": "DISLOC tile trade ledger",
    "disloc_foi_trades.jsonl": "DISLOC-FOI tile trade ledger",
}
# Suffix-classified exempt families (dynamic paths).
CAP_EXEMPT_SUFFIXES: dict[str, str] = {
    ".malformed_rows.jsonl": "CSV malformed-row quarantine sidecar",
}

_PROTECTED_SUBSTRINGS = ("ledger", "receipt", "quarantine")


def is_protected(path: str) -> bool:
    norm = str(path).replace("\\", "/")
    base = os.path.basename(norm).lower()
    if norm.startswith("v3/") or "/v3/" in norm:
        return True
    if base.startswith("research_events_v22"):
        return True
    return any(token in base for token in _PROTECTED_SUBSTRINGS)


def classify(path: str) -> tuple[str, Any]:
    """Return ("ALLOW", (rot, bytes)) | ("EXEMPT", reason) | ("PROTECTED", reason) | ("UNCLASSIFIED", None)."""
    base = os.path.basename(str(path))
    if base in CAP_EXEMPT:
        return "EXEMPT", CAP_EXEMPT[base]
    for suffix, reason in CAP_EXEMPT_SUFFIXES.items():
        if base.endswith(suffix):
            return "EXEMPT", reason
    if is_protected(path):
        return "PROTECTED", "never capped (v3/ledger/receipt/quarantine/research_events_v22)"
    if base in CAP_ALLOW:
        return "ALLOW", CAP_ALLOW[base]
    return "UNCLASSIFIED", None


def is_classified(path: str) -> bool:
    return classify(path)[0] != "UNCLASSIFIED"


def list_rotations(active_path: str) -> list[tuple[int, str]]:
    """Numbered rotations of ``active_path`` ordered oldest first."""
    directory = os.path.dirname(os.path.abspath(active_path))
    base = os.path.basename(active_path)
    pattern = re.compile(r"^" + re.escape(base) + r"\.(\d+)$")
    found: list[tuple[int, str]] = []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    for name in names:
        match = pattern.match(name)
        if match:
            found.append((int(match.group(1)), os.path.join(directory, name)))
    found.sort()
    return found


def newest_rotation(active_path: str) -> str | None:
    rotations = list_rotations(active_path)
    return rotations[-1][1] if rotations else None


def write_cap_receipt(directory: str, row: dict[str, Any]) -> bool:
    """Append one cap receipt; single rotation at 1 MB; never writes a receipt
    about itself. Returns False on any failure (callers ignore it).

    R4 note (HM C13, accepted risk): two processes (the bot and the segment
    shipper) append to and rotate this file with no shared lock. If both see
    the 1 MB size at once, or one appends between the other's size check and
    os.replace, a receipt row can land in the rotated-away ``.1`` and be
    overwritten by the next rotation. The receipt is an audit trail, not a
    gate: the cap/prune it describes has already happened and its RED (disk
    alert / r5_segment_red) is raised independently. Losing a row at
    rotation is accepted."""
    try:
        path = os.path.join(directory, CAP_RECEIPTS_FILE)
        try:
            if os.path.getsize(path) >= CAP_RECEIPTS_MAX_BYTES:
                os.replace(path, path + ".1")  # overwrite the only rotation
        except FileNotFoundError:
            pass
        line = json.dumps(row, sort_keys=True, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except Exception:  # noqa: BLE001 - a receipt never blocks the cap
        return False


def enforce_cap(
    active_path: str,
    *,
    max_rotations: int | None = None,
    max_bytes: int | None = None,
    max_unlinks: int = MAX_UNLINKS_PER_CALL,
    receipt_writer: Callable[[str, dict[str, Any]], Any] | None = write_cap_receipt,
    now: float | None = None,
) -> dict[str, Any]:
    """Never-delete rotation pressure check. R5: no custody gate; R25: no unlink.

    ``max_unlinks`` is accepted for API compatibility and ignored — this path
    never deletes. Over keep-N / byte / age pressure → stop-write + 70/85 alarm
    and ``would_have_dropped`` telemetry only.
    """
    del max_unlinks  # never-delete; kept for call-site compat
    kind, detail = classify(active_path)
    result: dict[str, Any] = {
        "path": os.path.basename(active_path), "class": kind, "deleted": [],
        "would_have_dropped": [], "stop_write": False, "alarm_level": "GREEN",
    }
    if kind != "ALLOW":
        return result
    cap_rot, cap_bytes = detail
    cap_rot = int(max_rotations if max_rotations is not None else cap_rot)
    cap_bytes = int(max_bytes if max_bytes is not None else cap_bytes)
    days = CAP_DAYS.get(os.path.basename(str(active_path)))
    clock_now = float(now if now is not None else time.time())
    rotations = list_rotations(active_path)
    sizes: dict[str, int] = {}
    mtimes: dict[str, float] = {}
    for _, path in rotations:
        try:
            st = os.stat(path)
            sizes[path], mtimes[path] = st.st_size, st.st_mtime
        except OSError:
            sizes[path], mtimes[path] = 0, 0.0
    total = sum(sizes.values())
    remaining = list(rotations)
    would_drop: list[str] = []
    active_abs = os.path.abspath(active_path)
    # Telemetry only: which victims the old keep-N deleter would have removed.
    probe = list(remaining)
    probe_total = total
    while probe:
        aged_out = bool(days) and clock_now - mtimes.get(probe[0][1], 0.0) > days[0] * 86400
        if len(probe) <= cap_rot and probe_total <= cap_bytes and not aged_out:
            break
        _, victim = probe.pop(0)
        if os.path.abspath(victim) == active_abs:
            continue
        would_drop.append(os.path.basename(victim))
        probe_total -= sizes.get(victim, 0)
    # Binding pressure ratios: rotations vs keep-N and bytes vs byte cap.
    rot_ratio = (len(remaining) / float(cap_rot)) if cap_rot > 0 else None
    byte_ratio = retention_stop_write.used_ratio(total, cap_bytes)
    candidates = [r for r in (rot_ratio, byte_ratio) if r is not None]
    binding_ratio = max(candidates) if candidates else None
    hard_over = bool(would_drop) or (len(remaining) > cap_rot) or (total > cap_bytes)
    family = f"rotation:{result['path']}"
    if hard_over or (binding_ratio is not None and binding_ratio >= retention_stop_write.AMBER_RATIO):
        # Represent pressure as used/cap so 70/85 ladders apply; hard_over forces RED stop.
        pressure_used = 1.0 if hard_over else float(binding_ratio or 0.0)
        decision = retention_stop_write.evaluate_and_latch(
            family,
            used=pressure_used,
            cap=1.0,
            reason="ROTATION_CAP_STOP_WRITE" if hard_over else "ROTATION_CAP_PRESSURE",
            receipt_dir=os.path.dirname(os.path.abspath(active_path)),
            detail={
                "would_have_dropped": would_drop[:50],
                "remaining_rotations": len(remaining),
                "remaining_bytes": total,
                "cap": [cap_rot, cap_bytes],
                "rule": "R25_NEVER_DELETE",
            },
            now=clock_now,
        )
        if hard_over:
            decision["stop_write"] = True
            decision["alarm_level"] = "RED"
            retention_stop_write.latch(
                family, reason="ROTATION_CAP_STOP_WRITE", used=pressure_used, cap=1.0,
                detail={"would_have_dropped": would_drop[:50]}, now=clock_now,
            )
    else:
        decision = {"alarm_level": "GREEN", "stop_write": False, "used_ratio": binding_ratio}
    result.update({
        "remaining_rotations": len(remaining), "remaining_bytes": total,
        "freed_bytes": 0, "backlog": len(remaining) > cap_rot or total > cap_bytes,
        "cap": [cap_rot, cap_bytes],
        "would_have_dropped": would_drop,
        "stop_write": bool(decision.get("stop_write") or hard_over),
        "alarm_level": decision.get("alarm_level") or "GREEN",
        "used_ratio": decision.get("used_ratio"),
        "deleted": [],  # never-delete invariant
    })
    if days:
        result["cap_days"] = days[0]
    if (result["stop_write"] or result["would_have_dropped"] or result["alarm_level"] != "GREEN") and receipt_writer is not None:
        try:
            receipt_writer(os.path.dirname(os.path.abspath(active_path)), {
                "schema": "rotation_cap_receipt_v1",
                "ts": clock_now,
                "family": result["path"],
                "deleted": [],
                "would_have_dropped": would_drop[:50],
                "freed_bytes": 0, "cap": result["cap"],
                "remaining_rotations": len(remaining),
                "stop_write": result["stop_write"],
                "alarm_level": result["alarm_level"],
                "used_ratio": result.get("used_ratio"),
                "rule": "R25_NEVER_DELETE",
            })
        except Exception:  # noqa: BLE001
            pass
    return result


def sweep(root: str, *, families: Iterable[str] | None = None,
          max_unlinks: int = MAX_UNLINKS_PER_CALL) -> list[dict[str, Any]]:
    """Background pass over every allow-listed family under ``root`` (never deletes)."""
    results = []
    for base in sorted(families if families is not None else CAP_ALLOW):
        res = enforce_cap(os.path.join(root, base), max_unlinks=max_unlinks)
        if res.get("would_have_dropped") or res.get("stop_write") or res.get("errors") or (
                res.get("alarm_level") not in (None, "GREEN")):
            results.append(res)
    return results


def start_sweeper(root_fn: Callable[[], str], stop_event: threading.Event,
                  *, interval_sec: float = 300.0,
                  logger: Any = None) -> threading.Thread:
    def _loop() -> None:
        while not stop_event.wait(interval_sec):
            try:
                for res in sweep(root_fn()):
                    if logger is not None:
                        logger.info("[ROTATION CAP] %s", res)
            except Exception as exc:  # noqa: BLE001
                if logger is not None:
                    logger.error("[ROTATION CAP] sweep failed: %s", exc)
    thread = threading.Thread(target=_loop, name="rotation-cap-sweeper", daemon=True)
    thread.start()
    return thread
