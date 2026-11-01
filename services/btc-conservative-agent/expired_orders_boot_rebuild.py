"""Boot-rebuild in-memory expired tail + durable total from expired_orders_3f.csv (R34).

Binding card seal ``450f82d7c018``. CSV bytes are strictly read-only (R25): never
rewrite, rotate, or dedupe on disk. Dedupe is in memory only. Rebuilt / no-fill
rows are never strategy evidence (R41).

Epoch marker (HM fold a): filter uses the same cutoff as
``bot._current_epoch_boundary()`` — named ``_current_epoch_boundary`` — so a
post-reset rebuild admits only rows at/after the reset-facing boundary.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional

# Named epoch marker the rebuild filters on (must match reset-facing boundary).
EPOCH_MARKER = "_current_epoch_boundary"
EPOCH_TS_FIELDS = ("expired_ts", "time", "created_ts", "ts", "timestamp")

SCHEMA = "expired_orders_boot_rebuild_v1"
STRATEGY_EVIDENCE_ELIGIBLE = False  # R41: rebuilt expired / no-fill never strategy evidence


def _parse_ts(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts if ts > 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime, timezone

        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def row_epoch_ts(row: Mapping[str, Any]) -> Optional[float]:
    for key in EPOCH_TS_FIELDS:
        if key in row:
            ts = _parse_ts(row.get(key))
            if ts is not None:
                return ts
    return None


def is_ai_scan(row: Mapping[str, Any]) -> bool:
    return str(row.get("research_lane") or "").strip().upper() == "AI_SCAN"


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coerce_row_types(row: dict) -> dict:
    out = dict(row)
    for key in ("created_ts", "expired_ts", "limit_price", "signal_price", "age_min",
                "closest_price", "missed_by_usd", "min_price_since_order", "max_price_since_order",
                "conf"):
        if out.get(key) in (None, ""):
            continue
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            pass
    for key in ("touched_limit",):
        if out.get(key) in ("True", "true", "1"):
            out[key] = True
        elif out.get(key) in ("False", "false", "0"):
            out[key] = False
    return out


def read_expired_csv_readonly(path: str) -> dict:
    """Read CSV without mutating bytes. Tolerates a half-written final line as a counted skip."""
    result = {
        "ok": False,
        "status": "RED",
        "total_status": "UNKNOWN",
        "rows": [],
        "skipped_rows": 0,
        "skip_reasons": {},
        "error": None,
        "sha256": None,
        "path": path,
    }
    if not path or not os.path.exists(path):
        result["error"] = "EXPIRED_CSV_MISSING"
        return result
    if not os.path.isfile(path):
        result["error"] = "EXPIRED_CSV_NOT_A_FILE"
        return result
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        result["error"] = f"EXPIRED_CSV_UNREADABLE:{type(exc).__name__}"
        return result
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    if not raw.strip():
        # Present but empty — parseable empty ledger, not missing.
        result.update(ok=True, status="GREEN", total_status="OK", rows=[])
        return result
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin1")
    lines = text.splitlines()
    if not lines:
        result.update(ok=True, status="GREEN", total_status="OK", rows=[])
        return result
    try:
        header = next(csv.reader([lines[0]]))
    except Exception as exc:  # noqa: BLE001 - surface as RED/UNKNOWN
        result["error"] = f"EXPIRED_CSV_HEADER_UNPARSEABLE:{type(exc).__name__}"
        return result
    if not header:
        result["error"] = "EXPIRED_CSV_HEADER_EMPTY"
        return result

    rows: list[dict] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}
    body = lines[1:]

    def _quote_balanced(text: str) -> bool:
        return text.count('"') % 2 == 0

    for index, line in enumerate(body):
        if not line.strip():
            continue
        is_last = index == len(body) - 1
        if is_last and not _quote_balanced(line):
            skipped += 1
            skip_reasons["HALF_WRITTEN_LAST_LINE"] = skip_reasons.get("HALF_WRITTEN_LAST_LINE", 0) + 1
            continue
        try:
            parsed = next(csv.reader([line]))
        except Exception:
            skipped += 1
            reason = "HALF_WRITTEN_LAST_LINE" if is_last else "CSV_PARSE_ERROR"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        if len(parsed) < 1:
            skipped += 1
            skip_reasons["EMPTY_ROW"] = skip_reasons.get("EMPTY_ROW", 0) + 1
            continue
        # Half-written last line: torn tail with fewer than the canonical
        # time/trade_id/dir prefix, or fewer fields than header without a
        # trailing record terminator.
        if is_last and len(parsed) < len(header):
            has_prefix = len(parsed) >= 3 and bool(str(parsed[1] or "").strip())
            looks_torn = (
                len(parsed) < 3
                or not has_prefix
                or (not line.rstrip().endswith(("+", "Z", '"')) and len(parsed) < max(4, len(header) // 2))
            )
            if looks_torn:
                skipped += 1
                skip_reasons["HALF_WRITTEN_LAST_LINE"] = skip_reasons.get("HALF_WRITTEN_LAST_LINE", 0) + 1
                continue
        material = {header[i]: (parsed[i] if i < len(parsed) else "") for i in range(len(header))}
        tid = str(material.get("trade_id") or "").strip()
        if not tid:
            skipped += 1
            skip_reasons["MISSING_TRADE_ID"] = skip_reasons.get("MISSING_TRADE_ID", 0) + 1
            continue
        rows.append(_coerce_row_types(material))

    # Unparseable body with no recovered rows and skips only → RED/UNKNOWN (R36).
    if not rows and skipped and not any(line.strip() for line in body if line):
        result.update(
            ok=False, status="RED", total_status="UNKNOWN",
            skipped_rows=skipped, skip_reasons=skip_reasons,
            error="EXPIRED_CSV_UNPARSEABLE",
        )
        return result

    result.update(
        ok=True,
        status="GREEN" if skipped == 0 else "AMBER",
        total_status="OK",
        rows=rows,
        skipped_rows=skipped,
        skip_reasons=skip_reasons,
    )
    return result


def select_in_scope_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    epoch_cutoff_ts: float | None,
    epoch_marker: str = EPOCH_MARKER,
) -> dict:
    """Dedupe in memory, exclude AI_SCAN, label (do not drop) pre-epoch rows."""
    seen: dict[str, dict] = {}
    ai_scan_excluded: list[str] = []
    epoch_excluded: list[dict] = []
    undated_skipped = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        tid = str(row.get("trade_id") or "").strip()
        if not tid:
            continue
        if is_ai_scan(row):
            ai_scan_excluded.append(tid)
            continue
        ts = row_epoch_ts(row)
        if epoch_cutoff_ts and epoch_cutoff_ts > 0:
            if ts is None:
                undated_skipped += 1
                epoch_excluded.append({
                    "trade_id": tid,
                    "label": "UNDATED_EXCLUDED",
                    "epoch_marker": epoch_marker,
                    "cutoff_ts": float(epoch_cutoff_ts),
                })
                continue
            if ts < float(epoch_cutoff_ts):
                epoch_excluded.append({
                    "trade_id": tid,
                    "label": "PRE_EPOCH_BOUNDARY",
                    "epoch_marker": epoch_marker,
                    "cutoff_ts": float(epoch_cutoff_ts),
                    "row_ts": ts,
                })
                continue
        # Later CSV duplicate wins (append-only ledger); first-seen order preserved via replace.
        row["boot_rebuilt_from_csv"] = True
        row["strategy_evidence_eligible"] = STRATEGY_EVIDENCE_ELIGIBLE
        seen[tid] = row

    ordered = list(seen.values())
    return {
        "in_scope": ordered,
        "unique_in_scope_total": len(ordered),
        "ai_scan_excluded_ids": ai_scan_excluded,
        "epoch_excluded": epoch_excluded,
        "epoch_excluded_count": len(epoch_excluded),
        "undated_skipped": undated_skipped,
        "epoch_marker": epoch_marker,
        "epoch_cutoff_ts": float(epoch_cutoff_ts) if epoch_cutoff_ts else None,
    }


def rebuild_fill_revalidation_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        reason = str(row.get("reason") or "")
        if not reason.startswith("FILL_REVALIDATION"):
            continue
        if is_ai_scan(row):
            continue
        lane = str(row.get("research_lane") or "UNKNOWN").strip().upper() or "UNKNOWN"
        bucket = counts.setdefault(lane, {})
        bucket[reason] = int(bucket.get(reason, 0)) + 1
    return counts


def build_rebuild_receipt(
    *,
    read_result: Mapping[str, Any],
    selected: Mapping[str, Any] | None,
    display_cap: int,
) -> dict:
    """Operator/API receipt for boot rebuild (R34/R36/R40)."""
    if not read_result.get("ok"):
        return {
            "schema": SCHEMA,
            "status": "RED",
            "total_status": "UNKNOWN",
            "expired_orders_total": None,
            "display_tail_cap": int(display_cap),
            "skipped_rows": int(read_result.get("skipped_rows") or 0),
            "skip_reasons": dict(read_result.get("skip_reasons") or {}),
            "epoch_marker": EPOCH_MARKER,
            "epoch_cutoff_ts": None,
            "epoch_excluded_count": 0,
            "epoch_excluded": [],
            "ai_scan_excluded_count": 0,
            "error": read_result.get("error"),
            "csv_sha256": read_result.get("sha256"),
            "strategy_evidence": "never",  # R41
        }
    selected = selected or {}
    skipped = int(read_result.get("skipped_rows") or 0)
    status = "GREEN"
    if skipped:
        status = "AMBER"
    return {
        "schema": SCHEMA,
        "status": status,
        "total_status": "OK",
        "expired_orders_total": int(selected.get("unique_in_scope_total") or 0),
        "display_tail_cap": int(display_cap),
        "skipped_rows": skipped,
        "skip_reasons": dict(read_result.get("skip_reasons") or {}),
        "epoch_marker": selected.get("epoch_marker") or EPOCH_MARKER,
        "epoch_cutoff_ts": selected.get("epoch_cutoff_ts"),
        "epoch_excluded_count": int(selected.get("epoch_excluded_count") or 0),
        "epoch_excluded": list(selected.get("epoch_excluded") or [])[:20],
        "ai_scan_excluded_count": len(selected.get("ai_scan_excluded_ids") or []),
        "error": None,
        "csv_sha256": read_result.get("sha256"),
        "strategy_evidence": "never",
    }


def apply_boot_rebuild(
    *,
    csv_path: str,
    epoch_cutoff_ts: float | None,
    display_cap: int,
    sha_before: Callable[[str], str] | None = None,
) -> dict:
    """Full boot hydrate. Proves CSV sha unchanged (read-only)."""
    sha_fn = sha_before or file_sha256
    before = None
    if os.path.exists(csv_path):
        before = sha_fn(csv_path)
    read_result = read_expired_csv_readonly(csv_path)
    after = file_sha256(csv_path) if os.path.exists(csv_path) else None
    if before is not None and after is not None and before != after:
        raise RuntimeError("EXPIRED_CSV_REWRITE_VIOLATION")  # R25
    selected = None
    fill_counts: dict[str, dict[str, int]] = {}
    display_tail: list[dict] = []
    if read_result.get("ok"):
        selected = select_in_scope_rows(
            read_result["rows"],
            epoch_cutoff_ts=epoch_cutoff_ts,
            epoch_marker=EPOCH_MARKER,
        )
        fill_counts = rebuild_fill_revalidation_counts(selected["in_scope"])
        in_scope = selected["in_scope"]
        display_tail = list(in_scope[-max(0, int(display_cap)):]) if display_cap else []
    receipt = build_rebuild_receipt(
        read_result=read_result, selected=selected, display_cap=display_cap,
    )
    receipt["csv_sha256_before"] = before
    receipt["csv_sha256_after"] = after
    receipt["csv_bytes_unchanged"] = bool(before is not None and before == after)
    return {
        "receipt": receipt,
        "display_tail": display_tail,
        "fill_revalidation_cancel_counts": fill_counts,
        "in_scope_rows": list((selected or {}).get("in_scope") or []),
    }


def assert_not_strategy_evidence(row: Mapping[str, Any]) -> bool:
    """R41 helper: rebuilt expired/no-fill rows must never enter strategy-evidence paths."""
    if row.get("boot_rebuilt_from_csv"):
        return row.get("strategy_evidence_eligible") is False
    return True
