"""Offline fixture for the PowerShell parent protocol. Never contacts Fly."""
import hashlib
import json
from pathlib import Path
import sys

request = json.load(sys.stdin)
manifest = request["manifest"]
root = Path(request["staging_root"])
members = []
for number, row in enumerate(manifest["files"]):
    raw = row["fixture_payload"].encode()
    path = root / (str(number) + ".json")
    path.write_bytes(raw)
    members.append({**{key: row[key] for key in ("path", "size", "mtime_ns", "inode", "consistency_mode")},
                    "staged_path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
if manifest.get("fixture_defect") == "hash":
    members[0]["sha256"] = "0" * 64
identity = {key: manifest[key] for key in ("inventory_generation_id", "inventory_sha256",
            "source_git_rev", "collection_epoch_id", "tile_registry_signature")}
if manifest.get("fixture_defect") in {"waiting", "foreign-wait", "late-wait"}:
    waiting_identity = dict(identity)
    if manifest["fixture_defect"] == "foreign-wait":
        waiting_identity["inventory_generation_id"] = "f" * 64
    print(json.dumps({"schema": "fly_bundle_staging_receipt_v1", "status": "INDEX_WAITING",
                      "generation": waiting_identity,
                      "elapsed_seconds": 601 if manifest["fixture_defect"] == "late-wait" else 5,
                      "next_retry_seconds": 10}), flush=True)
print(json.dumps({"schema": "fly_bundle_staging_receipt_v1", "status": "PACKAGE_VERIFIED",
                  "generation": identity, "members": members}), flush=True)
print(json.dumps({"schema": "fly_bundle_staging_receipt_v1", "status": "COMPLETE",
                  "files": len(members), "ack_sent": False}), flush=True)
