#!/usr/bin/env python3
"""GUARDED executor for the C1-C4 non-accumulation clean-up (HM 19 Oct). DRY-RUN BY DEFAULT.

    python3 nonaccum_execute.py ROOT EPOCH_TS --expected-sha <sha> [--classes C1,C2,C4] [--execute --confirm-sha <sha>]

Rules (all enforced in code, covered by tests):
  * re-builds the plans (nonaccum_plans.build_all) and REQUIRES plan_sha256 == --expected-sha. A different sha is accepted only
    with --expected-counts-json and only if every class has the same count or fewer; the new sha is logged loudly (tell HM);
  * gate (incl. API latency <= 5 s on /api/status) before the first batch and after every batch: live_armed false, force_paper_mode true, bot RSS under a ceiling,
    and no watched monitor check worse than its baseline (a RED that already existed at the start must be accepted explicitly
    with --accept-baseline-red NAME; any NEW or worsened check aborts);
  * C1 touches only <64hex>.json under the 7 ledger dirs with mtime < epoch; protected names can never be unlinked;
    every file is re-stat'ed right before unlink (same size + mtime_ns as planned, mtime < epoch, inside ROOT), else skipped;
  * current-epoch lifecycle bundle dirs are counted before/after and must not shrink; C3b (tape) is never executed
    (HM 19 Oct; no --include-tape flag); the transfer-bundle class C3a was retired with transfer bundles (R22);
  * C4 rewrites the two relay-outbox JSONL files atomically, dropping ONLY planned pre-epoch lines (matched by content sha),
    refusing if the relay outbox shows any pending event or the file changed between read and replace;
  * rate limited batches (--batch files, --sleep seconds); abort on the first failed gate; no archive/copy of anything;
  * logs removed counts + bytes per class (JSONL log + final summary).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
import urllib.request

import nonaccum_plans as P

WATCHED_CHECKS = ("exits-latency-guard", "exit-truth", "tape-retention", "data-completeness", "tile-integrity")
_RANK = {"GREEN": 0, "INFO": 0, "AMBER": 1, "UNKNOWN": 1, "RED": 2}
RSS_CEILING_BYTES = 2 * 1024 ** 3
RSS_GROWTH_ABORT_BYTES = 400 * 1024 ** 2
API_LATENCY_ABORT_S = 5.0   # /api/status must answer within this at every gate, else abort
CLASS_ORDER = ("C1", "C2", "C3b", "C4")


class Abort(Exception):
    pass


def http_json(base: str, token: str):
    def get(path: str):
        req = urllib.request.Request(base + path, headers={"X-Bot-Admin-Token": token})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as exc:   # /api/monitor/checks answers 503 when something is RED but still has a body
            return json.loads(exc.read() or b"{}")
    return get


def gate(get, baseline: dict | None, accepted_red: set) -> dict:
    """Return the snapshot or raise Abort. baseline=None records the first snapshot."""
    t0 = time.monotonic()
    st = get("/api/status")
    latency = round(time.monotonic() - t0, 3)
    if latency > API_LATENCY_ABORT_S:
        raise Abort(f"API latency {latency}s over {API_LATENCY_ABORT_S}s")
    if st.get("live_armed") is not False:
        raise Abort(f"live_armed is {st.get('live_armed')!r} (must be false)")
    if st.get("force_paper_mode") is not True:
        raise Abort(f"force_paper_mode is {st.get('force_paper_mode')!r} (must be true)")
    rss = int((((st.get("runtime_telemetry") or {}).get("latest") or {}).get("memory") or {}).get("rss_bytes") or 0)
    if not rss:
        raise Abort("no RSS sample")
    ro = st.get("relay_outbox") or {}
    checks = (get("/api/monitor/checks").get("checks") or {})
    snap = {"rss": rss, "relay_pending": ro.get("pending_total"), "api_latency_s": latency,
            "checks": {n: str((checks.get(n) or {}).get("status") or "UNKNOWN").upper() for n in WATCHED_CHECKS}}
    if rss > RSS_CEILING_BYTES:
        raise Abort(f"RSS {rss} over ceiling")
    if baseline is not None:
        if rss > baseline["rss"] + RSS_GROWTH_ABORT_BYTES:
            raise Abort(f"RSS grew {rss - baseline['rss']} bytes since start")
    for n, s in snap["checks"].items():
        base = (baseline or {}).get("checks", {}).get(n)
        if s == "RED" and n not in accepted_red:
            raise Abort(f"check {n} is RED (not an accepted baseline RED)")
        if baseline is not None and base is not None and _RANK.get(s, 1) > _RANK.get(base, 1) and s == "RED":
            raise Abort(f"check {n} worsened {base} -> {s}")
    return snap


def _safe_join(root: str, rel: str) -> str:
    p = os.path.realpath(os.path.join(root, rel))
    if not p.startswith(os.path.realpath(root) + os.sep):
        raise Abort(f"path escapes root: {rel}")
    return p


def _file_targets(cls: str, root: str, items: list) -> list:
    v3 = os.path.join(root, "runtime", "v3")
    out = []
    for rel, size, mtime_ns in items:
        if cls == "C1":
            path = os.path.join(v3, "receipts", "emergency_record_idempotency_v1", rel)
            led, name = rel.split("/", 1)
            if led not in P.LEDGERS or name in P.PROTECTED or not P.HEX64.match(name) or "/" in name:
                raise Abort(f"C1 item outside the allowed set: {rel}")
        elif cls == "C2":
            path = os.path.join(v3, "signal_snapshots_v1", rel)
            if not P.HEX64.match(rel):
                raise Abort(f"C2 item outside the allowed set: {rel}")
        elif cls == "C3b":
            path = os.path.join(root, rel)
            if not rel.startswith("tape_1s/") or not rel.endswith(".jsonl.gz"):
                raise Abort(f"C3b item outside the allowed set: {rel}")
        else:
            raise Abort(f"not a file class: {cls}")
        out.append((path, int(size), int(mtime_ns)))
    return out


def _class_dir(cls: str, root: str) -> str:
    v3 = os.path.join(root, "runtime", "v3")
    return {"C1": os.path.join(v3, "receipts", "emergency_record_idempotency_v1"), "C2": os.path.join(v3, "signal_snapshots_v1"),
            "C3b": os.path.join(root, "tape_1s")}[cls]


def _path_ok(class_dir: str, path: str) -> bool:
    """realpath must equal the plain path and stay under the class directory; no path component below ROOT may be a symlink."""
    cd = os.path.abspath(class_dir)
    ap = os.path.abspath(path)
    if not ap.startswith(cd + os.sep) or os.path.realpath(ap) != ap and os.path.realpath(cd) + ap[len(cd):] != os.path.realpath(ap):
        return False
    cur = cd
    if os.path.islink(cd):
        return False
    for part in ap[len(cd) + 1:].split(os.sep):
        cur = os.path.join(cur, part)
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return False
        except OSError:
            return False
    return True


def current_bundle_dirs(root: str, epoch_ts: float) -> int:
    b = os.path.join(root, "runtime", "v3", "lifecycle_bundles")
    n = 0
    for shard in os.listdir(b) if os.path.isdir(b) else []:
        sd = os.path.join(b, shard)
        for tr in os.listdir(sd) if os.path.isdir(sd) else []:
            newest = 0.0
            for d, _s, names in os.walk(os.path.join(sd, tr)):
                for f in names:
                    try:
                        newest = max(newest, os.stat(os.path.join(d, f)).st_mtime)
                    except OSError:
                        pass
            n += 1 if newest >= epoch_ts else 0
    return n


def delete_files(cls, root, epoch_ts, items, execute, batch, sleep_s, check, log, totals):
    targets = _file_targets(cls, root, items)
    rr = os.path.realpath(root)
    cdir = _class_dir(cls, root)
    for i in range(0, len(targets), batch):
        removed = skipped = nbytes = 0
        for path, size, mtime_ns in targets[i:i + batch]:
            rp = os.path.realpath(path)
            if not rp.startswith(rr + os.sep):
                raise Abort(f"path escapes root: {path}")
            if not _path_ok(cdir, path):
                raise Abort(f"path outside class dir or has a symlinked component: {path}")
            try:
                st = os.lstat(path)
            except OSError:
                skipped += 1
                continue
            if st.st_size != size or st.st_mtime_ns != mtime_ns or st.st_mtime >= epoch_ts or os.path.islink(path):
                skipped += 1      # changed since planning: never delete
                continue
            if execute:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    skipped += 1
                    continue
                except OSError as exc:
                    raise Abort(f"unlink failed for {path}: {exc}")
            removed += 1
            nbytes += size
        totals[cls]["removed"] += removed
        totals[cls]["bytes"] += nbytes
        totals[cls]["skipped_changed_or_gone"] += skipped
        log({"event": "batch", "class": cls, "execute": execute, "removed": removed, "bytes": nbytes, "skipped": skipped,
             "done": min(i + batch, len(targets)), "of": len(targets)})
        check()
        if execute and sleep_s:
            time.sleep(sleep_s)


def rewrite_c4(root, epoch_ts, items, execute, log, totals, pending_fn):
    def _need_zero(when):
        p = pending_fn()
        if p != 0:
            raise Abort(f"C4 needs relay outbox pending_total == 0 (got {p!r} {when}): unacked-event check failed")
    _need_zero("at start")
    planned: dict = {}
    for rel, _len, sha in items:
        fname, idx = rel.split("#")
        planned.setdefault(os.path.basename(fname), set()).add(sha)
    for name, shas in sorted(planned.items()):
        path = os.path.join(root, "runtime", name)
        if name not in ("relay_outbox_quarantine.jsonl", "relay_outbox_retired.jsonl"):
            raise Abort(f"C4 file not allowed: {name}")
        st0 = os.stat(path)
        lines = open(path, "rb").read().splitlines(keepends=True)
        keep, drop, dbytes = [], 0, 0
        for ln in lines:
            sha = hashlib.sha256(ln).hexdigest()
            try:
                old = float(json.loads(ln).get("created_at_unix")) < epoch_ts
            except (ValueError, TypeError, AttributeError):
                old = False
            if sha in shas and old:
                drop += 1
                dbytes += len(ln)
            else:
                keep.append(ln)
        if execute and drop:
            tmp = path + ".nonaccum.tmp"
            with open(tmp, "wb") as fh:
                fh.write(b"".join(keep))
                fh.flush()
                os.fsync(fh.fileno())
            try:
                _need_zero("right before the replace")      # re-checked after the rewrite is staged, immediately before os.replace
            except Abort:
                os.unlink(tmp)
                raise
            st1 = os.stat(path)
            if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
                os.unlink(tmp)
                raise Abort(f"{name} changed between read and replace; refusing")
            try:
                os.replace(tmp, path)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        totals["C4"]["removed"] += drop
        totals["C4"]["bytes"] += dbytes
        log({"event": "c4_file", "file": name, "execute": execute, "lines_removed": drop, "bytes": dbytes, "lines_kept": len(keep)})


def run(root: str, epoch_ts: float, expected_sha: str, get, classes=("C1", "C2", "C4"), execute=False, confirm_sha=None,
        batch=2000, sleep_s=1.0, accepted_red=(), expected_counts: dict | None = None, include_tape=False, log_path=None, now=None) -> dict:
    lines = []

    def log(rec):
        rec = {"ts": time.time(), **rec}
        lines.append(rec)
        print(json.dumps(rec, sort_keys=True), flush=True)
        if log_path:
            with open(log_path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    classes = [c for c in CLASS_ORDER if c in classes]
    if include_tape:
        raise Abort("tape deletion is NOT permitted (HM 19 Oct): tape stays out of C1-C4")
    if "C3b" in classes:
        classes.remove("C3b")
        log({"event": "c3b_skipped", "reason": "tape_1s is never deleted by this executor (HM 19 Oct)"})
    if execute and confirm_sha != expected_sha:
        raise Abort("--execute needs --confirm-sha equal to --expected-sha")
    accepted = set(accepted_red)
    sha = None
    try:
        base = gate(get, None, accepted)
        log({"event": "gate_baseline", **base})
        doc = P.build_all(root, epoch_ts, now)
        sha = doc["plan_sha256"]
        counts = {k: v["count"] for k, v in doc["plans"].items()}
        if sha != expected_sha:
            ok = bool(expected_counts) and all(counts.get(k, 0) <= int(v) for k, v in expected_counts.items())
            log({"event": "SHA_MISMATCH", "expected": expected_sha, "actual": sha, "counts": counts, "accepted_as_smaller": ok})
            if not ok:
                res = {"status": "ABORTED", "reason": f"plan sha {sha} != expected {expected_sha}", "execute": execute, "plan_sha256": sha, "totals": {}}
                log({"event": "summary", **res})
                return res
        log({"event": "plan_verified", "plan_sha256": sha, "counts": counts,
             "bytes": {k: v["bytes"] for k, v in doc["plans"].items()}, "classes": classes, "execute": execute})
        totals = {c: {"removed": 0, "bytes": 0, "skipped_changed_or_gone": 0} for c in classes}
        items = {k: list(P.ITEMS.get(k, [])) for k in classes}
        cur_before = current_bundle_dirs(root, epoch_ts)

        def check():
            # per batch: gate only. The bundle-dir walk is expensive, so it runs once per class (and once at the end), see below.
            log({"event": "gate", **gate(get, base, accepted)})

        def bundles_check(when):
            now_cur = current_bundle_dirs(root, epoch_ts)      # the current-epoch bundle dirs must never shrink
            if now_cur < cur_before:
                raise Abort(f"current-epoch bundle dirs shrank {cur_before} -> {now_cur} ({when})")
            return now_cur

        for cls in classes:
            if cls == "C4":
                rewrite_c4(root, epoch_ts, items[cls], execute, log, totals, lambda: (get("/api/status").get("relay_outbox") or {}).get("pending_total"))
                check()
            else:
                delete_files(cls, root, epoch_ts, items[cls], execute, batch, sleep_s, check, log, totals)
            bundles_check(f"after {cls}")
        cur_after = bundles_check("final")
        result = {"status": "DONE", "execute": execute, "plan_sha256": sha, "totals": totals,
                  "current_bundle_dirs_before": cur_before, "current_bundle_dirs_after": cur_after}
    except Abort as exc:
        result = {"status": "ABORTED", "reason": str(exc), "execute": execute, "plan_sha256": sha, "totals": locals().get("totals", {})}
    except ValueError as exc:   # bad JSON from the bot API (json.JSONDecodeError is a ValueError)
        result = {"status": "ABORTED", "reason": f"bad API response: {exc}", "execute": execute, "plan_sha256": sha, "totals": locals().get("totals", {})}
    except OSError as exc:   # a missing C4 file, a failing stat/replace: end with a summary event, never a bare traceback
        result = {"status": "ABORTED", "reason": f"OSError: {exc}", "execute": execute, "plan_sha256": sha, "totals": locals().get("totals", {})}
    log({"event": "summary", **result})
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root"); ap.add_argument("epoch_ts", type=float); ap.add_argument("--expected-sha", required=True)
    ap.add_argument("--classes", default="C1,C2,C4"); ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm-sha"); ap.add_argument("--batch", type=int, default=2000); ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--accept-baseline-red", default=""); ap.add_argument("--expected-counts-json")
    ap.add_argument("--log")
    ap.add_argument("--bot-url", default=os.environ.get("BOT_LOCAL_URL", "http://127.0.0.1:7002"))
    a = ap.parse_args(argv)
    os.nice(15)
    get = http_json(a.bot_url, os.environ["BOT_ADMIN_TOKEN"])
    res = run(a.root, a.epoch_ts, a.expected_sha, get, tuple(x for x in a.classes.split(",") if x), a.execute, a.confirm_sha, a.batch, a.sleep,
              tuple(x for x in a.accept_baseline_red.split(",") if x), json.loads(a.expected_counts_json) if a.expected_counts_json else None,
              False, a.log)
    return 0 if res["status"] == "DONE" else 2


if __name__ == "__main__":
    sys.exit(main())
