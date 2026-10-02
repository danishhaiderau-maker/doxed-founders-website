"""Clean-epoch certification gate: the 2 h window that must be GREEN before the pre-epoch wipe may run.

Reads the laptop self-aware service (:9021) only; never calls Fly with credentials and never deletes.

CERTIFIED requires, at check time:
  * the declared epoch (``data_epoch.json`` in the mirror, or ``--manifest``) is >= 2 h old;
  * every required finding is GREEN now: section contracts (``contract.*``), analyzer sections /
    dimensions / consistency, field-populated checks (``data.completeness``, ``data.dead_fields``,
    ``data.freshness``) and every ``data.compat_*`` check (one epoch, stamped rows, analyzer purity);
  * no required finding turned RED at any point since the epoch started (findings history);
  * no finding at all is RED now;
  * the compatibility document names the same epoch and shows stamped CURRENT rows.

Writes ``C:\\DoxxedCrypto\\clean-epoch\\certification.json`` (PENDING / REJECTED / CERTIFIED) and, when
CERTIFIED, prints the confirm token ``DELETE-PRE-EPOCH:<epoch>:<sha256(cert)[:8]>`` for clean_epoch_wipe.

    python scripts\\clean_epoch_certify.py [--epoch ce-...] [--manifest PATH] [--base http://127.0.0.1:9021]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "btc-conservative-agent"))
import data_epoch as de  # noqa: E402

DEFAULT_BASE = os.environ.get("SELF_AWARE_URL", "http://127.0.0.1:9021")
DEFAULT_MANIFEST = Path(os.environ.get("SELF_AWARE_MIRROR", r"C:\DoxxedCrypto\fly-mirror-segments\tree")) / de.MANIFEST_NAME
DEFAULT_OUT = Path(r"C:\DoxxedCrypto\clean-epoch") / "certification.json"
REQUIRED_PREFIXES = ("contract.", "analyzer.sections", "analyzer.dimensions", "analyzer.consistency",
                     "data.completeness", "data.dead_fields", "data.freshness", "data.compat_")
HEALTH_MAX_AGE_SEC = 15 * 60


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def required(fid: str) -> bool:
    return fid.startswith(REQUIRED_PREFIXES)


def evaluate(manifest: dict, health: dict, history: list[dict], compat: dict, now: float) -> list[dict]:
    """Certification checks (each GREEN or RED with the observed evidence)."""
    checks = []

    def add(cid, ok, observed):
        checks.append({"id": cid, "severity": "GREEN" if ok else "RED", "observed": observed})

    gen = _ts(health.get("generated_at"))
    add("selfaware.fresh", gen is not None and now - gen <= HEALTH_MAX_AGE_SEC,
        f"health generated_at {health.get('generated_at')}")
    findings = health.get("findings") or []
    req = [f for f in findings if required(str(f.get("id")))]
    not_green = [f"{f['id']}={f.get('severity')}" for f in req if f.get("severity") != "GREEN"]
    add("required.green_now", bool(req) and not not_green,
        f"{len(req)} required findings; not GREEN: {not_green[:20]}" if req else "no required findings reported")
    add("contracts.present", any(str(f.get("id")).startswith("contract.") for f in findings),
        f"{sum(1 for f in findings if str(f.get('id')).startswith('contract.'))} section-contract findings")
    red_now = [f["id"] for f in findings if f.get("severity") == "RED"]
    add("no_red_now", not red_now, f"RED now: {red_now[:20]}")
    start = float(manifest["started_at_ts"])
    window_red = sorted({e["id"] for e in history
                         if required(str(e.get("id"))) and e.get("to") == "RED" and (_ts(e.get("at")) or 0) >= start})
    add("required.no_red_in_window", not window_red, f"required findings RED since epoch start: {window_red[:20]}")
    epoch = (compat or {}).get("epoch") or {}
    add("compat.same_epoch", epoch.get("epoch_id") == manifest["epoch_id"],
        f"compat epoch {epoch.get('epoch_id')} vs manifest {manifest['epoch_id']}")
    current_rows = sum(int((s.get("classes") or {}).get(de.CURRENT, 0)) for s in (compat or {}).get("streams") or [])
    add("compat.stamped_rows", current_rows > 0, f"{current_rows} rows stamped {manifest['epoch_id']}")
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--epoch", help="expected epoch id (must match the manifest)")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--base", default=DEFAULT_BASE, help="self-aware base URL")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    manifest = de.load_manifest(Path(args.manifest))
    if not manifest:
        print(f"no valid clean-epoch manifest at {args.manifest}", file=sys.stderr)
        return 2
    if args.epoch and args.epoch != manifest["epoch_id"]:
        print(f"--epoch {args.epoch} != manifest epoch {manifest['epoch_id']}", file=sys.stderr)
        return 2
    now = time.time()
    health = _get(args.base, "/api/selfaware/health")
    history = _get(args.base, "/api/selfaware/findings/history?limit=2000&since="
                   + manifest["started_at_utc"]).get("events") or []
    try:
        compat = _get(args.base, "/api/selfaware/data/compatibility?severity=RED,AMBER,GREEN")
    except OSError:
        compat = {}
    cert = de.certification_doc(manifest, checks=evaluate(manifest, health, history, compat, now), now=now)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    de.write_json_atomic(out, cert)
    print(json.dumps({k: cert[k] for k in ("epoch_id", "status", "epoch_age_sec", "failing")}, indent=1))
    if cert["status"] != "CERTIFIED":
        return 3
    sha8 = hashlib.sha256(out.read_bytes()).hexdigest()[:8]
    print(f"confirm token: DELETE-PRE-EPOCH:{manifest['epoch_id']}:{sha8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
