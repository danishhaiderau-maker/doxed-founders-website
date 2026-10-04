"""Read-only monitor payloads for an external monitor (Grokbot).

Pure helpers behind ``/api/monitor/summary``, ``/api/monitor/lanes`` and the
token-gated ``/api/monitor/digest``. Nothing here touches trading, relay or
exchange state; every payload is bounded and carries ``boot_id`` so a monitor
can tell a restart (counters reset) from a stall (counters frozen).

The digest arrives from the laptop watcher push (``scripts/grokbot_digest.py``
already redacts it). Fly does not trust that: it re-applies the same scrub
rules, keeps only known top-level sections, bounds depth/width and caps the
serialized size at ``MAX_DIGEST_BYTES``.
"""
from __future__ import annotations

import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SUMMARY_SCHEMA = "monitor_summary_v1"
LANES_SCHEMA = "monitor_lanes_v1"
DIGEST_SCHEMA = "monitor_digest_v1"
LAPTOP_DIGEST_SCHEMA = "grokbot_digest_v1"

MAX_DIGEST_BYTES = 64 * 1024
MAX_SUMMARY_BYTES = 8 * 1024
MAX_LANES_BYTES = 10 * 1024
DIGEST_STALE_AFTER_SEC = 15 * 60
MIN_MONITOR_TOKEN_LEN = 24

MAX_STR = 240
MAX_ITEMS = 40
MAX_KEYS = 60
MAX_DEPTH = 6

DIGEST_SECTIONS = (
    "schema", "generated_at", "read_only", "sources", "watcher", "selfaware", "uptime", "tiles_24h",
    "tile_verdicts", "analyzer", "decision_readiness", "fees", "capacity", "ai_scorecard",
)

# Same rules as scripts/grokbot_digest.py (parity pinned by test_monitor_api.py).
_PATH_RE = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\)[^\s\"',;)]*")
_SECRET_RE = re.compile(
    r"(?i)\b(?:(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie)\s*[=:]\s*"
    r"(?:bearer\s+)?\S+|bearer\s+\S+)"
)
_LONG_OPAQUE_RE = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_QUERY_RE = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_SECRET_KEY_RE = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie)")


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat().replace("+00:00", "Z")


def scrub(value: Any) -> Any:
    """Truncate and redact one scalar."""
    if not isinstance(value, str):
        return value if isinstance(value, (int, float, bool)) or value is None else scrub(str(value))
    text = _SECRET_RE.sub("<redacted>", value)
    text = _QUERY_RE.sub(r"\1?<redacted>", text)
    text = _PATH_RE.sub("<path>", text)
    text = _LONG_OPAQUE_RE.sub(lambda m: m.group(0)[:12] + "…", text)
    return text if len(text) <= MAX_STR else text[: MAX_STR - 1] + "…"


def _clean(value: Any, depth: int) -> Any:
    if isinstance(value, Mapping):
        if depth >= MAX_DEPTH:
            return None
        out = {}
        for key, item in list(value.items())[:MAX_KEYS]:
            if not isinstance(key, str) or not _KEY_RE.match(key) or _SECRET_KEY_RE.search(key):
                continue
            out[key] = _clean(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if depth >= MAX_DEPTH:
            return None
        return [_clean(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
    if isinstance(value, float) and value != value:
        return None
    return scrub(value)


def _encoded_size(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def _longest_list(node: Any, best: tuple[int, Any] = (1, None)) -> tuple[int, Any]:
    if isinstance(node, dict):
        for item in node.values():
            best = _longest_list(item, best)
    elif isinstance(node, list):
        if len(node) > best[0]:
            best = (len(node), node)
        for item in node:
            best = _longest_list(item, best)
    return best


def fit_to_budget(payload: dict, max_bytes: int) -> dict:
    """Halve the longest list until the payload fits; mark it truncated."""
    truncated = False
    for _ in range(64):
        if _encoded_size(payload) <= max_bytes:
            break
        length, node = _longest_list(payload)
        if node is None:
            break
        del node[length // 2:]
        truncated = True
    if _encoded_size(payload) > max_bytes:
        return {"schema": payload.get("schema"), "truncated": True, "error": "PAYLOAD_OVER_BUDGET",
                "max_bytes": max_bytes}
    if truncated:
        payload["truncated"] = True
    return payload


def sanitize_digest(raw: Any) -> dict | None:
    """Re-sanitize an untrusted laptop digest; None unless it is a grokbot_digest_v1 mapping."""
    if not isinstance(raw, Mapping) or raw.get("schema") != LAPTOP_DIGEST_SCHEMA:
        return None
    digest = {key: _clean(raw[key], 1) for key in DIGEST_SECTIONS if key in raw}
    digest["read_only"] = True
    return fit_to_budget(digest, MAX_DIGEST_BYTES)


def configured_monitor_token(value: str | None, admin_token: str | None) -> str:
    """The usable monitor token, or "" (route disabled) when unset, short, or equal to the admin token."""
    token = (value or "").strip()
    if len(token) < MIN_MONITOR_TOKEN_LEN:
        return ""
    if admin_token and hmac.compare_digest(token, str(admin_token)):
        return ""
    return token


def bearer_matches(authorization: str | None, expected: str) -> bool:
    if not expected or not authorization:
        return False
    scheme, _, presented = str(authorization).strip().partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        return False
    return hmac.compare_digest(presented.strip().encode("utf-8"), expected.encode("utf-8"))


def digest_view(stored: Mapping[str, Any] | None, now: float, boot_id: str | None) -> dict:
    """GET /api/monitor/digest body; staleness uses Fly's receive clock, never the laptop's."""
    stored = stored or {}
    received_ts = stored.get("received_ts")
    age = None if received_ts is None else max(0.0, now - float(received_ts))
    return {
        "schema": DIGEST_SCHEMA,
        "boot_id": boot_id,
        "generated_at": utc_iso(now),
        "received_at": None if received_ts is None else utc_iso(received_ts),
        "age_sec": None if age is None else round(age),
        "stale": age is None or age > DIGEST_STALE_AFTER_SEC,
        "stale_after_sec": DIGEST_STALE_AFTER_SEC,
        "digest": stored.get("digest"),
    }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def trade_bp(row: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """Per-trade return in bp of notional, from the most precise booked field.

    ``pnl_margin_pct`` (the trade row's ``pnl``: net / margin * 100, 2 dp) is
    bp-exact at 100x (bp = pct * 100 / leverage). ``net_pnl_usd`` is booked at
    cent precision, i.e. 4 bp steps on a $25 notional, so it is the fallback.
    """
    pct, lev = row.get("pnl_margin_pct"), row.get("leverage")
    try:
        if pct is not None and lev not in (None, "") and float(lev) > 0:
            value = float(pct) * 100.0 / float(lev)
            if value == value:
                return value, "pnl_margin_pct"
    except (TypeError, ValueError):
        pass
    notional = _num(row.get("notional_usd"))
    if notional > 0 and row.get("net_pnl_usd") is not None:
        return _num(row.get("net_pnl_usd")) / notional * 1e4, "net_pnl_usd"
    return None, None


def lane_stats(rows: Iterable[Mapping[str, Any]]) -> dict:
    """Closed-trade stats for one lane from booked rows (net_pnl_usd as booked, never recomputed)."""
    ordered = sorted(rows, key=lambda r: _num(r.get("close_ts")))
    closes = wins = losses = 0
    net = long_net = short_net = notional = slip_sum = 0.0
    slip_n = 0
    bp_weighted = bp_notional = 0.0
    bp_bases: set[str] = set()
    cumulative = peak = drawdown = 0.0
    last_ts = None
    for row in ordered:
        pnl = _num(row.get("net_pnl_usd"))
        closes += 1
        wins += pnl > 0
        losses += pnl < 0
        net += pnl
        direction = str(row.get("direction") or "").upper()
        if direction == "LONG":
            long_net += pnl
        elif direction == "SHORT":
            short_net += pnl
        row_notional = max(0.0, _num(row.get("notional_usd")))
        notional += row_notional
        bp, basis = trade_bp(row)
        if bp is not None and row_notional > 0:
            bp_weighted += bp * row_notional
            bp_notional += row_notional
            bp_bases.add(basis)
        if row.get("book_slippage_usd") is not None:
            slip_sum += _num(row.get("book_slippage_usd"))
            slip_n += 1
        cumulative += pnl
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        last_ts = row.get("close_ts") or last_ts
    return {
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "net_usd": round(net, 4),
        "long_net_usd": round(long_net, 4),
        "short_net_usd": round(short_net, 4),
        "mean_usd": round(net / closes, 4) if closes else None,
        # Notional-weighted per-trade bp (== net / notional when every row is exact).
        "mean_bp": round(bp_weighted / bp_notional, 2) if bp_notional > 0 else None,
        "mean_bp_basis": (next(iter(bp_bases)) if len(bp_bases) == 1 else "MIXED" if bp_bases else None),
        "max_drawdown_usd": round(drawdown, 4),
        "last_trade_at": utc_iso(last_ts) if last_ts else None,
        "mean_book_slippage_usd": round(slip_sum / slip_n, 4) if slip_n else None,
    }


def latency_brief(latency: Mapping[str, Any] | None) -> dict | None:
    """Signal-to-fill p50/p90/n and status from xvl_signal_latency_v1, if the lane has it."""
    if not isinstance(latency, Mapping):
        return None
    fill = ((latency.get("stages") or {}).get("signal_to_fill") or {})
    return {
        "status": latency.get("status"),
        "signal_to_fill_p50_s": fill.get("p50_s"),
        "signal_to_fill_p90_s": fill.get("p90_s"),
        "n": fill.get("n"),
        "target_s": latency.get("target_median_signal_to_fill_s"),
    }
