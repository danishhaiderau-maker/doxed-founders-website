#!/usr/bin/env python3
"""Babysit invent until CURRENT with transferable ≤ soft_cap (default 550)."""
import json, re, time, urllib.request
from pathlib import Path

SOFT_CAP = float((__import__("os").environ.get("SOFT_CAP_MIB") or "550").strip() or "550")
token = re.search(
    r"(?m)^BOT_ADMIN_TOKEN=(.+)$",
    Path(r"C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\home-bot.env").read_text(encoding="utf-8"),
).group(1).strip().strip('"').strip("'")
h = {"Accept": "application/json", "X-Bot-Admin-Token": token}
BASE = "https://doxed-btc-bot.fly.dev"


def get(p, timeout=90):
    last = None
    for a in range(6):
        try:
            req = urllib.request.Request(BASE + p, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(5 * (a + 1))
    return {"_err": str(last)}


def main():
    for i in range(360):
        man = get("/api/data-sync/manifest")
        if "_err" in man:
            print(f"t={i} ERR {man['_err']}", flush=True)
            time.sleep(30)
            continue
        w = man.get("inventory_worker") or {}
        gen = (man.get("inventory_generation_id") or "")[:12]
        stt = man.get("inventory_status")
        bs = man.get("inventory_build_status")
        mb = man.get("inventory_transferable_mb")
        if mb is None:
            ds = get("/api/data_size")
            if "_err" not in ds:
                mb = ds.get("inventory_transferable_mb")
        phase = w.get("phase")
        rem = w.get("current_directory_files_remaining")
        pend = w.get("pending_directories")
        boot = (man.get("receipt_bootstrap") or {}).get("status")
        ack = man.get("inventory_ack_eligible")
        auth = man.get("inventory_authoritative")
        print(
            f"t={i} {stt}/{bs} gen={gen} mb={mb} phase={phase} "
            f"seen={w.get('files_seen')} rem={rem} pend={pend} "
            f"pages={w.get('pages_written')}/{w.get('pages_total')} "
            f"ack={ack} auth={auth} boot={boot}",
            flush=True,
        )
        if (
            stt == "CURRENT"
            and mb is not None
            and float(mb) <= SOFT_CAP
            and gen
            and ack
        ):
            print("UNDER_SOFTCAP_CURRENT", flush=True)
            print(
                json.dumps(
                    {
                        "gen": man.get("inventory_generation_id"),
                        "mb": mb,
                        "files": man.get("file_count"),
                        "ack": ack,
                        "auth": auth,
                        "soft_cap": SOFT_CAP,
                    },
                    indent=2,
                ),
                flush=True,
            )
            return 0
        if phase in (
            "FINALIZE",
            "COMPLETE",
            "PERSISTING_POINTER",
            "VALIDATING_INDEX",
            "WAITING_RECEIPT_BOOTSTRAP",
        ):
            print("PHASE", phase, flush=True)
        time.sleep(25)
    print("TIMEOUT", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
