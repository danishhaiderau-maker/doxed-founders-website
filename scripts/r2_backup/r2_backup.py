#!/usr/bin/env python3
"""Off-site backup of BTC bot data to Cloudflare R2 (per-file zstd, incremental), HM (c) rewrite.

Drop-in replacement for the laptop's r2_backup.py (same ROOTS, same object layout ``<key>.zst`` with
``sha256``/``raw-size`` metadata, same state.json shape). NOT installed on the laptop by this repo change.

What changed (16 Oct 2026, HM-agreed), after the 13 Oct wipe left state.json listing 426,780 keys while the
bucket held 5,935 objects, so 21 of 23 restore samples were NoSuchKey and the receipt still said ok:

* ``restore-test`` samples ONLY keys pinned by the KEEP manifest (``--keep-prefixes`` / ``--keep-manifest``),
  never the local state file's whole key space; each sampled object is HEAD-verified first and misses are
  counted separately from hash mismatches.
* ``reconcile`` rebuilds state.json = bucket listing  ∩  keep prefixes  −  quarantine, and reports counts.
* every run writes a receipt with ``state_keys``, ``r2_objects``, ``missing``, ``quarantined`` (and
  ``quarantine_pending``); ``ok`` is False if anything is missing, bad, errored or the pinned set is empty.
* ``sync`` runs reconcile first so a key absent from the bucket is re-uploaded instead of skipped.
Never deletes local or remote data.

  python r2_backup.py reconcile    [--keep-prefixes a/,b/ | --keep-manifest keep.json] [--apply]
  python r2_backup.py sync
  python r2_backup.py restore-test [--sample 100]
  python r2_backup.py receipt                       # print the last receipt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time

ACCOUNT = "242582298202462d75198184516c54d2"
BUCKET = "doxed-btc-archive"
BASE = r"C:\DoxxedCrypto"
ROOTS = {  # remote prefix -> local dir
    "canonical-research-data": BASE + r"\btc-v31-current\services\btc-conservative-agent\canonical-research-data",
    "fly-mirror-segments": BASE + r"\fly-mirror-segments",
    "fly-segments": BASE + r"\fly-segments",
    "storage-dedupe-retained": BASE + r"\storage-dedupe-retained",
    "clean-epoch": BASE + r"\clean-epoch",
    "pre-clean-epoch": BASE + r"\pre-clean-epoch",
    "bot-data-retention": BASE + r"\bot-data-retention",
    "bot-data-compact": BASE + r"\bot-data-compact",
    "analysis-archive": BASE + r"\analysis-archive",
    # HM #678 B2: dedicated TOP-LEVEL prefix for the new trusted archive (not under analysis-archive/,
    # bot-data-* or fly-mirror-segments/). It is the one prefix in keep_manifest.R2_KEEP_PREFIXES.
    "epoch-archive": BASE + r"\epoch-archive",
}
ARCHIVE_PREFIX = "epoch-archive/"
SKIP_SUFFIX = (".download", ".lease", ".lock", ".tmp", "-journal", "-wal", "-shm")
SQLITE_SUFFIX = (".db", ".sqlite", ".sqlite3")
STATE_DIR = BASE + r"\r2-backup"
STATE = os.path.join(STATE_DIR, "state.json")
LOG = os.path.join(STATE_DIR, "r2-backup.log")
META_KEY = "_meta/state.json.zst"
QUARANTINE_PREFIX = "_quarantine/"
RECEIPT_SCHEMA = "r2_backup_receipt_v2"
DEFAULT_SAMPLE = 100
SIG_UNKNOWN = [-1, -1]  # forces the next sync to re-check/upload the local file


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ keep manifest pinning
def load_keep(keep_prefixes: str = "", keep_manifest: str = "") -> dict:
    """The pinned set of R2 key prefixes (the KEEP manifest's ``r2_keep_prefixes``) + its sha256.

    Sources, first match wins: ``--keep-manifest`` JSON file ({"r2_keep_prefixes": [...]}), then
    ``--keep-prefixes`` (comma list). The pinned list is hashed into every receipt.
    """
    prefixes: list[str] = []
    if keep_manifest:
        with open(keep_manifest, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        prefixes = [str(p) for p in (doc.get("r2_keep_prefixes") or [])]
    elif keep_prefixes:
        prefixes = [p.strip() for p in keep_prefixes.split(",") if p.strip()]
    prefixes = sorted(set(prefixes))
    digest = hashlib.sha256(json.dumps({"r2_keep_prefixes": prefixes}, sort_keys=True).encode()).hexdigest()
    return {"prefixes": prefixes, "sha256": digest}


def is_pinned(key: str, prefixes) -> bool:
    return any(key.startswith(p) for p in prefixes)


# ------------------------------------------------------------------ bucket listing / classification
def obj_to_key(obj_key: str) -> str | None:
    """Bucket object key -> state key (strip the .zst suffix). None for non-backup objects."""
    if obj_key.startswith("_meta/") or obj_key.startswith(QUARANTINE_PREFIX):
        return None
    return obj_key[:-4] if obj_key.endswith(".zst") else obj_key


def list_bucket(s3, bucket: str = BUCKET) -> dict:
    """{object_key: {"size": int, "ts": epoch_s}} for the whole bucket (paginated, read-only)."""
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            lm = o["LastModified"]
            out[o["Key"]] = {"size": int(o["Size"]), "ts": lm.timestamp() if hasattr(lm, "timestamp") else float(lm)}
    return out


def classify_listing(listing: dict, prefixes) -> dict:
    """Split bucket objects into pinned (kept), quarantined (under _quarantine/), meta, outside_keep."""
    pinned, quarantined, meta, outside = {}, [], [], []
    for obj_key, info in listing.items():
        if obj_key.startswith(QUARANTINE_PREFIX):
            quarantined.append(obj_key)
        elif obj_key.startswith("_meta/"):
            meta.append(obj_key)
        else:
            key = obj_to_key(obj_key)
            if key is not None and is_pinned(key, prefixes):
                pinned[key] = info
            else:
                outside.append(obj_key)
    return {"pinned": pinned, "quarantined": sorted(quarantined), "meta": sorted(meta), "outside_keep": sorted(outside)}


def reconcile_state(state: dict, listing: dict, prefixes, head=None) -> tuple[dict, dict]:
    """New state = bucket listing ∩ keep prefixes (− quarantine). Returns (new_state, report).

    Keys in the old state without an object are dropped (counted as ``missing``) so the next sync
    re-uploads whatever still exists locally. Objects without a state entry are adopted from the
    object's ``sha256`` metadata via ``head(obj_key)`` with an unknown sig (forces a re-check).
    """
    cls = classify_listing(listing, prefixes)
    pinned = cls["pinned"]
    new_state, adopted, adopt_failed = {}, 0, 0
    for key, info in pinned.items():
        old = state.get(key)
        if old is not None:
            new_state[key] = old
            continue
        meta = head(key + ".zst") if head else None
        if meta and meta.get("sha256"):
            new_state[key] = {"sig": list(SIG_UNKNOWN), "sha256": meta["sha256"],
                              "raw": int(meta.get("raw-size") or 0), "zst": info["size"]}
            adopted += 1
        else:
            adopt_failed += 1
    state_pinned = {k for k in state if is_pinned(k, prefixes)}
    missing = sorted(k for k in state_pinned if k not in pinned)
    not_pinned_in_state = sorted(k for k in state if not is_pinned(k, prefixes))
    report = {"state_keys_before": len(state), "state_keys_after": len(new_state),
              "r2_objects": len(pinned), "r2_objects_total": len(listing),
              "missing": len(missing), "missing_examples": missing[:5],
              "dropped_not_pinned": len(not_pinned_in_state), "adopted_from_bucket": adopted,
              "adopt_failed": adopt_failed, "outside_keep": len(cls["outside_keep"]),
              "quarantined": len(cls["quarantined"]), "meta_objects": len(cls["meta"])}
    return new_state, report


# ------------------------------------------------------------------ restore test
def _is_missing(exc: Exception) -> bool:
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = str((resp.get("Error") or {}).get("Code") or "")
        status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if status == 404:
            return True
    return code in ("404", "NoSuchKey", "NotFound")


def restore_test(state: dict, s3, prefixes, *, sample: int = DEFAULT_SAMPLE, bucket: str = BUCKET,
                 rng=None, decompress=None, allow_empty: bool = False) -> dict:
    """Sample pinned state keys only; HEAD each (miss counted separately), then GET + sha256 compare.

    ``ok`` requires checked > 0 (unless ``allow_empty``), missing == 0, bad == 0, errors == 0.
    """
    rng = rng or random.Random()
    if decompress is None:
        import zstandard
        d = zstandard.ZstdDecompressor()
        decompress = lambda b: d.decompress(b, max_output_size=1 << 34)  # noqa: E731
    pool = sorted(k for k in state if is_pinned(k, prefixes))
    keys = rng.sample(pool, min(sample, len(pool))) if pool else []
    ok = bad = missing = errors = 0
    bad_keys, missing_keys = [], []
    for k in keys:
        obj = k + ".zst"
        try:
            s3.head_object(Bucket=bucket, Key=obj)
        except Exception as exc:  # noqa: BLE001
            if _is_missing(exc):
                missing += 1; missing_keys.append(k)
            else:
                errors += 1
            continue
        try:
            raw = decompress(s3.get_object(Bucket=bucket, Key=obj)["Body"].read())
            if hashlib.sha256(raw).hexdigest() == state[k]["sha256"]:
                ok += 1
            else:
                bad += 1; bad_keys.append(k)
        except Exception as exc:  # noqa: BLE001
            if _is_missing(exc):
                missing += 1; missing_keys.append(k)
            else:
                errors += 1
    checked = ok + bad + missing + errors
    empty = not pool
    passed = (checked > 0 or allow_empty) and missing == 0 and bad == 0 and errors == 0
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checked": checked, "ok": ok, "bad": bad,
            "missing": missing, "errors": errors, "pinned_pool": len(pool), "sample": sample,
            "empty_pinned_set": empty, "passed": bool(passed),
            "bad_examples": bad_keys[:5], "missing_examples": missing_keys[:5]}


# ------------------------------------------------------------------ receipt
def build_receipt(*, state: dict, report: dict | None, restore: dict | None, keep: dict, errors: int = 0,
                  quarantine_pending: int = 0, extra: dict | None = None) -> dict:
    """Receipt pushed to Fly /api/monitor/archive/receipt. ``files`` is kept for older readers."""
    report = report or {}
    missing = int(report.get("missing") or 0) + int((restore or {}).get("missing") or 0)
    r = {"schema": RECEIPT_SCHEMA, "bucket": BUCKET, "finished_ts": time.time(),
         "state_keys": len(state), "files": len(state),
         "r2_objects": report.get("r2_objects"), "missing": missing,
         "quarantined": report.get("quarantined", 0), "quarantine_pending": int(quarantine_pending),
         "outside_keep": report.get("outside_keep", 0), "errors": int(errors),
         "keep_manifest_sha256": keep.get("sha256"), "keep_prefixes": keep.get("prefixes"),
         "restore_test": restore}
    # HM #678 B1: quarantine_pending is its OWN field. A clean bucket with objects still waiting to be
    # quarantined is AMBER on Fly (monitor_checks.archive_receipt), never a failed (RED) backup.
    r["ok"] = bool(errors == 0 and missing == 0 and restore is not None and restore.get("passed"))
    if extra:
        r.update(extra)
    return r


# ------------------------------------------------------------------ receipt -> Fly
RECEIPT_URL = os.environ.get("R2_RECEIPT_URL", "https://doxed-btc-bot.fly.dev/api/monitor/archive/receipt")
# Fields Fly's POST /api/monitor/archive/receipt whitelists (bot.monitor_archive_receipt_post). The WHOLE v2
# receipt is posted; test_archive_receipt_v2 asserts every v2 field below survives the route.
RECEIPT_V2_FIELDS = ("schema", "state_keys", "r2_objects", "missing", "quarantined", "quarantine_pending",
                     "outside_keep", "keep_manifest_sha256", "restore_test", "errors", "ok", "finished_ts",
                     "files", "bucket")


def post_receipt(receipt: dict, *, token: str = "", url: str = "", urlopen=None) -> bool:
    """Best-effort POST of the v2 receipt to Fly (admin token from BOT_ADMIN_TOKEN). Never raises."""
    token = token or os.environ.get("BOT_ADMIN_TOKEN", "")
    if not token:
        log("receipt not posted: BOT_ADMIN_TOKEN is not set")
        return False
    import urllib.request
    body = json.dumps({k: receipt[k] for k in RECEIPT_V2_FIELDS if k in receipt}, default=str).encode()
    req = urllib.request.Request(url or RECEIPT_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Bot-Admin-Token": token})
    try:
        with (urlopen or urllib.request.urlopen)(req, timeout=20) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception as exc:  # noqa: BLE001 - the backup result stands even if Fly is unreachable
        log(f"receipt post failed: {exc!r}")
        return False


# ------------------------------------------------------------------ quarantine (PLAN ONLY: nothing moves here)
# The 5,930 objects re-uploaded by the first nightly sync after the 13 Oct wipe (14 Oct 03:xx AEDT = 13 Oct
# 16:xx UTC) are pre-wipe laptop files. They are listed (never moved or deleted) by r2_quarantine_plan.py.
QUARANTINE_SINCE = "2026-10-13T16:00:00Z"
QUARANTINE_UNTIL = "2026-10-13T17:00:00Z"
QUARANTINE_EXPECTED = 5930


def _iso_ts(value: str) -> float:
    import calendar
    return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))


class QuarantinePlanError(RuntimeError):
    """A quarantine candidate is pinned (or the plan no longer matches the bucket). Never proceed."""


def quarantine_candidates(listing: dict, prefixes, since: str = QUARANTINE_SINCE, until: str = QUARANTINE_UNTIL) -> list:
    """Object keys last modified in [since, until) that are not meta/already quarantined.

    HM #678 B2: FAILS (QuarantinePlanError) if ANY candidate is pinned by the keep manifest. A pinned
    object in the window means the window or the manifest is wrong; silently skipping it would hide that.
    """
    lo, hi = _iso_ts(since), _iso_ts(until)
    out, pinned = [], []
    for obj_key, info in listing.items():
        if obj_key.startswith(("_meta/", QUARANTINE_PREFIX)):
            continue
        key = obj_to_key(obj_key)
        if key is None:
            continue
        if lo <= info["ts"] < hi:
            (pinned if is_pinned(key, prefixes) else out).append(obj_key)
    if pinned:
        raise QuarantinePlanError(
            f"{len(pinned)} quarantine candidate(s) are pinned by the keep manifest, e.g. {sorted(pinned)[:3]}; "
            "refusing to build a plan")
    return sorted(out)


# ------------------------------------------------------------------ boto3 / zstd glue (laptop only)
def client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=f"https://{ACCOUNT}.r2.cloudflarestorage.com",
                        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto",
                        config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=32))


def _head_meta(s3):
    def head(obj_key):
        try:
            h = s3.head_object(Bucket=BUCKET, Key=obj_key)
        except Exception:  # noqa: BLE001
            return None
        return dict(h.get("Metadata") or {})
    return head


def _load_state() -> dict:
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def _save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(state, open(STATE + ".new", "w")); os.replace(STATE + ".new", STATE)


def cmd_reconcile(args, keep) -> int:
    s3 = client()
    state = _load_state()
    new_state, report = reconcile_state(state, list_bucket(s3), keep["prefixes"], head=_head_meta(s3))
    log("reconcile " + json.dumps(report))
    if args.apply:
        if os.path.exists(STATE):
            os.replace(STATE, STATE + ".pre-reconcile." + time.strftime("%Y%m%d%H%M%S"))
        _save_state(new_state)
        log(f"state.json rewritten: {len(new_state)} keys (old file kept as .pre-reconcile.*)")
    else:
        log("dry run: state.json NOT modified (use --apply)")
    print(json.dumps(report, indent=1))
    return 0


def _read_bytes(path):
    import sqlite3
    import tempfile
    if path.lower().endswith(SQLITE_SUFFIX):  # consistent snapshot of live sqlite
        fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
        try:
            src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60); dst = sqlite3.connect(tmp)
            src.backup(dst); dst.close(); src.close()
            with open(tmp, "rb") as f:
                return f.read()
        except sqlite3.Error:
            pass
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    with open(path, "rb") as f:
        return f.read()


def _scan(prefixes):
    for prefix, root in ROOTS.items():
        if not is_pinned(prefix + "/", prefixes) or not os.path.isdir(root):
            continue
        for dp, _dns, fns in os.walk(root):
            for fn in fns:
                if fn.endswith(SKIP_SUFFIX):
                    continue
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                rel = os.path.relpath(p, root).replace("\\", "/")
                yield f"{prefix}/{rel}", p, st.st_size, int(st.st_mtime)


def cmd_sync(args, keep) -> int:
    """Reconcile first (so a key missing from R2 is re-uploaded), upload pinned changes, restore-test, receipt."""
    import threading
    import zstandard
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not keep["prefixes"]:
        log("sync refused: the keep manifest pins no R2 prefix (nothing is kept in R2)")
        return 3
    s3 = client()
    state = _load_state()
    state, report = reconcile_state(state, list_bucket(s3), keep["prefixes"], head=_head_meta(s3))
    log("reconcile " + json.dumps(report))
    dropped = report["missing"]
    todo = [(k, p, sz, m) for k, p, sz, m in _scan(keep["prefixes"]) if state.get(k, {}).get("sig") != [sz, m]]
    log(f"sync start: {len(todo)} changed/new files")
    lock, cctx, errs = threading.Lock(), threading.local(), 0

    def up(item):
        k, p, sz, m = item
        if not hasattr(cctx, "c"):
            cctx.c = zstandard.ZstdCompressor(level=args.level, threads=0)
        data = _read_bytes(p); sha = hashlib.sha256(data).hexdigest()
        z = cctx.c.compress(data)
        s3.put_object(Bucket=BUCKET, Key=k + ".zst", Body=z, Metadata={"sha256": sha, "raw-size": str(len(data))})
        return k, sz, m, sha, len(data), len(z)

    with ThreadPoolExecutor(args.workers) as ex:
        for f in as_completed([ex.submit(up, t) for t in todo]):
            try:
                k, sz, m, sha, rs, zs = f.result()
                with lock:
                    state[k] = {"sig": [sz, m], "sha256": sha, "raw": rs, "zst": zs}
            except Exception as e:  # noqa: BLE001
                errs += 1; log(f"ERR {e!r}")
    _save_state(state)
    s3.put_object(Bucket=BUCKET, Key=META_KEY, Body=zstandard.ZstdCompressor(level=10).compress(json.dumps(state).encode()))
    restore = restore_test(state, s3, keep["prefixes"], sample=args.sample, allow_empty=args.allow_empty)
    _, after = reconcile_state(state, list_bucket(s3), keep["prefixes"], head=None)
    pending = len(quarantine_candidates(list_bucket(s3), keep["prefixes"]))
    receipt = build_receipt(state=state, report=after, restore=restore, keep=keep, errors=errs, quarantine_pending=pending,
                            extra={"uploaded": len(todo) - errs, "reconciled_dropped": dropped})
    json.dump(receipt, open(os.path.join(STATE_DIR, "last-sync.json"), "w"), indent=1)
    post_receipt(receipt)
    log("sync done " + json.dumps({k: receipt[k] for k in ("state_keys", "r2_objects", "missing", "quarantined",
                                                          "quarantine_pending", "errors", "ok")}))
    return 0 if receipt["ok"] else 1


def cmd_restore_test(args, keep) -> int:
    s3 = client()
    state = _load_state()
    result = restore_test(state, s3, keep["prefixes"], sample=args.sample, allow_empty=args.allow_empty)
    receipt = build_receipt(state=state, report=None, restore=result, keep=keep)
    log("restore-test " + json.dumps(result))
    json.dump(receipt, open(os.path.join(STATE_DIR, "last-restore-test.json"), "w"))
    post_receipt(receipt)
    return 0 if result["passed"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["reconcile", "sync", "restore-test", "receipt"])
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--level", type=int, default=9)
    ap.add_argument("--keep-prefixes", default=os.environ.get("R2_KEEP_PREFIXES", ""))
    ap.add_argument("--keep-manifest", default=os.environ.get("R2_KEEP_MANIFEST", ""))
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-empty", action="store_true",
                    help="an empty pinned set passes (only when the keep manifest intends R2 to be empty)")
    args = ap.parse_args(argv)
    keep = load_keep(args.keep_prefixes, args.keep_manifest)
    if args.cmd == "receipt":
        print(open(os.path.join(STATE_DIR, "last-restore-test.json")).read())
        return 0
    if args.cmd == "sync":
        return cmd_sync(args, keep)
    return cmd_reconcile(args, keep) if args.cmd == "reconcile" else cmd_restore_test(args, keep)


if __name__ == "__main__":
    sys.exit(main())
