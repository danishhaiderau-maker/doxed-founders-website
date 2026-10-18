#!/usr/bin/env python3
"""Pre-deploy gate: flat, live OFF, force_paper, and NO open/pending paper trade on ANY /tiles/trades page.

Fails CLOSED (exit 1, deploy blocked) when: live is armed / force_paper false, any page read errors or loops, the
paging never ends, a non-CLOSED row exists, or the number of open rows seen across all pages does not equal the
monitor's own open count (/api/state lane_position_counts open+pending). 19 Oct: a gate that read only page 1 of the
oldest-first feed missed open trades; this gate walks every page of both status=open and the full feed.

Usage: BOT_ADMIN_TOKEN=... python scripts/deploy_gate_open_trades.py [--base https://doxed-btc-bot.fly.dev]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

MAX_PAGES = 200
# HM 19 Oct: a terminal NON-POSITION status never counts as open, but only when the row provably has no entry fill
# (entry_ts present and null AND fill snapshot present and empty). Any fill, any unknown status or any missing field
# counts as open (fail closed). CLOSED rows are handled as before.
OPEN_STATUSES_EXCLUDED = {"CLOSED"}
TERMINAL_NO_POSITION = {"EXPIRED", "CANCELLED", "CANCELED", "REJECTED", "NO_FILL"}


def _has_entry_fill(row: dict) -> bool:
    fill = row.get("fill")
    if isinstance(fill, dict) and any(fill.get(k) not in (None, "") for k in ("fill_price", "fill_ts", "filled_qty")):
        return True
    return row.get("entry_ts") not in (None, "")


def counts_as_open(row: dict) -> bool:
    status = str(row.get("status")).upper()
    if status in OPEN_STATUSES_EXCLUDED:
        return False
    if status in TERMINAL_NO_POSITION:
        if "entry_ts" not in row:
            return True                       # missing entry field: fail closed
        fill = row.get("fill")
        if fill not in (None, {}) and not isinstance(fill, dict):
            return True                       # unreadable fill snapshot: fail closed
        order = row.get("order")
        if isinstance(order, dict) and str(order.get("terminal") or "").upper() == "FILLED":
            return True                       # order says filled: fail closed
        return _has_entry_fill(row)           # (real EXPIRED rows carry no 'fill' key at all)
    return True                               # OPEN, PENDING, anything unknown


def walk_pages(fetch, path_base: str, page_log: list | None = None) -> list:
    rows, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        path = f"{path_base}{'&cursor=' + cursor if cursor else ''}"
        page = fetch(path)
        if not isinstance(page, dict) or not isinstance(page.get("rows"), list):
            raise ValueError(f"BAD_PAGE {path}")
        rows.extend(page["rows"])
        if page_log is not None:
            n_open = sum(1 for r in page["rows"] if counts_as_open(r))
            page_log.append({"feed": path_base.split("?")[0].rsplit("/", 1)[-1] + ("?open" if "status=open" in path_base else ""),
                             "page": len(page_log) + 1, "rows": len(page["rows"]), "non_closed": n_open})
        cursor = page.get("next_cursor")
        if not cursor:
            if page.get("remaining_after_page") not in (0, None):
                raise ValueError("PAGING_INCOMPLETE remaining_after_page without cursor")
            return rows
        if cursor in seen:
            raise ValueError("CURSOR_LOOP")
        seen.add(cursor)
    raise ValueError("TOO_MANY_PAGES")


def monitor_open_count(state: dict) -> int:
    counts = state.get("lane_position_counts")
    if not isinstance(counts, dict) or not counts:
        raise ValueError("NO_MONITOR_OPEN_COUNT")
    total = 0
    for lane, c in counts.items():
        if not isinstance(c, dict) or not isinstance(c.get("open"), int):
            raise ValueError(f"BAD_LANE_COUNT {lane}")
        total += c["open"] + int(c.get("pending") or 0)
    return total


def evaluate(fetch) -> dict:
    """Returns {"ok": bool, "reasons": [...], ...}. Never raises: every error is a blocking reason."""
    reasons: list = []
    out: dict = {"ok": False, "reasons": reasons}
    try:
        status = fetch("/api/status")
        if status.get("live_armed") is not False:
            reasons.append("LIVE_NOT_OFF")
        if status.get("force_paper_mode") is not True:
            reasons.append("FORCE_PAPER_NOT_TRUE")
        out["git_rev"] = status.get("git_rev")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"STATUS_READ_FAILED:{type(exc).__name__}")
    try:
        page_log: list = []
        out["pages"] = page_log
        open_rows = walk_pages(fetch, "/api/monitor/tiles/trades?status=open&limit=500", page_log)
        all_rows = walk_pages(fetch, "/api/monitor/tiles/trades?limit=500", page_log)
        non_closed = [r for r in all_rows if counts_as_open(r)]
        out.update(pages_open=len(open_rows), rows_total=len(all_rows), non_closed_all_pages=len(non_closed))
        if open_rows or non_closed:
            reasons.append(f"OPEN_TRADES:{max(len(open_rows), len(non_closed))}")
        if len(open_rows) != len(non_closed):
            reasons.append(f"OPEN_VIEWS_DISAGREE:status_open={len(open_rows)} full_feed={len(non_closed)}")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"TRADES_READ_FAILED:{exc}")
        open_rows, non_closed = None, None
    try:
        monitor_n = monitor_open_count(fetch("/api/state"))
        out["monitor_open_count"] = monitor_n
        if non_closed is not None and monitor_n != len(non_closed):
            reasons.append(f"COUNT_MISMATCH:monitor={monitor_n} trades_feed={len(non_closed)}")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"MONITOR_COUNT_FAILED:{exc}")
    out["ok"] = not reasons
    return out


def _http_fetch(base: str, token: str):
    def fetch(path: str):
        req = urllib.request.Request(base + path, headers={"X-Bot-Admin-Token": token})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return fetch


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    base = argv[argv.index("--base") + 1] if "--base" in argv else "https://doxed-btc-bot.fly.dev"
    token = os.environ.get("BOT_ADMIN_TOKEN", "")
    wait = int(argv[argv.index("--wait-seconds") + 1]) if "--wait-seconds" in argv else 0
    deadline = time.monotonic() + wait
    while True:
        result = evaluate(_http_fetch(base, token)) if token else {"ok": False, "reasons": ["NO_TOKEN"]}
        for pg in result.get("pages", []):
            print(f"page feed={pg['feed']} n={pg['page']} rows={pg['rows']} non_closed={pg['non_closed']}", file=sys.stderr)
        print(f"TOTAL non_closed_all_pages={result.get('non_closed_all_pages')} status_open={result.get('pages_open')} "
              f"monitor_open={result.get('monitor_open_count')} ok={result['ok']}", file=sys.stderr)
        print(json.dumps(result, sort_keys=True))
        if result["ok"]:
            return 0
        # Only a plain "positions still open" verdict may be waited out; every other reason (read error, mismatch,
        # live armed, ...) fails closed immediately.
        if wait and all(r.startswith("OPEN_TRADES:") for r in result["reasons"]) and time.monotonic() < deadline:
            time.sleep(30)
            continue
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
