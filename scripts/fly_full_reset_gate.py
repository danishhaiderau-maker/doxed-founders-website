"""Guarded-workflow helpers for the full reset, no archive (read-only except the epoch stamp).

    snapshot   record the live-off facts BEFORE the wipe (/api/build features + /health) to a JSON file
    verify     after the wipe: live facts identical to the snapshot, /api/build still reads live-eligible switch
               ON 0 + DISARMED, force_paper true, and tiles/trades, reconcile, fill-provenance, lanes show 0 rows
    stamp      POST /api/monitor/fresh-start (epoch only moves forward; the bot refuses while live is armed)
    wait-book  wait until the bfx_book_10l stream is writing again (paper is NOT resumed here)

Reads BOT_ADMIN_TOKEN from the environment and never prints it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BOT_BASE_URL", "https://doxed-btc-bot.fly.dev")
SWITCH_RE = re.compile(r"live-eligible switch ON: (\d+)")


def build_facts(build: dict, health: dict) -> dict:
    features = str((build or {}).get("features") or "")
    m = SWITCH_RE.search(features)
    return {"live_eligible_switch_on": int(m.group(1)) if m else None,
            "disarmed": "DISARMED" in features and "ARMED" not in features.replace("DISARMED", ""),
            "force_paper_mode": (health or {}).get("force_paper_mode"),
            "live_armed": (health or {}).get("live_armed"),
            "bitfinex_live_enabled": (health or {}).get("bitfinex_live_enabled")}


def live_failures(facts: dict) -> list[str]:
    bad = []
    if facts.get("live_eligible_switch_on") != 0:
        bad.append(f"live-eligible switch ON = {facts.get('live_eligible_switch_on')!r} (want 0)")
    if facts.get("disarmed") is not True:
        bad.append("/api/build does not read DISARMED")
    if facts.get("force_paper_mode") is not True:
        bad.append(f"force_paper_mode={facts.get('force_paper_mode')!r}")
    if facts.get("live_armed") is not False or facts.get("bitfinex_live_enabled") is not False:
        bad.append("live_armed / bitfinex_live_enabled not both false")
    return bad


def zero_row_failures(tiles_trades: dict, reconcile: dict, provenance: dict, lanes: dict) -> list[str]:
    bad = []
    if (tiles_trades or {}).get("total_matching") != 0:
        bad.append(f"tiles/trades total_matching={(tiles_trades or {}).get('total_matching')!r}")
    for row in (reconcile or {}).get("per_tile") or []:
        if row.get("fly_n") not in (0, None):
            bad.append(f"reconcile {row.get('lane')} fly_n={row.get('fly_n')}")
    if not (provenance or {}).get("per_tile"):
        bad.append("fill-provenance has no per_tile rows")
    for row in (provenance or {}).get("per_tile") or []:
        if row.get("closed") != 0 or row.get("fresh_eligible") != 0:
            bad.append(f"fill-provenance {row.get('lane')} closed={row.get('closed')} fresh={row.get('fresh_eligible')}")
    if not (lanes or {}).get("lanes"):
        bad.append("lanes has no rows")
    for row in (lanes or {}).get("lanes") or []:
        if row.get("closes") != 0:
            bad.append(f"lanes {row.get('lane')} closes={row.get('closes')}")
        for key, value in row.items():
            if "slippage" in key and not key.startswith("n_") and value not in (None,):
                bad.append(f"lanes {row.get('lane')} {key}={value!r} (must be null until measured)")
    return bad


def _get(path: str, *, admin: bool = True, timeout: float = 60.0) -> dict:
    headers = {"Cache-Control": "no-cache"}
    if admin:
        headers["X-Bot-Admin-Token"] = os.environ["BOT_ADMIN_TOKEN"]
    request = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GET {path} -> HTTP {exc.code} (a 5xx here means the empty-file path is broken)")


def _facts() -> dict:
    return build_facts(_get("/api/build", admin=False), _get("/health?fresh=1", admin=False))


def cmd_snapshot(args) -> int:
    facts = _facts()
    print(json.dumps({"snapshot": facts}, sort_keys=True))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(facts, fh, sort_keys=True)
    bad = live_failures(facts)
    if bad:
        raise SystemExit("refusing to wipe, live-off facts are not proven before the wipe: " + "; ".join(bad))
    return 0


def cmd_verify(args) -> int:
    before = json.load(open(args.before, encoding="utf-8"))
    after = _facts()
    print(json.dumps({"before": before, "after": after}, sort_keys=True))
    bad = live_failures(after)
    if after != before:
        bad.append("live facts changed across the wipe")
    bad += zero_row_failures(_get("/api/monitor/tiles/trades"), _get("/api/monitor/reconcile"),
                             _get("/api/monitor/fill-provenance"), _get("/api/monitor/lanes", admin=False))
    if bad:
        raise SystemExit("full reset verification FAILED: " + "; ".join(bad))
    print("full reset verification OK: live off unchanged, 0 rows on tiles/trades, reconcile, fill-provenance, lanes")
    return 0


def cmd_stamp(args) -> int:
    body = json.dumps({"confirm": "SET_FRESH_START_EPOCH", "epoch_ts": time.time() - 1,
                       "note": args.note[:200]}).encode()
    request = urllib.request.Request(BASE + "/api/monitor/fresh-start", data=body, method="POST", headers={
        "X-Bot-Admin-Token": os.environ["BOT_ADMIN_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            doc = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"fresh-start stamp refused: HTTP {exc.code} {exc.read()[:200]!r}")
    print(json.dumps({"stamped": True, "epoch_ts": (doc.get("fresh_start") or {}).get("epoch_ts")}, sort_keys=True))
    return 0


def cmd_wait_book(args) -> int:
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            streams = _get("/api/monitor/streams/health").get("streams") or {}
            book = streams.get("bfx_book_10l") or {}
            if book.get("exists") and book.get("status") == "OK":
                print(json.dumps({"book_stream": "OK", "age_sec": book.get("age_sec")}))
                return 0
        except SystemExit:
            pass
        time.sleep(5)
    raise SystemExit("bfx_book_10l stream did not resume writing (do NOT resume paper)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("verify"); p.add_argument("--before", required=True); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("stamp"); p.add_argument("--note", default="full reset, no archive"); p.set_defaults(fn=cmd_stamp)
    p = sub.add_parser("wait-book"); p.add_argument("--timeout", type=float, default=600.0); p.set_defaults(fn=cmd_wait_book)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
