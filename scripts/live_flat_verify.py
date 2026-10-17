"""Exchange-mirror flat verdict for the pre-deploy gate (wait-live-flat-before-deploy.sh).

stdin-free: ``live_flat_verify.py LIFECYCLE_JSON EXCHANGE_HEALTH_JSON`` prints ``<not_flat> <stale_but_flat>``.

An account the website lists only because it is a known Bitfinex instance (fly-view lists every ACTIVE/PAUSED one)
but whose hire has EXPIRED, is paused, unarmed and has no usable credentials cannot hold exchange exposure through us:
its mirror error ``credentials-missing`` is not evidence of an open position. Every other not-ok / not-flat /
position / order / orphan entry still blocks the deploy.
"""
import json
import sys


def ignorable(entry: dict, web_row: dict | None) -> bool:
    if not isinstance(web_row, dict) or entry.get("error") != "credentials-missing":
        return False
    return (web_row.get("hire_active") is False and web_row.get("paused") is True and web_row.get("armed") is False
            and not entry.get("positions") and not entry.get("open_orders") and not entry.get("orphans"))


def verdict(lifecycle: dict, health: dict) -> tuple[int, int]:
    if not isinstance(lifecycle.get("exchange"), dict):
        raise ValueError("lifecycle has no exchange map")  # a failed fetch is never "flat"
    rows = {str(a.get("account")): a for a in ((health.get("website") or {}).get("accounts") or [])
            if isinstance(a, dict)}
    bad = stale = 0
    for name, ex in ((lifecycle.get("exchange") or {}).items()):
        if ignorable(ex, rows.get(str(name))):
            continue
        if not ex.get("ok") or ex.get("flat") is not True or ex.get("open_orders") or ex.get("positions") or ex.get("orphans"):
            bad += 1
        elif (ex.get("read_age_s") or 999) > 90:
            stale += 1
    return bad, stale


def main(argv: list[str]) -> int:
    try:
        with open(argv[1], encoding="utf-8") as a, open(argv[2], encoding="utf-8") as b:
            bad, stale = verdict(json.load(a), json.load(b))
    except Exception:  # noqa: BLE001 - unreadable evidence is never "flat"
        print("ERR ERR")
        return 0
    print(bad, stale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
