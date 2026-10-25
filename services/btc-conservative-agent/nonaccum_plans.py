#!/usr/bin/env python3
"""READ-ONLY plan builders C1-C4 for the non-accumulation clean-up (HM 19 Oct). Nothing here deletes or writes data files.

Each plan: {id, criteria, count, bytes, per_prefix, items_sha256 (sha of the sorted item list incl. size+mtime_ns), sample}.
A later guarded executor must (1) re-run the builder, (2) require the same plan sha, (3) be gated on flat + live OFF +
force_paper true + the all-pages open-trade gate, and (4) delete in rate-limited batches, aborting on RED.

C1 receipts       v3/receipts/emergency_record_idempotency_v1/<7 ledger dirs>/<64-hex>.json, mtime < epoch AND
                  identity.epoch_id not among the epoch ids seen on current-epoch receipts. complete.json, bootstrap.json,
                  round_robin.json, append_heads/ and every non-<64-hex>.json name are protected by construction.
C2 signal_snapshots  v3/signal_snapshots_v1/<64-hex>.json, mtime < epoch, hash not referenced by any current-epoch bundle,
                  the bundle index, lifecycle_membership_v1 receipts or the current ledgers.
C3 tape          tape_1s/*.jsonl.gz mtime < epoch AND older than 168 h (C3b; the transfer-bundle class C3a was retired
                  with transfer bundles, R22 - r5_retention purges that tree).
C4 relay outbox   runtime/relay_outbox_{quarantine,retired}.jsonl LINES created before the epoch (28 Sep events); the files
                  themselves, switch and arming files are never in the plan.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

HEX64 = re.compile(r"^[0-9a-f]{64}\.json$")
HEX64_TOKEN = re.compile(rb"[0-9a-f]{64}")
LEDGERS = ("decision", "execution", "lifecycle", "market_segment", "opportunity", "order_intent", "pre_entry_features")
PROTECTED = {"complete.json", "bootstrap.json", "round_robin.json"}
TAPE_RETENTION_SEC = 168 * 3600


def _sha(items: list) -> str:
    h = hashlib.sha256()
    for it in sorted(items):
        h.update(("\t".join(str(x) for x in it) + "\n").encode())
    return h.hexdigest()


ITEMS: dict = {}   # plan id -> item list of the LAST build (read by the guarded executor; never serialised)


def _plan(pid: str, criteria: str, items: list, prefix_of, extra=None) -> dict:
    ITEMS[pid] = list(items)
    per: dict = {}
    for it in items:
        p = per.setdefault(prefix_of(it), {"count": 0, "bytes": 0})
        p["count"] += 1
        p["bytes"] += int(it[1])
    out = {"id": pid, "criteria": criteria, "count": len(items), "bytes": sum(int(i[1]) for i in items),
           "per_prefix": dict(sorted(per.items())), "items_sha256": _sha(items), "sample": [i[0] for i in sorted(items)[:5]]}
    out.update(extra or {})
    return out


def _scan_tokens(path: str, into: set) -> None:
    try:
        with open(path, "rb") as fh:
            tail = b""
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                into.update(m.decode() for m in HEX64_TOKEN.findall(buf))
                tail = buf[-63:]
    except OSError:
        pass


def current_references(v3: str, epoch_ts: float) -> tuple[set, dict]:
    """64-hex tokens referenced by anything current: bundles written since the epoch, bundle index, membership receipts,
    worker state and the current ledgers."""
    refs: set = set()
    n = {"bundle_files_current": 0, "index_files": 0, "membership_files": 0, "ledger_files": 0}
    b = os.path.join(v3, "lifecycle_bundles")
    for d, _s, names in os.walk(b):
        for f in names:
            p = os.path.join(d, f)
            try:
                if os.stat(p).st_mtime >= epoch_ts:
                    _scan_tokens(p, refs)
                    n["bundle_files_current"] += 1
            except OSError:
                pass
    for sub, key in (("lifecycle_bundle_index", "index_files"), ("lifecycle_worker", "index_files"),
                     ("receipts/lifecycle_membership_v1", "membership_files"), ("ledgers", "ledger_files")):
        for d, _s, names in os.walk(os.path.join(v3, sub)):
            for f in names:
                _scan_tokens(os.path.join(d, f), refs)
                n[key] += 1
    return refs, n


def plan_c1(v3: str, epoch_ts: float) -> dict:
    base = os.path.join(v3, "receipts", "emergency_record_idempotency_v1")
    current_ids: set = set()
    pre: list = []
    skipped = {"protected_or_non_hex_name": 0, "unreadable": 0}
    for led in LEDGERS:  # R3-NON-INPUT: reads idempotency receipt epoch ids for retention planning only, never evidence
        d = os.path.join(base, led)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            if f in PROTECTED or not HEX64.match(f):
                skipped["protected_or_non_hex_name"] += 1
                continue
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
                with open(p, "rb") as fh:
                    eid = (json.loads(fh.read()).get("identity") or {}).get("epoch_id")
            except (OSError, ValueError):
                skipped["unreadable"] += 1
                continue
            if st.st_mtime >= epoch_ts:
                current_ids.add(eid)
            else:
                pre.append((f"{led}/{f}", st.st_size, st.st_mtime_ns, eid))
    items = [(r, s, m) for r, s, m, e in pre if e not in current_ids]
    kept_same_epoch = sum(1 for _r, _s, _m, e in pre if e in current_ids)
    return _plan("C1", "receipts <64hex>.json under the 7 ledger dirs; mtime<epoch AND identity.epoch_id not a current-epoch id",
                 items, lambda it: it[0].split("/")[0],
                 {"current_epoch_ids_seen": sorted(x for x in current_ids if x), "kept_pre_mtime_but_current_epoch_id": kept_same_epoch,
                  "skipped": skipped, "epoch_ts": epoch_ts})


def plan_c2(v3: str, epoch_ts: float, refs: set) -> dict:
    d = os.path.join(v3, "signal_snapshots_v1")
    items, kept_ref, kept_new = [], 0, 0
    for f in os.listdir(d):
        if not HEX64.match(f):
            continue
        p = os.path.join(d, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime >= epoch_ts:
            kept_new += 1
        elif f[:-5] in refs:
            kept_ref += 1
        else:
            items.append((f, st.st_size, st.st_mtime_ns))
    return _plan("C2", "signal_snapshots_v1 <hash>.json mtime<epoch and hash unreferenced by current bundles/index/membership/ledgers",
                 items, lambda it: "signal_snapshots_v1",
                 {"kept_current_epoch_files": kept_new, "kept_pre_epoch_but_referenced": kept_ref, "epoch_ts": epoch_ts})


def plan_c3(root: str, epoch_ts: float, now: float) -> dict:
    tape_dir = os.path.join(root, "tape_1s")
    tape_items, tape_in_window, tape_mtimes = [], 0, []
    for f in sorted(os.listdir(tape_dir)) if os.path.isdir(tape_dir) else []:
        p = os.path.join(tape_dir, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        tape_mtimes.append(st.st_mtime)
        if st.st_mtime < epoch_ts and st.st_mtime < now - TAPE_RETENTION_SEC:
            tape_items.append((f"tape_1s/{f}", st.st_size, st.st_mtime_ns))
        elif st.st_mtime < epoch_ts:
            tape_in_window += 1
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    tape = _plan("C3b", "tape_1s/*.jsonl.gz mtime<epoch AND older than the 168 h tape-retention window", tape_items,
                 lambda it: "tape_1s",
                 {"pre_epoch_still_inside_168h": tape_in_window, "tape_files_total": len(tape_mtimes),
                  "oldest_mtime_utc": iso(min(tape_mtimes)) if tape_mtimes else None,
                  "newest_mtime_utc": iso(max(tape_mtimes)) if tape_mtimes else None,
                  "first_eligible_utc": iso(min(tape_mtimes) + TAPE_RETENTION_SEC) if tape_mtimes else None,
                  "needed_by_pre_registered_test": "NOT VERIFIED by this builder (Strategist must confirm before any tape delete)"})
    return {"C3b": tape}


def plan_c4(root: str, epoch_ts: float) -> dict:
    items, kept, per_file = [], 0, {}
    for name in ("relay_outbox_quarantine.jsonl", "relay_outbox_retired.jsonl"):
        p = os.path.join(root, "runtime", name)
        try:
            lines = open(p, "rb").read().splitlines(keepends=True)
        except OSError:
            continue
        for i, ln in enumerate(lines):
            try:
                ts = float(json.loads(ln).get("created_at_unix"))
            except (ValueError, TypeError, AttributeError):
                kept += 1  # unparseable: never planned
                continue
            if ts < epoch_ts:
                items.append((f"runtime/{name}#{i}", len(ln), hashlib.sha256(ln).hexdigest()))
            else:
                kept += 1
    return _plan("C4", "relay outbox JSONL lines with created_at_unix < epoch (pre-epoch 28 Sep events); files, switch and arming files untouched",
                 items, lambda it: it[0].split("#")[0], {"lines_kept": kept, "epoch_ts": epoch_ts,
                                                         "unacked_check": "NOT EVALUATED here: executor must verify no unacked/pending event for current-epoch intents"})


def build_all(root: str = "/app/data", epoch_ts: float | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    v3 = os.path.join(root, "runtime", "v3")
    if epoch_ts is None:
        raise SystemExit("epoch_ts required")
    t0 = time.time()
    refs, refn = current_references(v3, epoch_ts)
    plans = {"C1": plan_c1(v3, epoch_ts), "C2": plan_c2(v3, epoch_ts, refs), **plan_c3(root, epoch_ts, now),
             "C4": plan_c4(root, epoch_ts)}
    doc = {"schema": "nonaccum_plans_v1", "read_only": True, "epoch_ts": epoch_ts, "built_at": now,
           "reference_scan": {"tokens": len(refs), **refn}, "plans": plans, "elapsed_s": round(time.time() - t0, 1)}
    doc["plan_sha256"] = hashlib.sha256(json.dumps({k: v["items_sha256"] for k, v in plans.items()}, sort_keys=True).encode()).hexdigest()
    return doc


if __name__ == "__main__":
    print(json.dumps(build_all(sys.argv[1] if len(sys.argv) > 1 else "/app/data", float(sys.argv[2]) if len(sys.argv) > 2 else None), sort_keys=True))
