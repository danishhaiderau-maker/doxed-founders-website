"""Workflow-side execute gates for clean-epoch-reset-plan (read-only).

clean-epoch-reset-execute takes the epoch from fly.toml at the dispatch ref and
refuses outside the 60 min epoch window or without a 6 h volume snapshot. The
plan runs the same checks so a clean plan means execute will not refuse them.

    LIVE=<data_epoch.json + SOURCE_GIT_REV> SNAPSHOTS=<flyctl json> EPOCH=<fly.toml epoch> \
        python scripts/fly_reset_plan_live_gates.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

WINDOW_SEC = 3600
SNAPSHOT_MAX_AGE_SEC = 6 * 3600


def _parse_live(text: str) -> tuple[dict, str]:
    start = text.find("{")
    manifest, end = {}, 0
    if start >= 0:
        try:
            manifest, end = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            manifest = {}
    tail = text[start + end:] if start >= 0 else text
    match = re.search(r"\b[0-9a-f]{40}\b", tail)
    return (manifest if isinstance(manifest, dict) else {}), (match.group(0) if match else "")


def _parse_ts(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def evaluate(live: str, snapshots: list, epoch: str, now: dt.datetime) -> dict:
    manifest, revision = _parse_live(live)
    failures = []
    if manifest.get("epoch_id") != epoch:
        failures.append(f"dispatch-ref fly.toml DATA_EPOCH_ID={epoch} but live data epoch="
                        f"{manifest.get('epoch_id')}: dispatch from the ref whose fly.toml opened the live epoch")
    opened = _parse_ts(manifest.get("started_at_utc"))
    age = (now - opened).total_seconds() if opened else None
    if age is None or age > WINDOW_SEC:
        failures.append(f"epoch window: opened {age} s ago (execute requires <= {WINDOW_SEC} s)")
    fresh = [s for s in snapshots or [] if str(s.get("status") or "").lower() == "created"
             and (ts := _parse_ts(s.get("created_at"))) and (now - ts).total_seconds() < SNAPSHOT_MAX_AGE_SEC]
    if not fresh:
        failures.append("no volume snapshot created within 6 h: run mode=snapshot-volume first")
    if not revision:
        failures.append("live SOURCE_GIT_REV unavailable")
    return {"epoch": manifest.get("epoch_id"), "epoch_age_s": age, "revision": revision,
            "execute_token": f"RESET-AT-BOUNDARY:{epoch}:{revision[:12]}",
            "window_closes_utc": (opened + dt.timedelta(seconds=WINDOW_SEC)).isoformat() if opened else None,
            "execute_gate_failures": failures}


def main() -> int:
    out = evaluate(os.environ["LIVE"], json.loads(os.environ.get("SNAPSHOTS") or "[]"),
                   os.environ["EPOCH"], dt.datetime.now(dt.timezone.utc))
    print(json.dumps(out, sort_keys=True))
    if out["execute_gate_failures"]:
        print("reset plan execute gates failed: " + "; ".join(out["execute_gate_failures"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
