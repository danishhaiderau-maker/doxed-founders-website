#!/usr/bin/env python3
"""Pre-deploy gate: flat, live OFF, force_paper, and NO open/pending paper trade on ANY /tiles/trades page.

Fails CLOSED (exit 1, deploy blocked) when: live is armed / force_paper false, any page read errors or loops, the
paging never ends, a non-CLOSED row exists, or the number of open rows seen across all pages does not equal the
monitor's own open count (/api/state lane_position_counts open+pending). 19 Oct: a gate that read only page 1 of the
oldest-first feed missed open trades; this gate walks every page of both status=open and the full feed.

#806 (HM 23 Oct): the gate also reads /api/live-copy/status and /api/monitor/live/exchange-stops (every account in
accounts_seen, with ?account=). Live-surface reasons (LIVE_COPY_OUTPUT_ON, LIVE_ORDERS_ON:<lanes>,
WEBSITE_ARMED:<accounts>, WEBSITE_ARM_READ_FAILED, EXCHANGE_NOT_FLAT:..., *_READ_FAILED / *_MALFORMED / EXCHANGE_STALE)
are NEVER relaxed by --allow-open. Fail-closed rule: an exchange-mirror outage (read failed, mirror not ok, stale read,
no accounts) is only a WARNING when the live-copy status is readable AND output is OFF AND every tile's bitfinex_live_orders is OFF AND nothing is armed (website armed_accounts empty and reachable);
otherwise it blocks. A fresh read that shows a position, a resting order or an orphan always blocks.

Usage: BOT_ADMIN_TOKEN=... python scripts/deploy_gate_open_trades.py [--base https://doxed-btc-bot.fly.dev]
       [--allow-open] [--wait-seconds N] [--dry-run]
--dry-run evaluates and prints the verdict but always exits 0 (never prints the token).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

MAX_PAGES = 200
# HM 19 Oct: a terminal NON-POSITION status never counts as open, but only when the row provably has no entry fill
# (entry_ts present and null AND fill snapshot present and empty). Any fill, any unknown status or any missing field
# counts as open (fail closed). CLOSED rows are handled as before.
OPEN_STATUSES_EXCLUDED = {"CLOSED"}
TERMINAL_NO_POSITION = {"EXPIRED", "CANCELLED", "CANCELED", "REJECTED", "NO_FILL"}


_FILLED_QTY_KEYS = ("filled_qty", "executed_qty", "fill_qty", "cum_qty")
_EXCHANGE_ID_KEYS = ("exchange_order_id", "order_id", "exchange_id", "venue_order_id", "orderId")


def _present(v) -> bool:
    return v not in (None, "", 0, 0.0, {}, [])


def _has_entry_fill(row: dict) -> bool:
    """Any sign of an entry: entry_ts, any fill snapshot content, filled qty, or an exchange order id."""
    if _present(row.get("entry_ts")) or _present(row.get("fill")):
        return True
    if any(_present(row.get(k)) for k in _FILLED_QTY_KEYS + _EXCHANGE_ID_KEYS + ("qty",)):
        return True
    # any fill-like top-level key (fill, fill_price, fill_ts, fill_basis, filled_*...) that is present and non-empty
    if any("fill" in str(k).lower() and _present(v) for k, v in row.items()):
        return True
    order = row.get("order")
    if isinstance(order, dict) and (any(_present(order.get(k)) for k in _FILLED_QTY_KEYS + _EXCHANGE_ID_KEYS)
                                    or str(order.get("terminal") or "").upper() in ("FILLED", "PARTIAL", "PARTIALLY_FILLED")):
        return True
    return False


def counts_as_open(row: dict) -> bool:
    """CLOSED does not count. A status on the TERMINAL_NO_POSITION allowlist does not count ONLY if entry_ts is present
    and null AND no fill snapshot AND no filled qty AND no exchange order id. Everything else counts (working, PENDING,
    OPEN, unknown status, missing field, partial fill, EXPIRED with any fill): fail closed."""
    status = str(row.get("status")).upper()
    if status in OPEN_STATUSES_EXCLUDED:
        return False
    if status in TERMINAL_NO_POSITION:
        if "entry_ts" not in row:
            return True                       # missing entry field
        if isinstance(row.get("fill"), (str, int, float, list)) and row.get("fill") not in (None, "", 0, []):
            return True                       # unreadable fill snapshot (real EXPIRED rows carry no 'fill' key at all)
        return _has_entry_fill(row)
    return True


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


# ---- #806 live surfaces ---------------------------------------------------------------------------------------------
MAX_EXCHANGE_READ_AGE_S = 20.0          # exchange_stops_view.MAX_MIRROR_AGE_SEC
MAX_ACCOUNTS = 50
# Exchange-side reasons that mean "could not see the exchange" (outage), as opposed to "saw it and it is not flat".
EXCHANGE_OUTAGE_PREFIXES = ("EXCHANGE_STOPS_READ_FAILED", "EXCHANGE_MIRROR_NOT_OK", "EXCHANGE_STALE",
                            "EXCHANGE_NOT_FLAT:NO_ACCOUNTS")
UNOBSERVABLE_NOTE = ("NOT VISIBLE FROM FLY: the relay-executor's effective arm and the Railway subscriber cap "
                     "(SUBSCRIBER_MAX_CONCURRENT_SIGNALS) cannot be read from Fly, so a pass does not prove them. "
                     "Runbook: while any account is armed, keep the manual relay-paused read-back before deploying.")


def _num(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def live_copy_reasons(status) -> tuple[list, dict]:
    """Blocking reasons from /api/live-copy/status + a summary. Missing/odd fields are MALFORMED (fail closed)."""
    reasons: list = []
    if not isinstance(status, dict):
        return ["LIVE_COPY_STATUS_MALFORMED:body"], {"readable": False}
    output = status.get("live_copy_output")
    output_on = None
    if not isinstance(output, dict) or not isinstance(output.get("enabled"), bool):
        reasons.append("LIVE_COPY_STATUS_MALFORMED:live_copy_output.enabled")
    else:
        output_on = output["enabled"]
        if output_on:
            reasons.append("LIVE_COPY_OUTPUT_ON")
    source = status.get("live_copy_source_enabled")   # reported only: the HM rule keys on output / tiles / arm
    tiles = status.get("tiles")
    live_lanes: list = []
    if not isinstance(tiles, list):
        reasons.append("LIVE_COPY_STATUS_MALFORMED:tiles")
    else:
        for t in tiles:
            if not isinstance(t, dict) or not isinstance(t.get("bitfinex_live_orders"), (bool, type(None))):
                reasons.append("LIVE_COPY_STATUS_MALFORMED:tiles.bitfinex_live_orders")
                break
            if t.get("bitfinex_live_orders"):
                live_lanes.append(str(t.get("lane")))
        if live_lanes:
            reasons.append("LIVE_ORDERS_ON:" + ",".join(sorted(live_lanes)))
    web = status.get("website")
    armed: list = []
    if not isinstance(web, dict) or not isinstance(web.get("armed_accounts"), list):
        reasons.append("LIVE_COPY_STATUS_MALFORMED:website.armed_accounts")
    else:
        armed = [str(a) for a in web["armed_accounts"]]
        if web.get("reachable") is not True:
            reasons.append("WEBSITE_ARM_READ_FAILED")
        if armed:
            reasons.append("WEBSITE_ARMED:" + ",".join(sorted(armed)))
    summary = {"readable": True, "output_on": output_on, "source_enabled": source, "live_orders_lanes": sorted(live_lanes),
               "website_reachable": (web or {}).get("reachable") if isinstance(web, dict) else None,
               "armed_accounts": sorted(armed)}
    return reasons, summary


def _account_reasons(acct: str, body) -> list:
    if not isinstance(body, dict):
        return [f"EXCHANGE_STOPS_MALFORMED:{acct}:body"]
    if body.get("account") not in (None, acct):
        return [f"EXCHANGE_STOPS_MALFORMED:{acct}:account={body.get('account')}"]
    if body.get("mirror_ok") is not True:
        return [f"EXCHANGE_MIRROR_NOT_OK:{acct}:{body.get('verdict') or 'UNKNOWN'}"]
    age = _num(body.get("read_age_s"))
    if age is None or not isinstance(body.get("fresh"), bool):
        return [f"EXCHANGE_STOPS_MALFORMED:{acct}:read_age_s/fresh"]
    out = []
    if age < 0 or age > MAX_EXCHANGE_READ_AGE_S or body.get("fresh") is not True:
        out.append(f"EXCHANGE_STALE:{acct}:read_age_s={age}")
    positions, orphans, open_orders = body.get("positions"), body.get("orphans"), body.get("open_orders")
    if not isinstance(positions, list) or not isinstance(orphans, list) or isinstance(open_orders, bool) \
            or not isinstance(open_orders, int):
        return out + [f"EXCHANGE_STOPS_MALFORMED:{acct}:positions/orphans/open_orders"]
    verdict = str(body.get("verdict") or "")
    if positions or verdict != "FLAT_NO_STOPS":
        if verdict not in ("STALE_READ",) or positions:
            out.append(f"EXCHANGE_NOT_FLAT:{acct}:{verdict or 'NO_VERDICT'}")
    if open_orders != 0:
        out.append(f"EXCHANGE_NOT_FLAT:{acct}:OPEN_ORDERS={open_orders}")
    if orphans:
        out.append(f"EXCHANGE_NOT_FLAT:{acct}:ORPHANS={len(orphans)}")
    return out


def exchange_reasons(fetch) -> tuple[list, dict]:
    """Every account in accounts_seen must be flat: no positions, no resting non-stop orders, no orphans, fresh read."""
    try:
        first = fetch("/api/monitor/live/exchange-stops")
    except Exception as exc:  # noqa: BLE001
        return [f"EXCHANGE_STOPS_READ_FAILED:{type(exc).__name__}"], {"accounts": []}
    if not isinstance(first, dict) or not isinstance(first.get("accounts_seen"), list):
        return ["EXCHANGE_STOPS_MALFORMED:accounts_seen"], {"accounts": []}
    accounts = [a for a in first["accounts_seen"]]
    if not accounts:
        return ["EXCHANGE_NOT_FLAT:NO_ACCOUNTS"], {"accounts": []}
    if len(accounts) > MAX_ACCOUNTS or not all(isinstance(a, str) and a for a in accounts):
        return ["EXCHANGE_STOPS_MALFORMED:accounts_seen"], {"accounts": []}
    reasons: list = []
    verdicts: dict = {}
    for acct in accounts:
        try:
            body = fetch("/api/monitor/live/exchange-stops?account=" + urllib.parse.quote(acct, safe=""))
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"EXCHANGE_STOPS_READ_FAILED:{acct}:{type(exc).__name__}")
            continue
        verdicts[acct] = body.get("verdict") if isinstance(body, dict) else None
        reasons.extend(_account_reasons(acct, body))
    return reasons, {"accounts": accounts, "verdicts": verdicts}


def live_surface_check(fetch) -> dict:
    """Blocking reasons + warnings for the live surfaces (#806). Never raises."""
    try:
        status = fetch("/api/live-copy/status")
        lc_reasons, lc = live_copy_reasons(status)
    except Exception as exc:  # noqa: BLE001
        lc_reasons, lc = [f"LIVE_COPY_STATUS_READ_FAILED:{type(exc).__name__}"], {"readable": False}
    ex_reasons, ex = exchange_reasons(fetch)
    provably_off = (lc.get("readable") is True and not lc_reasons and lc.get("output_on") is False
                    and not lc.get("live_orders_lanes")
                    and not lc.get("armed_accounts") and lc.get("website_reachable") is True)
    outage = [r for r in ex_reasons if r.startswith(EXCHANGE_OUTAGE_PREFIXES)]
    hard = [r for r in ex_reasons if r not in outage]
    if provably_off:
        blocking, warnings = lc_reasons + hard, [f"WARN_{r}" for r in outage]
    else:
        blocking, warnings = lc_reasons + hard + outage, []
    return {"reasons": blocking, "warnings": warnings, "live_copy": lc, "exchange": ex,
            "live_provably_off": provably_off, "unobservable": UNOBSERVABLE_NOTE}


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
    try:
        live = live_surface_check(fetch)
    except Exception as exc:  # noqa: BLE001 - defensive: live_surface_check never raises
        live = {"reasons": [f"LIVE_SURFACE_CHECK_FAILED:{type(exc).__name__}"], "warnings": [],
                "unobservable": UNOBSERVABLE_NOTE}
    reasons.extend(live["reasons"])
    out.update(warnings=live["warnings"], live_surfaces={k: v for k, v in live.items() if k not in ("reasons", "warnings")},
               unobservable=UNOBSERVABLE_NOTE)
    out["ok"] = not reasons
    return out


ALLOW_OPEN_INFO_PREFIXES = ("OPEN_TRADES:", "OPEN_VIEWS_DISAGREE:", "COUNT_MISMATCH:", "TRADES_READ_FAILED:",
                            "MONITOR_COUNT_FAILED:")


def apply_allow_open(result: dict) -> dict:
    """Paper deploy mode (--allow-open): open paper trades do NOT block (the deploy force-closes them as
    non-countable DEPLOY_MAINTENANCE rows). Live-not-off, force_paper-not-true, an unreadable /api/status and every
    #806 live-surface reason (live copy, tile live orders, website arm, exchange not flat / unreadable) still block."""
    blocking = [r for r in result["reasons"] if not r.startswith(ALLOW_OPEN_INFO_PREFIXES)]
    info = [r for r in result["reasons"] if r.startswith(ALLOW_OPEN_INFO_PREFIXES)]
    return {**result, "reasons": blocking, "info_open_trades_will_be_flattened": info, "ok": not blocking}


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
    allow_open = "--allow-open" in argv
    dry_run = "--dry-run" in argv
    deadline = time.monotonic() + wait
    while True:
        result = evaluate(_http_fetch(base, token)) if token else {"ok": False, "reasons": ["NO_TOKEN"]}
        for pg in result.get("pages", []):
            print(f"page feed={pg['feed']} n={pg['page']} rows={pg['rows']} non_closed={pg['non_closed']} "
                  f"monitor_open_plus_pending={result.get('monitor_open_count')}", file=sys.stderr)
        print(f"TOTAL non_closed_all_pages={result.get('non_closed_all_pages')} status_open={result.get('pages_open')} "
              f"monitor_open={result.get('monitor_open_count')} ok={result['ok']}", file=sys.stderr)
        if allow_open:
            result = apply_allow_open(result)
        for w in result.get("warnings") or []:
            print(f"WARNING {w} (live provably off: exchange-mirror outage does not block a paper deploy)", file=sys.stderr)
        print(UNOBSERVABLE_NOTE, file=sys.stderr)
        if dry_run:
            result = {**result, "dry_run": True}
        print(json.dumps(result, sort_keys=True))
        if result["ok"] or dry_run:
            return 0
        # Only a plain "positions still open" verdict may be waited out; every other reason (read error, mismatch,
        # live armed, ...) fails closed immediately.
        if wait and all(r.startswith("OPEN_TRADES:") for r in result["reasons"]) and time.monotonic() < deadline:
            time.sleep(30)
            continue
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
