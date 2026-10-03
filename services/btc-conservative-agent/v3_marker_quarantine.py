"""Retire V3 ledger completeness markers stamped by an earlier revision.

``emergency_record_idempotency_v1/<ledger>/complete.json`` carries the identity
of the revision that last wrote that ledger. The running revision already treats
a marker with another identity as absent (``_complete_generation``), but
``V3EvidenceStore.open_read_only`` refuses mixed identities, so after a held
deploy the boundary reset fails with "V3 read authority identity conflict" until
every ledger has been written again.

    python v3_marker_quarantine.py preflight       --data-dir /app/data
    python v3_marker_quarantine.py offline-proof   --data-dir /app/data --expected-rev <rev12>
    python v3_marker_quarantine.py offline-execute --data-dir /app/data --expected-rev <rev12> \
        --confirm QUARANTINE-V3-MARKERS:<sha12>

preflight is read-only and mirrors the reset boundary's open_read_only and
emergency-WAL checks. offline-execute must run with no bot process; it moves only
the planned stale markers to ``/app/data/quarantine/v3_completeness_markers/``
(outside the reset scope; never deletes) and verifies hashes before and after.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

CONFIRM_PREFIX = "QUARANTINE-V3-MARKERS"
SCHEMA = "v3_completeness_marker_quarantine_v1"
MARKER_DIR = "v3/receipts/emergency_record_idempotency_v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(runtime_root: str, current_rev: str) -> dict:
    root = Path(runtime_root)
    receipts = root / "v3" / "receipts"
    markers, refusals = [], []
    if any((receipts / "ledger_generations_v1").glob("*/ACTIVE.json")):
        refusals.append("ledger generation pointers present: out of scope")
    marker = receipts / "transactional_record_authority_v1" / "ACTIVE.json"
    if marker.exists() or marker.is_symlink():
        refusals.append("transactional authority marker present: out of scope")
    for path in sorted((root / MARKER_DIR).glob("*/complete.json")):
        row = json.loads(path.read_text("utf-8"))
        identity = row.get("identity") if isinstance(row.get("identity"), dict) else {}
        markers.append({
            "path": path.relative_to(root).as_posix(), "ledger": path.parent.name, "sha256": _sha(path),
            "bytes": path.stat().st_size, "identity": identity,
            "stale": not (str(identity.get("source_revision") or "").startswith(current_rev)
                          and str(identity.get("deployed_revision") or "").startswith(current_rev)),
        })
    epochs = {m["identity"].get("epoch_id") for m in markers}
    if len(epochs) > 1:
        refusals.append("markers span more than one epoch_id")
    body = {"schema": SCHEMA, "current_revision": current_rev, "markers": markers,
            "stale": [m["path"] for m in markers if m["stale"]], "refusals": refusals}
    body["plan_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    body["confirm_token"] = f"{CONFIRM_PREFIX}:{body['plan_sha256'][:12]}"
    return body


def preflight(runtime_root: str, current_rev: str) -> dict:
    """Read-only replica of the reset boundary's V3 identity and WAL checks."""
    from research_v3_store import V3EvidenceStore

    out = {"schema": "v3_reset_identity_preflight_v1", "current_revision": current_rev}
    try:
        store = V3EvidenceStore.open_read_only(runtime_root)
        identity = store._identity_binding()
        out["open_read_only"] = "OK"
        out["adopted_identity"] = identity
        out["adopted_matches_current"] = str(identity.get("source_revision") or "").startswith(current_rev)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        out["open_read_only"] = f"{type(exc).__name__}: {exc}"
        identity = None
    wal_root = Path(runtime_root) / "v3" / "emergency_evidence_wal_v2"
    if identity is None:
        out["wal"] = "NOT_CHECKED"
    elif not wal_root.exists():
        out["wal"] = "NOT_PRESENT"
    else:
        from emergency_evidence_wal import EmergencyEvidenceWal
        try:
            wal = EmergencyEvidenceWal.inspect_existing(wal_root, identity=identity)
            empty = (wal.get("records") == [] and wal.get("deferred_count") == 0 and wal.get("alarms") == [])
            out["wal"] = "EMPTY" if empty else "NOT_EMPTY"
        except Exception as exc:  # noqa: BLE001
            out["wal"] = f"{type(exc).__name__}: {exc}"
    out["ok"] = (out["open_read_only"] == "OK" and out.get("adopted_matches_current") is True
                 and out["wal"] in {"EMPTY", "NOT_PRESENT"})
    return out


def quarantine(data_dir: str, current_rev: str, expected_plan_sha256: str, stamp: str | None = None) -> dict:
    runtime_root = str(Path(data_dir) / "runtime")
    body = plan(runtime_root, current_rev)
    if body["plan_sha256"] != expected_plan_sha256:
        raise RuntimeError("V3_MARKER_QUARANTINE_PLAN_CHANGED")
    if body["refusals"] or not body["stale"]:
        raise RuntimeError("V3_MARKER_QUARANTINE_NOT_APPLICABLE")
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = Path(data_dir) / "quarantine" / "v3_completeness_markers" / stamp
    if target.exists():
        raise RuntimeError("V3_MARKER_QUARANTINE_TARGET_EXISTS")
    target.mkdir(parents=True)
    manifest = target / "quarantine_manifest.json"
    manifest.write_text(json.dumps({**body, "status": "PREPARED", "quarantined_at": stamp}, sort_keys=True), "utf-8")
    kept_before = {m["path"]: m["sha256"] for m in body["markers"] if not m["stale"]}
    moved = {}
    for row in (m for m in body["markers"] if m["stale"]):
        source = Path(runtime_root) / row["path"]
        if _sha(source) != row["sha256"]:
            raise RuntimeError("V3_MARKER_QUARANTINE_MARKER_CHANGED")
        destination = target / row["ledger"] / "complete.json"
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)
        if _sha(destination) != row["sha256"]:
            raise RuntimeError("V3_MARKER_QUARANTINE_COPY_MISMATCH")
        os.remove(source)
        moved[row["path"]] = _sha(destination)
    after = plan(runtime_root, current_rev)
    checks = {
        "moved_hashes_match": moved == {m["path"]: m["sha256"] for m in body["markers"] if m["stale"]},
        "current_markers_unchanged": {m["path"]: m["sha256"] for m in after["markers"]} == kept_before,
        "no_stale_markers_remain": after["stale"] == [],
    }
    status = "COMPLETE" if all(checks.values()) else "VERIFY_FAILED"
    manifest.write_text(json.dumps({**body, "status": status, "quarantined_at": stamp, "moved": moved,
                                    "checks": checks}, sort_keys=True), "utf-8")
    return {"status": status, "moved": moved, "checks": checks,
            "quarantine_dir": str(target), "plan_sha256": body["plan_sha256"]}


def main(argv: list[str] | None = None, env=None, proc_root: str = "/proc") -> int:
    import v22_seal_repair

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "offline-proof", "offline-execute"))
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--expected-rev", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    runtime_root = str(Path(args.data_dir) / "runtime")
    current = str(env.get("SOURCE_GIT_REV") or "")[:40].lower()
    if args.mode == "preflight":
        result = preflight(runtime_root, current[:12])
        print(json.dumps(result, sort_keys=True, default=str))
        return 0 if result["ok"] else 9
    if len(args.expected_rev) != 12:
        parser.error("offline modes need a 12-character --expected-rev")
    violations, state = v22_seal_repair.durable_state_violations(args.data_dir, args.expected_rev, env)
    body = plan(runtime_root, args.expected_rev)
    violations += body["refusals"]
    if not body["stale"]:
        violations.append("no stale markers")
    out = {"schema": SCHEMA, "mode": args.mode, "revision": args.expected_rev, "state": state, "plan": body}
    if args.mode == "offline-execute":
        running = v22_seal_repair.bot_processes(proc_root)
        out["bot_processes"] = running
        if running:
            violations.append("bot process is running")
        if args.confirm != body["confirm_token"]:
            violations.append("confirm token mismatch")
    if violations:
        print(json.dumps({**out, "status": "REFUSED", "violations": violations}, sort_keys=True, default=str))
        return 9
    if args.mode == "offline-proof":
        print(json.dumps({**out, "status": "PROVEN"}, sort_keys=True, default=str))
        return 0
    result = quarantine(args.data_dir, args.expected_rev, body["plan_sha256"])
    result["preflight_after"] = preflight(runtime_root, args.expected_rev)
    print(json.dumps({**out, **result}, sort_keys=True, default=str))
    return 0 if result["status"] == "COMPLETE" else 8


if __name__ == "__main__":
    sys.exit(main())
