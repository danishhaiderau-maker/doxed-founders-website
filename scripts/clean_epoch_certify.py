"""Clean-epoch certification gate: the 2 h window that must be GREEN before the pre-epoch wipe may run.

Reads the laptop self-aware service (:9021) only; never calls Fly with credentials and never deletes.

CERTIFIED requires, at check time:
  * the certification window is >= 2 h old (it opens at the epoch start of ``data_epoch.json`` in the
    mirror / ``--manifest``, or at a later window declared on the same epoch, see below);
  * every required finding is GREEN now: section contracts (``contract.*``), analyzer sections /
    dimensions / consistency, field-populated checks (``data.completeness``, ``data.dead_fields``,
    ``data.freshness``) and every ``data.compat_*`` check (one epoch, stamped rows, analyzer purity);
  * no required finding was RED at any point since the window opened (findings history: a transition
    to RED, or out of RED, i.e. RED when the window opened); a truncated history fails closed;
  * no finding at all is RED now;
  * the compatibility document names the same epoch and shows stamped CURRENT rows.

Writes ``C:\\DoxxedCrypto\\clean-epoch\\certification.json`` (PENDING / REJECTED / CERTIFIED) and, when
CERTIFIED, prints the confirm token ``DELETE-PRE-EPOCH:<epoch>:<sha256(cert)[:8]>`` for clean_epoch_wipe.

    python scripts\\clean_epoch_certify.py [--epoch ce-...] [--manifest PATH] [--base http://127.0.0.1:9021]

Fresh window on the same epoch (e.g. after planned laptop-chain downtime during a reset): declare it
forward in time; it can never start before the epoch, nor more than 5 min before the declaration, so a
RED that was already observed cannot be declared away. Without ``--confirm`` the plan and its token are
printed and nothing is written; with it the declaration is appended to
``C:\\DoxxedCrypto\\clean-epoch\\certification-windows.jsonl`` and recorded on the WALL. The latest
declaration of the manifest's epoch is used by every later certification run.

    python scripts\\clean_epoch_certify.py --declare-window now|<ISO-UTC> --reason "..." [--epoch ce-...]
    python scripts\\clean_epoch_certify.py --declare-window <ISO from the plan> --reason "..." \\
        --confirm CERT-WINDOW:<epoch>:<start>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "btc-conservative-agent"))
import data_epoch as de  # noqa: E402

DEFAULT_BASE = os.environ.get("SELF_AWARE_URL", "http://127.0.0.1:9021")
DEFAULT_MANIFEST = Path(os.environ.get("SELF_AWARE_MIRROR", r"C:\DoxxedCrypto\fly-mirror-segments\tree")) / de.MANIFEST_NAME
DEFAULT_OUT = Path(r"C:\DoxxedCrypto\clean-epoch") / "certification.json"
DEFAULT_WINDOWS = Path(r"C:\DoxxedCrypto\clean-epoch") / "certification-windows.jsonl"
DEFAULT_WALL = Path(r"C:\DoxxedCrypto\btc-v31-current\diagnostics\WALL-STATUS-FLY.md")
WINDOW_SCHEMA = "clean_epoch_certification_window_v1"
WINDOW_MAX_BACKDATE_SEC = 5 * 60
WINDOW_MAX_LEAD_SEC = 24 * 3600
REQUIRED_PREFIXES = ("contract.", "analyzer.sections", "analyzer.dimensions", "analyzer.consistency",
                     "data.completeness", "data.dead_fields", "data.freshness", "data.compat_")
HEALTH_MAX_AGE_SEC = 15 * 60
HISTORY_LIMIT = 5000


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


def _compact(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def window_token(epoch_id: str, start_ts: float) -> str:
    return f"CERT-WINDOW:{epoch_id}:{_compact(start_ts)}"


def plan_window(manifest: dict, start: str, now: float) -> tuple[float, list[str]]:
    """(window start, refusal reasons) for a declared window on the manifest's epoch."""
    start_ts = float(int(now)) if start == "now" else _ts(start)
    if start_ts is None:
        return 0.0, [f"window start {start!r} is not an ISO-8601 time or 'now'"]
    refusals = []
    if start_ts < float(manifest["started_at_ts"]):
        refusals.append(f"window start {de.utc_iso(start_ts)} precedes the epoch start {manifest['started_at_utc']}")
    if start_ts < now - WINDOW_MAX_BACKDATE_SEC:
        refusals.append(f"window start {de.utc_iso(start_ts)} is more than {WINDOW_MAX_BACKDATE_SEC // 60} min in the "
                        "past: a window is declared forward, never over already-observed findings")
    if start_ts > now + WINDOW_MAX_LEAD_SEC:
        refusals.append(f"window start {de.utc_iso(start_ts)} is more than 24 h ahead")
    return start_ts, refusals


def declared_window(path: Path, epoch_id: str) -> dict | None:
    """Latest window declaration of ``epoch_id`` (None: the window opens at the epoch start)."""
    latest = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("schema") == WINDOW_SCHEMA and rec.get("epoch_id") == epoch_id:
            latest = rec
    return latest


def declare_window(manifest: dict, start: str, reason: str, confirm: str | None, now: float, *,
                   windows: Path, wall: Path | None) -> tuple[int, dict]:
    """Plan (no ``confirm``) or append a window declaration; returns (exit code, plan/record)."""
    start_ts, refusals = plan_window(manifest, start, now)
    if not reason.strip():
        refusals.append("--reason is required")
    plan = {"epoch_id": manifest["epoch_id"], "window_start_utc": de.utc_iso(start_ts) if start_ts else None,
            "refusals": refusals}
    if refusals:
        return 2, plan
    token = window_token(manifest["epoch_id"], start_ts)
    plan["confirm_token"] = token
    if confirm is None:
        return 0, {**plan, "note": "plan only; rerun with --declare-window " + de.utc_iso(start_ts) + " --confirm " + token}
    if start == "now":
        return 2, {**plan, "refusals": ["confirm needs the planned ISO start, not 'now'"]}
    if confirm != token:
        return 2, {**plan, "refusals": [f"confirm token mismatch (expected {token})"]}
    record = {"schema": WINDOW_SCHEMA, "epoch_id": manifest["epoch_id"], "epoch_started_at_utc": manifest["started_at_utc"],
              "window_start_utc": de.utc_iso(start_ts), "window_start_ts": start_ts, "declared_at_utc": de.utc_iso(now),
              "reason": reason.strip(), "window_sec": de.CERTIFICATION_WINDOW_SEC,
              "requires": ["required findings GREEN now", "no RED finding now",
                           "no required finding RED during the window"]}
    windows.parent.mkdir(parents=True, exist_ok=True)
    with open(windows, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    if wall is not None:
        line = (f"| CERT-WINDOW | {de.utc_iso(now)} | AMBER | epoch {manifest['epoch_id']}: fresh certification window "
                f"opens {record['window_start_utc']} (>= {de.CERTIFICATION_WINDOW_SEC // 3600} h; requires required "
                f"findings GREEN, no RED now, no required RED during the window) - reason: {record['reason']} |\n")
        with open(wall, "a", encoding="utf-8") as fh:
            fh.write(line)
    return 0, record


def evaluate(manifest: dict, health: dict, history: list[dict], compat: dict, now: float,
             window: dict | None = None) -> list[dict]:
    """Certification checks (each GREEN or RED with the observed evidence); ``window`` = declared window record."""
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
    start = max(float(manifest["started_at_ts"]), float((window or {}).get("window_start_ts") or 0))
    in_window = [e for e in history if (_ts(e.get("at")) or 0) >= start]
    # Leaving RED inside the window means the finding was RED when the window opened.
    window_red = sorted({e["id"] for e in in_window
                         if required(str(e.get("id"))) and "RED" in (e.get("to"), e.get("from"))})
    opened = "declared window " + de.utc_iso(start) if window else "epoch start"
    add("required.no_red_in_window", not window_red, f"required findings RED since {opened}: {window_red[:20]}")
    truncated = len(history) >= HISTORY_LIMIT and min((_ts(e.get("at")) or 0) for e in history) > start
    add("required.window_history_complete", not truncated,
        f"{len(history)} findings-history events since {opened}" + (" (truncated)" if truncated else ""))
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
    ap.add_argument("--windows", default=str(DEFAULT_WINDOWS), help="certification window declarations (JSONL)")
    ap.add_argument("--declare-window", metavar="now|ISO", help="plan / declare a fresh window on the same epoch")
    ap.add_argument("--reason", default="", help="why a fresh window is declared (WALL + record)")
    ap.add_argument("--confirm", help="CERT-WINDOW:<epoch>:<start> token printed by the plan")
    ap.add_argument("--wall", default=str(DEFAULT_WALL), help="WALL file the declaration is recorded on")
    args = ap.parse_args(argv)
    manifest = de.load_manifest(Path(args.manifest))
    if not manifest:
        print(f"no valid clean-epoch manifest at {args.manifest}", file=sys.stderr)
        return 2
    if args.epoch and args.epoch != manifest["epoch_id"]:
        print(f"--epoch {args.epoch} != manifest epoch {manifest['epoch_id']}", file=sys.stderr)
        return 2
    now = time.time()
    if args.declare_window:
        code, doc = declare_window(manifest, args.declare_window, args.reason, args.confirm, now,
                                   windows=Path(args.windows), wall=Path(args.wall) if args.wall else None)
        print(json.dumps(doc, indent=1))
        return code
    window = declared_window(Path(args.windows), manifest["epoch_id"])
    since = de.utc_iso(max(float(manifest["started_at_ts"]), float((window or {}).get("window_start_ts") or 0)))
    health = _get(args.base, "/api/selfaware/health")
    history = _get(args.base, f"/api/selfaware/findings/history?limit={HISTORY_LIMIT}&since={since}").get("events") or []
    try:
        compat = _get(args.base, "/api/selfaware/data/compatibility?severity=RED,AMBER,GREEN")
    except OSError:
        compat = {}
    cert = de.certification_doc(manifest, checks=evaluate(manifest, health, history, compat, now, window),
                                now=now, window=window)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    de.write_json_atomic(out, cert)
    print(json.dumps({k: cert[k] for k in ("epoch_id", "status", "epoch_age_sec", "window_started_at_utc",
                                           "window_age_sec", "failing")}, indent=1))
    if cert["status"] != "CERTIFIED":
        return 3
    sha8 = hashlib.sha256(out.read_bytes()).hexdigest()[:8]
    print(f"confirm token: DELETE-PRE-EPOCH:{manifest['epoch_id']}:{sha8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
