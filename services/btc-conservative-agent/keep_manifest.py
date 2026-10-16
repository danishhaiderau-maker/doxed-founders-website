"""Pinned KEEP manifest: MINIMAL CONFIG ONLY (Danish 13 Oct 13:42 AEDT, superseding 13:22).

Danish: delete everything old, live order/fill/reconciliation history included. The fresh-start
wipe keeps only the files below; integrity-gaps G16 reports them as KEEP and anything else older
than the epoch (Fly, box, R2) is RED. No v3 ledgers, no Neon tables.

R2 (HM #678 B2, 16 Oct 2026): exactly ONE R2 prefix is kept: ``epoch-archive/``, the dedicated top-level
home of the NEW trusted archive (never under analysis-archive/, bot-data-* or fly-mirror-segments/). It starts
empty; the laptop r2_backup.py pins, restore-tests and reports on this prefix only. Every other R2 object
older than the epoch is still un-kept.

Changing this list changes MANIFEST_SHA256; test_keep_manifest pins the hash, so an edit needs
an explicit test update (reviewed).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json

DECISION = "Danish 2026-10-13 13:42 AEDT: delete everything incl. live history; KEEP minimal config only"

FLY = (  # relative to /app/data; matched by file name anywhere
    "**/bitfinex_live_state.json",
    "**/bitfinex_live_switch_state.json",
    "**/config-*.json",          # dashboard config: arming state + settings (config-7002.json)
    "**/config.json",            # legacy config (migrated to config-<port>.json)
    "**/policy.json",            # settings
    "**/spread-gate.json",       # spread-gate config
)
BOX = ()
NEON_TABLES = ()
R2_KEEP_PREFIXES = ("epoch-archive/",)   # HM #678 B2: dedicated top-level prefix for the new archive

MANIFEST = {"decision": DECISION, "fly": list(FLY), "box": list(BOX), "neon_tables": list(NEON_TABLES),
            "r2_keep_prefixes": list(R2_KEEP_PREFIXES)}
MANIFEST_SHA256 = hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()


def _match(rel: str, pattern: str) -> bool:
    rel = rel.replace("\\", "/")
    if pattern.endswith("/**"):
        return rel == pattern[:-3] or rel.startswith(pattern[:-2])
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pattern[3:])
    return fnmatch.fnmatch(rel, pattern)


def is_kept(rel: str, patterns=FLY, content=None) -> bool:  # content kept for call compatibility
    return any(_match(rel, p) for p in patterns)


def kept_dir(rel_dir: str, patterns=FLY) -> bool:
    return any(p.endswith("/**") and (rel_dir == p[:-3] or rel_dir.startswith(p[:-2])) for p in patterns)


def is_r2_kept(key: str) -> bool:
    """Same rule for the wipe receipt and the independent box R2 listing: nothing in R2 is kept."""
    return any(key.startswith(p) for p in R2_KEEP_PREFIXES)


def stamp_receipt(receipt: dict, r2_objects, epoch_ts: float) -> dict:
    """HM 14:10: the wipe receipt carries the manifest hash and the un-kept R2 count.

    ``r2_objects`` is an iterable of (key, last_modified_epoch_s). Returns the receipt (mutated)."""
    old = kept = 0
    for key, ts in r2_objects:
        if ts < float(epoch_ts):
            if is_r2_kept(key):
                kept += 1
            else:
                old += 1
    receipt["keep_manifest_sha256"] = MANIFEST_SHA256
    receipt["r2_objects_older_than_epoch_unkept"] = old
    receipt["r2_objects_older_than_epoch_kept"] = kept
    return receipt


if __name__ == "__main__":  # python keep_manifest.py stamp <receipt.json> <listing.jsonl> <epoch_ts>
    import sys
    if len(sys.argv) == 5 and sys.argv[1] == "stamp":
        with open(sys.argv[2], "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        with open(sys.argv[3], "r", encoding="utf-8") as fh:
            objs = [(r["key"], float(r["ts"])) for r in (json.loads(l) for l in fh if l.strip())]
        stamp_receipt(rec, objs, float(sys.argv[4]))
        with open(sys.argv[2] + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(rec, fh, sort_keys=True)
        import os
        os.replace(sys.argv[2] + ".tmp", sys.argv[2])
        print(json.dumps({k: rec[k] for k in ("keep_manifest_sha256", "r2_objects_older_than_epoch_unkept",
                                                 "r2_objects_older_than_epoch_kept")}))
    else:
        print(json.dumps({"manifest_sha256": MANIFEST_SHA256}))
