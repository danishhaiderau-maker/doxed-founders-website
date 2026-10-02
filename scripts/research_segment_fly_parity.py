"""Parity of the laptop segment tree against the Fly shipper checkpoint.

The checkpoint (``/api/research-segments/<prefix>/files``) is Fly's record of
exactly what every published segment carried, so at the same ``seq`` the
laptop must hold byte-identical shipped content:

- append streams: local length == shipped offset (minus the epoch baseline,
  plus a CSV header preamble) and the last 4 KiB match Fly's tail anchor;
- shipped snapshots: sha256 equal; SQLite copies must pass integrity_check;
- baseline-only files: pre-cutover history that never left Fly (informational);
- pruned_verified: sealed files the laptop deleted under custody-gated retention,
  whose prune-ledger sha256 equals the checkpoint's (not missing).

GREEN means 0 sealed mismatches and 0 missing shipped files.

    set BOT_ADMIN_TOKEN=...   (never printed)
    python scripts/research_segment_fly_parity.py --prefix v2 --report C:\\DoxxedCrypto\\fly-mirror-segments\\parity-latest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
from research_segment_puller import PullerError, _RunLock  # noqa: E402

SCHEMA = "research_segment_checkpoint_parity_v1"
ANCHOR_BYTES = 4096
RED_CLASSES = ("missing", "sealed_mismatch", "sqlite_corrupt")
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _get(base_url: str, prefix: str, route: str, token: str) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/research-segments/{prefix}/{route}",
                                     headers={"X-Bot-Admin-Token": token})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def _sha256(path: Path, start: int = 0) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(start)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_ok(path: Path) -> bool:
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.Error:
        return False


def classify(files: dict[str, dict], tombstones: list[str], tree: Path, baselines: dict,
             pruned: dict[str, str] | None = None) -> dict:
    local = {path.relative_to(tree).as_posix(): path for path in tree.rglob("*") if path.is_file()}
    pruned = pruned or {}
    buckets: dict[str, list] = {name: [] for name in (
        "append_match", "snapshot_match", "sqlite_ok", "baseline_only", "pruned_verified",
        "missing", "sealed_mismatch", "sqlite_corrupt", "local_only")}
    for relpath, entry in sorted(files.items()):
        path = local.get(relpath)
        if entry.get("baseline") and entry.get("class") == "snapshot":
            buckets["baseline_only"].append(relpath)
            continue
        base = baselines.get(relpath)
        if entry.get("class") == "append" or (base is not None and entry.get("class") == "snapshot"):
            shipped = int(entry.get("offset", entry.get("size")) or 0)
            expected = shipped if base is None else shipped - base["base_offset"] + base["preamble_size"]
            size = path.stat().st_size if path is not None else 0
            if path is None and expected:
                buckets["missing"].append(relpath)
            elif size != expected:
                buckets["sealed_mismatch"].append({"path": relpath, "expected": expected, "local": size})
            elif (entry.get("tail_sha256") and (base is None or shipped - base["base_offset"] >= ANCHOR_BYTES)
                  and shipped >= ANCHOR_BYTES and _sha256(path, size - ANCHOR_BYTES) != entry["tail_sha256"]):
                buckets["sealed_mismatch"].append({"path": relpath, "reason": "tail_anchor"})
            else:
                buckets["append_match"].append(relpath)
            continue
        if path is None and entry.get("sha256") and pruned.get(relpath) == entry["sha256"]:
            buckets["pruned_verified"].append(relpath)
        elif path is None:
            buckets["missing"].append(relpath)
        elif _sha256(path) != entry.get("sha256"):
            buckets["sealed_mismatch"].append({"path": relpath, "reason": "sha256"})
        elif relpath.lower().endswith(SQLITE_SUFFIXES):
            buckets["sqlite_ok" if _sqlite_ok(path) else "sqlite_corrupt"].append(relpath)
        else:
            buckets["snapshot_match"].append(relpath)
    buckets["local_only"] = sorted(set(local) - set(files) - set(tombstones))
    counts = {name: len(items) for name, items in buckets.items()}
    verdict = "GREEN" if all(counts[name] == 0 for name in RED_CLASSES) else "RED"
    return {"verdict": verdict, "counts": counts,
            "examples": {name: items[:40] for name, items in buckets.items() if items}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-root", default=r"C:\DoxxedCrypto\fly-mirror-segments")
    parser.add_argument("--base-url", default="https://doxed-btc-bot.fly.dev")
    parser.add_argument("--prefix", default=os.getenv("RESEARCH_SEGMENTS_PREFIX") or "v2")
    parser.add_argument("--report", required=True)
    parser.add_argument("--retention-dir", default=r"C:\DoxxedCrypto\bot-data-retention")
    args = parser.parse_args(argv)
    token = os.environ.get("BOT_ADMIN_TOKEN") or ""
    if not token:
        print(json.dumps({"error": "BOT_ADMIN_TOKEN is not set"}))
        return 2
    shadow_root = Path(args.shadow_root)
    # The tree must not advance between the seq check and the scan.
    try:
        lock = _RunLock(shadow_root / ".puller" / "run.lock")
    except PullerError:
        print(json.dumps({"verdict": "RETRY", "reason": "a puller run holds the shadow-root lock"}))
        return 3
    try:
        state = json.loads((shadow_root / ".puller" / "state.json").read_text(encoding="utf-8"))
        checkpoint = _get(args.base_url, args.prefix, "files", token)
        if int(checkpoint["seq"]) != int(state["applied_seq"]) \
                or checkpoint["last_manifest_sha256"] != state["last_manifest_sha256"]:
            print(json.dumps({"verdict": "RETRY", "reason": "laptop is not at the checkpoint seq",
                              "fly_seq": checkpoint["seq"], "laptop_seq": state["applied_seq"]}))
            return 3
        from bot_data_retention import pruned_index

        report = classify(checkpoint["files"], checkpoint.get("tombstones") or [],
                          shadow_root / "tree", state.get("baselines") or {},
                          pruned_index(args.retention_dir))
    finally:
        lock.release()
    report.update({
        "schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
        "prefix": args.prefix, "seq": state["applied_seq"],
        "manifest_sha256": state["last_manifest_sha256"], "baseline": checkpoint.get("baseline"),
        "tracked_files": len(checkpoint["files"]),
    })
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("verdict", "counts", "seq", "prefix")}))
    return 0 if report["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
