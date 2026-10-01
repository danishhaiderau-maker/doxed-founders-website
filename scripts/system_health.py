"""One aggregated, positive-progress health verdict for the whole BTC V3.1 stack.

Every check answers "is this subsystem making progress?", never "is a process
alive?". Each check carries status (GREEN/AMBER/RED/SKIP), the observed value,
its threshold, the last time it was GREEN, a likely-cause hint and a runbook
anchor in ``docs/SYSTEM_HEALTH_RUNBOOK.md``. The overall verdict is the worst
non-SKIP status.

Read-only by construction: it GETs public Fly/Railway endpoints, the
admin-authenticated Fly ``/api/state`` (read-only), the local analyzer API and
laptop files. It never pauses, resumes, arms, deploys, closes or prunes
anything. The only write towards Fly is the optional banner report
(``POST /api/system-health/report``), which stores a summary for the dashboard.

Usage:
  python scripts/system_health.py            # live evaluation, human summary
  python scripts/system_health.py --json     # same, full JSON
  python scripts/system_health.py --tick     # watcher tick: evaluate, alarm, publish
  python scripts/system_health.py --latest   # print the last published verdict

Exit codes: 0 GREEN, 1 AMBER, 2 RED.
"""
from __future__ import annotations

import argparse
import base64
import copy
import csv
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA = "system_health_v1"
ALARM_SCHEMA = "system_health_alarm_v1"
GREEN, AMBER, RED, SKIP = "GREEN", "AMBER", "RED", "SKIP"
RANK = {SKIP: -1, GREEN: 0, AMBER: 1, RED: 2}
RUNBOOK = "docs/SYSTEM_HEALTH_RUNBOOK.md"

DEFAULT_STATE_DIR = r"C:\DoxxedCrypto\laptop-chain"
DEFAULT_ANALYZER_REPO = r"C:\DoxxedCrypto\v2c"
DEFAULT_MIRROR_TREE = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_SHADOW_ROOT = r"C:\DoxxedCrypto\fly-mirror-segments"
DEFAULT_EXPORTS = r"C:\DoxxedCrypto\analyzer-exports\latest"
DEFAULT_PROOF_DIR = r"C:\DoxxedCrypto\btc-v31-current\diagnostics"
DEFAULT_VAULT = r"C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\home-bot.env"
FLY_URL = "https://doxed-btc-bot.fly.dev"
ANALYZER_URL = "http://127.0.0.1:9001"
SERVER_PORT = 9011
REPO = "danishhaiderau-maker/doxed-founders-website"

MIN = 60.0
HOUR = 3600.0

# Thresholds in seconds unless named otherwise. RED means "blocks trading,
# data custody or safety"; AMBER means "degraded, look soon".
THRESHOLDS: dict[str, float] = {
    "fly_unreachable_ticks": 2,
    "deploy_pause_max_sec": 45 * MIN,
    "ai_success_amber_sec": 6 * MIN,
    "ai_success_red_sec": 12 * MIN,
    "ai_consecutive_failures_red": 3,
    "ai_neutral_min_samples": 20,
    "ai_neutral_window_sec": 6 * HOUR,
    "orders_red_sec": 3 * HOUR,
    "tile_quiet_amber_sec": 6 * HOUR,
    "contradiction_red_window_sec": 2 * HOUR,
    "contradiction_amber_window_sec": 24 * HOUR,
    "ws_amber_sec": 60.0,
    "ws_red_sec": 120.0,
    # Idle gaps of up to ~14 min occur with a few MB of backlog even after #257,
    # so a plain gap is AMBER; an erroring shipper (PLAN_RACE every cycle) is RED
    # at 9 min and any backlog with no segment at 20 min.
    "shipper_stall_amber_sec": 9 * MIN,
    "shipper_stall_red_sec": 9 * MIN,
    "shipper_stall_backlog_red_sec": 20 * MIN,
    "shipper_status_red_sec": 10 * MIN,
    "unshipped_amber_bytes": 256 * 1024 * 1024,
    "pull_finished_red_sec": 15 * MIN,
    "pull_lag_seq_red": 30,
    "pull_lag_red_sec": 15 * MIN,
    "ack_lag_red_sec": 15 * MIN,
    "ack_lag_seq_red": 10,
    "analyzer_gen_amber_sec": 45 * MIN,
    "analyzer_gen_red_sec": 90 * MIN,
    "analyzer_api_down_red_sec": 20 * MIN,
    "cycle_amber_sec": 45 * MIN,
    "cycle_red_sec": 90 * MIN,
    "exports_amber_sec": 90 * MIN,
    "streams_stale_amber_sec": 15 * MIN,
    "registry_mismatch_red_sec": 30 * MIN,
    "relay_snapshot_amber_sec": 15 * MIN,
    "relay_heartbeat_amber_ms": 5 * MIN * 1000,
    "laptop_free_amber_bytes": 20 * 1024**3,
    "laptop_free_red_bytes": 5 * 1024**3,
    "fly_free_amber_bytes": 8 * 1024**3,
    "fly_free_red_bytes": 4 * 1024**3,
    "store_cap_amber_pct": 80.0,
    "store_cap_red_pct": 95.0,
    "proof_row_amber_sec": 45 * MIN,
    "supervisor_tick_red_sec": 15 * MIN,
    "watcher_stale_sec": 15 * MIN,
    "renotify_sec": 6 * HOUR,
    "tick_min_interval_sec": 240.0,
    "served_model_change_amber_sec": 6 * HOUR,
    "deepseek_balance_amber_usd": 5.0,
    "deepseek_balance_red_usd": 1.0,
    "deepseek_balance_cache_sec": 10 * MIN,
    "deepseek_balance_fly_max_age_sec": 30 * MIN,
}

# DeepSeek retired "deepseek-v4-flash" on 2026-10-01 and serves those requests
# as "deepseek-flash" (DeepSeek-V4.1-Flash). Used only when Fly does not yet
# report its configured model.
EXPECTED_DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"

# Checks that need N consecutive bad evaluations before an alarm opens (flap
# guard for single network blips). Default is 1.
SUSTAIN = {"fly.process": 2, "analyzer.api": 2, "railway.api": 2, "trading.orphans": 2}


# ---------------------------------------------------------------- utilities

def utcnow() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> float | None:
    """Epoch seconds from an epoch number or an ISO-8601 string."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).strip().lstrip("\ufeff")
    try:
        number = float(text)
        return number if number > 0 else None
    except ValueError:
        pass
    text = re.sub(r"(\.\d{6})\d+", r"\1", text).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def read_json(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def fmt_age(sec: float | None) -> str:
    if sec is None:
        return "never"
    sec = max(0.0, float(sec))
    if sec < 120:
        return f"{sec:.0f}s"
    if sec < 2 * HOUR:
        return f"{sec / MIN:.0f}m"
    return f"{sec / HOUR:.1f}h"


def dig(obj: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(obj, Mapping):
            return default
        obj = obj.get(key)
    return default if obj is None else obj


def tail_csv(path: Path, max_bytes: int = 2_000_000) -> list[dict[str, str]]:
    """Rows from the last ``max_bytes`` of a CSV, keyed by the file's header."""
    try:
        with open(path, "rb") as handle:
            header = handle.readline().decode("utf-8-sig", "replace").strip("\r\n")
            size = handle.seek(0, os.SEEK_END)
            start = max(len(header) + 1, size - max_bytes)
            handle.seek(start)
            blob = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    if start > len(header) + 1:
        blob = blob.split("\n", 1)[1] if "\n" in blob else ""
    reader = csv.DictReader(io.StringIO(header + "\n" + blob))
    try:
        return [row for row in reader]
    except csv.Error:
        return []


def check(
    cid: str,
    subsystem: str,
    status: str,
    observed: Any,
    threshold: Any,
    hint: str = "",
    detail: str = "",
) -> dict[str, Any]:
    return {
        "id": cid,
        "subsystem": subsystem,
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "hint": hint,
        "detail": detail,
        "runbook": f"{RUNBOOK}#{cid.replace('.', '-')}",
    }


# ------------------------------------------------------------------ inputs

def load_vault(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def http_json(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 20.0,
              method: str = "GET", body: Any = None) -> tuple[Any, str | None]:
    """(payload, error_code). Error codes are bounded; never echo upstream text."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", "User-Agent": "doxxed-system-health/1",
        **({"Content-Type": "application/json"} if data is not None else {}),
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return (json.loads(raw.decode("utf-8")) if raw else {}), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP_{exc.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        return None, "UNREACHABLE"
    except ValueError:
        return None, "BAD_JSON"


def _git(repo: str, *args: str, timeout: float = 20.0) -> str | None:
    try:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_registry(repo: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    """Active tile roster + signature from the canonical registry (cached per HEAD)."""
    head = _git(repo, "rev-parse", "HEAD")
    if head and cache.get("head") == head and cache.get("registry"):
        return cache["registry"]
    agent = Path(repo) / "services" / "btc-conservative-agent"
    code = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);import combo_pathway_config as c;"
        "print(json.dumps({'signature':c.active_tile_registry_signature(),'lanes':list(c.ACTIVE_TILE_ORDER)}))"
    )
    try:
        result = subprocess.run([sys.executable, "-c", code, str(agent)], capture_output=True,
                                text=True, timeout=90, check=False, cwd=str(agent))
        registry = json.loads(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        registry = None
    if registry:
        cache["head"], cache["registry"] = head, registry
    return registry


def collect_mirror(tree: Path, now: float) -> dict[str, Any]:
    """AI and order evidence from the laptop mirror (lags Fly by the shipper)."""
    out: dict[str, Any] = {"ai_success_ts": None, "ai_failure_ts": None, "ai_events": [],
                           "lane_orders": {}, "expired": {}, "filled": {}}
    for row in tail_csv(tree / "decisions_3factor.csv", 1_500_000):
        ts = parse_ts(row.get("ts"))
        if ts is None or now - ts > 24 * HOUR:
            continue
        if row.get("skip_stage") == "AI" and str(row.get("ai_error")).lower() != "true":
            out["ai_success_ts"] = max(out["ai_success_ts"] or 0.0, ts)
            out["ai_events"].append({"ts": ts, "ok": True, "direction": row.get("final_direction") or None})
        elif str(row.get("ai_error")).lower() == "true" or row.get("skip_stage") == "AI_ERROR":
            out["ai_failure_ts"] = max(out["ai_failure_ts"] or 0.0, ts)
    for row in tail_csv(tree / "ai_errors_3factor.csv", 300_000):
        ts = parse_ts(row.get("ts"))
        if ts is not None and now - ts <= 24 * HOUR:
            out["ai_failure_ts"] = max(out["ai_failure_ts"] or 0.0, ts)
            out["ai_events"].append({"ts": ts, "ok": False, "direction": None})
    for row in tail_csv(tree / "trades_3factor.csv", 3_000_000):
        tid, lane, ts = row.get("trade_id"), row.get("research_lane"), parse_ts(row.get("ts"))
        if not tid or ts is None:
            continue
        try:
            entry = ts - float(row.get("dur_min") or 0.0) * MIN
        except ValueError:
            entry = ts
        out["filled"][tid] = ts
        if lane:
            out["lane_orders"].setdefault(lane, []).append(entry)
    for row in tail_csv(tree / "expired_orders_3factor.csv", 1_000_000):
        tid, lane = row.get("trade_id"), row.get("research_lane")
        created = parse_ts(row.get("created_ts")) or parse_ts(row.get("time"))
        expired = parse_ts(row.get("expired_ts")) or parse_ts(row.get("time"))
        if tid and expired is not None:
            out["expired"][tid] = expired
        if lane and created is not None:
            out["lane_orders"].setdefault(lane, []).append(created)
    return out


def collect_exports(path: Path, now: float) -> dict[str, Any]:
    if not path.exists():
        return {"present": False}
    newest = None
    count = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                count += 1
                newest = max(newest or 0.0, entry.stat().st_mtime)
                if count > 5000:
                    break
    except OSError:
        pass
    manifest = None
    for name in ("manifest.json", "export-manifest.json", "latest.json"):
        manifest = read_json(path / name)
        if isinstance(manifest, dict):
            break
    generated = None
    if isinstance(manifest, dict):
        generated = parse_ts(manifest.get("generated_at") or manifest.get("generatedAt") or manifest.get("at"))
    client_status = None
    try:  # worker eed92197's analyzer_client, when present, is the authority.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import analyzer_client  # type: ignore  # noqa: PLC0415

        for name in ("exports_status", "export_status", "health"):
            func = getattr(analyzer_client, name, None)
            if callable(func):
                client_status = func()
                break
    except Exception:  # noqa: BLE001 - optional integration must never fail the tick
        client_status = None
    return {"present": True, "files": count, "newest_mtime": newest,
            "generated_at": generated or newest, "manifest": bool(manifest), "client_status": client_status}


def last_supervisor_tick(log_dir: Path) -> float | None:
    newest = None
    for log in sorted(log_dir.glob("laptop-chain-supervisor-*.log"))[-2:]:
        try:
            with open(log, "rb") as handle:
                handle.seek(max(0, handle.seek(0, os.SEEK_END) - 20_000))
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if " TICK " in line:
                ts = parse_ts(line.split(" ", 1)[0])
                if ts:
                    newest = max(newest or 0.0, ts)
    return newest


def collect(opts: argparse.Namespace, state: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Gather every input. Each source fails independently into ``errors``."""
    now = utcnow() if now is None else now
    state_dir = Path(opts.state_dir)
    vault = load_vault(opts.vault)
    admin = os.environ.get("BOT_ADMIN_TOKEN") or vault.get("BOT_ADMIN_TOKEN")
    inputs: dict[str, Any] = {"now": now, "errors": {}}
    errors = inputs["errors"]

    def fetch(key: str, url: str, **kw: Any) -> None:
        payload, err = http_json(url, **kw)
        inputs[key] = payload
        if err:
            errors[key] = err

    fetch("fly_status", f"{opts.fly_url}/api/status", timeout=30)
    fetch("fly_health", f"{opts.fly_url}/health", timeout=30)
    if admin:
        fetch("fly_state", f"{opts.fly_url}/api/state", headers={"X-Bot-Admin-Token": admin}, timeout=60)
    else:
        inputs["fly_state"], errors["fly_state"] = None, "ADMIN_TOKEN_MISSING"
    fetch("analyzer_api", f"{opts.analyzer_url}/api/health", timeout=10)
    base = os.environ.get("PLATFORM_API_BASE_URL") or vault.get("PLATFORM_API_BASE_URL")
    if base:
        fetch("railway_health", f"{base.rstrip('/')}/health", timeout=20)
    else:
        inputs["railway_health"], errors["railway_health"] = None, "CONFIG_MISSING"

    shadow = Path(opts.shadow_root)
    files = {
        "pull_status": state_dir / "segment-pull.status.json",
        "head_snapshot": state_dir / "fly_segment_head_snapshot_v1.json",
        "relay_snapshot": state_dir / "relay_status_snapshot_v1.json",
        "runtime_snapshot": state_dir / "fly_runtime_snapshot_v1.json",
        "deploy_runs": state_dir / "fly_deploy_runs_snapshot_v1.json",
        "analyzer_status": state_dir / "analyzer-run.status.json",
        "cycle_status": state_dir / "segment-analyzer-cycle.status.json",
        "proof_active": state_dir / "unattended-proof" / "active.json",
        "puller_status": shadow / ".puller" / "status.json",
    }
    for key, path in files.items():
        inputs[key] = read_json(path)
    receipts = shadow / ".puller" / "ack-receipts.jsonl"
    try:
        with open(receipts, "rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - 4096))
            last = [l for l in handle.read().decode("utf-8", "replace").splitlines() if l.strip()][-1]
        inputs["ack_receipt"] = json.loads(last)
    except (OSError, ValueError, IndexError):
        inputs["ack_receipt"] = None
    inputs["proof_last_row"] = latest_proof_row(inputs.get("proof_active"), Path(opts.proof_dir))
    inputs["supervisor_tick_at"] = last_supervisor_tick(state_dir / "logs")

    inputs["mirror"] = collect_mirror(Path(opts.mirror_tree), now)
    inputs["exports"] = collect_exports(Path(opts.exports), now)
    cache = state.setdefault("cache", {})
    inputs["registry"] = collect_registry(opts.analyzer_repo, cache.setdefault("registry", {}))
    inputs["analyzer_head"] = _git(opts.analyzer_repo, "rev-parse", "HEAD")
    fly_rev = str(dig(inputs.get("fly_status"), "git_rev") or dig(inputs.get("fly_health"), "git_rev") or "")
    if fly_rev and inputs["analyzer_head"]:
        # Same rule as run-segment-analyzer-cycle.ps1: laptop-only merges on top of
        # the deployed revision are fine.
        inputs["analyzer_contains_fly"] = _git(opts.analyzer_repo, "merge-base", "--is-ancestor",
                                               fly_rev, "HEAD") is not None
    master = cache.get("master") or {}
    if now - float(master.get("at") or 0) > 10 * MIN:
        line = _git(opts.analyzer_repo, "ls-remote", "origin", "refs/heads/master", timeout=30)
        if line:
            master = {"at": now, "sha": line.split()[0]}
            cache["master"] = master
    inputs["master_sha"] = master.get("sha")
    try:
        usage = shutil.disk_usage(Path(opts.state_dir).anchor or "C:\\")
        inputs["laptop_disk"] = {"free": usage.free, "total": usage.total}
    except OSError:
        inputs["laptop_disk"] = None
    inputs["neon"] = collect_neon(cache)
    inputs["deepseek_balance"] = collect_deepseek_balance(vault, cache, now)
    return inputs


def parse_deepseek_balance(payload: Any, err: str | None, now: float, source: str) -> dict[str, Any]:
    """Bounded summary of GET /user/balance; upstream text is never echoed."""
    out: dict[str, Any] = {"checked_at": now, "source": source}
    if err:
        return {**out, "error": err}
    infos = dig(payload, "balance_infos", default=[]) or []
    usd = next((i for i in infos if isinstance(i, Mapping) and str(i.get("currency")).upper() == "USD"), None)
    try:
        total = float(usd["total_balance"]) if usd else None
    except (KeyError, TypeError, ValueError):
        total = None
    if total is None:
        return {**out, "error": "NO_USD_BALANCE"}
    return {**out, "total_usd": total, "is_available": bool(dig(payload, "is_available", default=False))}


def collect_deepseek_balance(vault: Mapping[str, str], cache: dict[str, Any], now: float) -> dict[str, Any]:
    """Read-only balance GET, cached. The key goes only into the request header."""
    cached = cache.get("deepseek_balance")
    if isinstance(cached, Mapping) and now - float(cached.get("checked_at") or 0) < THRESHOLDS["deepseek_balance_cache_sec"]:
        return dict(cached)
    key = os.environ.get("DEEPSEEK_API_KEY") or vault.get("DEEPSEEK_API_KEY")
    if not key:
        return {"checked_at": now, "source": "laptop", "error": "KEY_MISSING"}
    payload, err = http_json(DEEPSEEK_BALANCE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    result = parse_deepseek_balance(payload, err, now, "laptop")
    cache["deepseek_balance"] = result
    return result


def collect_neon(cache: dict[str, Any]) -> dict[str, Any] | None:
    """Neon consumption, only when NEON_API_KEY + NEON_PROJECT_ID are configured."""
    key, project = os.environ.get("NEON_API_KEY"), os.environ.get("NEON_PROJECT_ID")
    if not key or not project:
        return None
    payload, err = http_json(f"https://console.neon.tech/api/v2/projects/{project}",
                             headers={"Authorization": f"Bearer {key}"}, timeout=20)
    if err:
        return {"error": err}
    proj = payload.get("project") or {}
    return {k: proj.get(k) for k in ("data_transfer_bytes", "compute_time_seconds",
                                     "active_time_seconds", "written_data_bytes",
                                     "consumption_period_start")}


def latest_proof_row(active: Any, proof_dir: Path) -> dict[str, Any] | None:
    receipt = Path(active["receipt"]) if isinstance(active, dict) and active.get("receipt") else None
    if receipt is None or not receipt.exists():
        candidates = sorted(proof_dir.glob("unattended-proof-*.jsonl"))
        receipt = candidates[-1] if candidates else None
    if receipt is None:
        return None
    try:
        with open(receipt, "rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - 20_000))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "ROW":
            return row
    return None


# --------------------------------------------------------------- evaluation

def _ai_provider_health(status: Any, state: Any) -> dict[str, Any] | None:
    """Provider-truth block from the AI repair (c609373e) when Fly exposes it."""
    for source in (dig(status, "strategy_progress"), status, state, dig(state, "strategy_progress")):
        block = dig(source, "ai_provider_health")
        if isinstance(block, Mapping):
            return dict(block)
    return None


def evaluate(inputs: Mapping[str, Any], state: dict[str, Any], thresholds: Mapping[str, float] | None = None
             ) -> list[dict[str, Any]]:
    """Pure evaluation of every check. ``state`` carries progress memory."""
    t = {**THRESHOLDS, **(thresholds or {})}
    now = float(inputs["now"])
    errors = inputs.get("errors") or {}
    mem = state.setdefault("memory", {})
    status = inputs.get("fly_status") if isinstance(inputs.get("fly_status"), Mapping) else None
    health = inputs.get("fly_health") if isinstance(inputs.get("fly_health"), Mapping) else None
    fstate = inputs.get("fly_state") if isinstance(inputs.get("fly_state"), Mapping) else None
    progress = dig(status, "strategy_progress", default={})
    paused = bool(dig(status, "execution_paused", default=dig(health, "execution_paused", default=False)))
    pause_owner = dig(status, "pause_owner", default=dig(health, "pause_owner")) or ""
    checks: list[dict[str, Any]] = []
    add = checks.append

    # ---------------- Fly
    reachable = status is not None and health is not None
    if reachable:
        mem["fly_unreachable_ticks"] = 0
        alive = bool(dig(status, "process_alive", default=True))
        ready = dig(status, "system_ready")
        st = GREEN if alive else RED
        add(check("fly.process", "fly", st, f"alive={alive} system_ready={ready} rev={dig(status, 'git_rev')}",
                  "process_alive and /api/status + /health answer",
                  "" if alive else "Fly process reports not alive"))
    else:
        mem["fly_unreachable_ticks"] = int(mem.get("fly_unreachable_ticks") or 0) + 1
        add(check("fly.process", "fly", RED, f"unreachable ({errors.get('fly_status') or errors.get('fly_health')})",
                  "/api/status and /health answer",
                  "Fly app down, restarting, or the laptop is offline"))

    if reachable:
        if paused:
            since = mem.setdefault("paused_since", now)
            age = now - float(since)
            deploy = str(pause_owner).upper() == "DEPLOY_MAINTENANCE"
            st = AMBER if deploy and age <= t["deploy_pause_max_sec"] else RED
            add(check("fly.paused", "fly", st, f"paused owner={pause_owner or 'none'} for {fmt_age(age)}",
                      f"unpaused; DEPLOY_MAINTENANCE <= {fmt_age(t['deploy_pause_max_sec'])}",
                      "guarded deploy boundary" if deploy else
                      "safety/admin pause (THREAD_CRASH, SAFETY, ADMIN_MANUAL) or a deploy that never resumed",
                      dig(status, "execution_reason", default="")))
        else:
            mem.pop("paused_since", None)
            add(check("fly.paused", "fly", GREEN, "paper running (execution_paused=false)", "unpaused"))

    fly_rev = str(dig(status, "git_rev") or dig(health, "git_rev") or "")
    master = str(inputs.get("master_sha") or "")
    runs = dig(inputs.get("deploy_runs"), "runs", default=[]) or []
    runs = sorted([r for r in runs if isinstance(r, Mapping)], key=lambda r: str(r.get("createdAt")), reverse=True)
    last_run = runs[0] if runs else {}
    failed = last_run.get("status") == "completed" and last_run.get("conclusion") not in (None, "success")
    if not fly_rev:
        add(check("fly.revision", "fly", SKIP if not reachable else AMBER, "unknown", "Fly git_rev reported"))
    else:
        same = bool(master) and master.startswith(fly_rev)
        obs = f"fly={fly_rev[:12]} master={master[:12] or '?'}{' (current)' if same else ' (master ahead; deploy queued)'}"
        if failed:
            obs += f"; last guarded deploy {last_run.get('databaseId')} concluded {last_run.get('conclusion')}"
        add(check("fly.revision", "fly", AMBER if failed else GREEN, obs,
                  "Fly runs a successfully deployed master revision",
                  "last guarded deploy failed; Fly still on the previous revision" if failed else ""))

    # ---------------- AI
    prov = _ai_provider_health(status, fstate)
    history = dig(fstate, "ai_history", default=[]) or []
    events = mem.setdefault("ai_events", [])
    seen = {e["ts"] for e in events}
    for entry in history:
        ts = parse_ts(entry.get("time") or entry.get("ts"))
        if ts is None or ts in seen:
            continue
        events.append({"ts": ts, "ok": not bool(entry.get("ai_error")),
                       "direction": entry.get("ai_direction_raw") or entry.get("final_direction")})
        seen.add(ts)
    mirror = inputs.get("mirror") or {}
    for entry in mirror.get("ai_events") or []:
        if entry["ts"] not in seen:
            events.append(dict(entry))
            seen.add(entry["ts"])
    events.sort(key=lambda e: e["ts"])
    del events[:-400]
    events[:] = [e for e in events if now - e["ts"] <= 24 * HOUR]
    success_ts = [e["ts"] for e in events if e["ok"]]
    candidates = [mem.get("ai_last_success_ts"), mirror.get("ai_success_ts"), max(success_ts) if success_ts else None,
                  parse_ts(dig(prov, "last_ai_success_ts") or dig(prov, "last_success_ts"))]
    last_success = max([c for c in candidates if c] or [0.0]) or None
    if last_success:
        mem["ai_last_success_ts"] = last_success
    trailing_failures = 0
    for e in reversed(events):
        if e["ok"]:
            break
        trailing_failures += 1
    if prov and prov.get("consecutive_failures") is not None:
        trailing_failures = max(trailing_failures, int(prov.get("consecutive_failures") or 0))
    startup_age = float(dig(progress, "process_startup_age_sec", default=1e9) or 1e9)
    ai_expected = reachable and not paused and startup_age > t["ai_success_red_sec"]
    age = (now - last_success) if last_success else None
    if not reachable:
        add(check("ai.success", "ai", SKIP, "Fly unreachable", f"success within {fmt_age(t['ai_success_red_sec'])}"))
    else:
        st = GREEN
        if age is None or age > t["ai_success_red_sec"]:
            st = RED if ai_expected else AMBER
        elif age > t["ai_success_amber_sec"]:
            st = AMBER
        attempt_age = dig(progress, "ai_age_sec")
        add(check("ai.success", "ai", st,
                  f"last SUCCESSFUL model response {fmt_age(age)} ago (last attempt {fmt_age(attempt_age)} ago)",
                  f"<= {fmt_age(t['ai_success_amber_sec'])} AMBER / {fmt_age(t['ai_success_red_sec'])} RED",
                  "" if st == GREEN else
                  "DeepSeek calls timing out/failing (attempts still advance ai_age); check egress, key, provider status",
                  f"provider_health={'present' if prov else 'not exposed yet (c609373e)'}"))
        st = RED if trailing_failures >= t["ai_consecutive_failures_red"] else AMBER if trailing_failures else GREEN
        add(check("ai.failures", "ai", st, f"{trailing_failures} consecutive failed AI calls",
                  f"< {int(t['ai_consecutive_failures_red'])}",
                  "" if st == GREEN else "provider errors (ReadTimeout/HTTP) on consecutive calls"))
    window = [e for e in events if e["ok"] and now - e["ts"] <= t["ai_neutral_window_sec"] and e.get("direction")]
    neutral = [e for e in window if str(e["direction"]).upper() in {"NO_TRADE", "NEUTRAL", "HOLD", "NONE"}]
    if len(window) < t["ai_neutral_min_samples"]:
        add(check("ai.decision_mix", "ai", SKIP if not window else GREEN,
                  f"{len(neutral)}/{len(window)} neutral in {fmt_age(t['ai_neutral_window_sec'])}",
                  f"not 100% neutral over >= {int(t['ai_neutral_min_samples'])} responses"))
    else:
        all_neutral = len(neutral) == len(window)
        add(check("ai.decision_mix", "ai", AMBER if all_neutral else GREEN,
                  f"{len(neutral)}/{len(window)} neutral in {fmt_age(t['ai_neutral_window_sec'])}",
                  "not 100% neutral",
                  "model returns only NO_TRADE: prompt/parse regression or dead inputs" if all_neutral else ""))

    echo = str(dig(prov, "last_model_echo") or "") or None
    fingerprint = dig(prov, "last_system_fingerprint")
    configured = str(dig(prov, "configured_model") or "") or EXPECTED_DEEPSEEK_MODEL
    previous = mem.get("ai_served_model")
    if echo and previous and echo != previous:
        mem["ai_served_model_changed_at"] = now
        mem["ai_served_model_previous"] = previous
    if echo:
        mem["ai_served_model"] = echo
    changed_at = mem.get("ai_served_model_changed_at")
    change_from = mem.get("ai_served_model_previous")
    for change in dig(prov, "served_model_changes", default=[]) or []:
        at = parse_ts(dig(change, "at"))
        if at and (changed_at is None or at > float(changed_at)):
            changed_at, change_from = at, dig(change, "from")
    recent_change = changed_at is not None and now - float(changed_at) <= t["served_model_change_amber_sec"]
    if not reachable or not echo:
        add(check("ai.served_model", "ai", SKIP, "Fly unreachable" if not reachable else "no served model echoed yet",
                  f"served model == configured ({configured})"))
    else:
        st = AMBER if echo != configured or recent_change else GREEN
        obs = f"served={echo} configured={configured} fingerprint={fingerprint or '?'}"
        if recent_change:
            obs += f"; changed {change_from}->{echo} at {iso(float(changed_at))}"
        add(check("ai.served_model", "ai", st, obs,
                  f"served == configured, no change within {fmt_age(t['served_model_change_amber_sec'])}",
                  "" if st == GREEN else
                  "DeepSeek is serving a different model than configured (silent alias/retirement); "
                  "analyzer splits cohorts by served model"))

    fly_balance = dig(prov, "deepseek_balance")
    balance = inputs.get("deepseek_balance") if isinstance(inputs.get("deepseek_balance"), Mapping) else None
    if (isinstance(fly_balance, Mapping) and fly_balance.get("total_usd") is not None
            and now - (parse_ts(fly_balance.get("checked_at")) or 0) <= t["deepseek_balance_fly_max_age_sec"]):
        balance = {**fly_balance, "source": "fly"}
    threshold = f">= ${t['deepseek_balance_amber_usd']:.2f} (AMBER < ${t['deepseek_balance_amber_usd']:.2f}, RED < ${t['deepseek_balance_red_usd']:.2f})"
    if not balance or balance.get("error") == "KEY_MISSING":
        add(check("deepseek.balance", "ai", SKIP, "balance not observed (no key on laptop, not exposed by Fly)", threshold))
    elif balance.get("total_usd") is None:
        add(check("deepseek.balance", "ai", AMBER, f"balance unknown ({balance.get('error')})", threshold,
                  "DeepSeek /user/balance unreachable or changed shape"))
    else:
        total = float(balance["total_usd"])
        available = balance.get("is_available") is not False
        st = (RED if total < t["deepseek_balance_red_usd"] or not available
              else AMBER if total < t["deepseek_balance_amber_usd"] else GREEN)
        add(check("deepseek.balance", "ai", st,
                  f"${total:.2f} USD is_available={available} (source={balance.get('source')})", threshold,
                  "" if st == GREEN else "top up DeepSeek; at $0 every AI call fails with HTTP 402"))

    # ---------------- Trading
    toggles = dig(fstate, "research_lane_enabled", default=None) or dig(inputs.get("runtime_snapshot"),
                                                                          "research_lane_enabled", default={}) or {}
    on_lanes = sorted(k for k, v in toggles.items() if v is True)
    route_counts = dig(fstate, "tile_route_counts", default={}) or {}
    instance = str(dig(status, "bot_instance_id") or "")
    lane_mem = mem.setdefault("lanes", {})
    lane_evidence = {lane: list(ts) for lane, ts in (mirror.get("lane_orders") or {}).items()}
    for item in (dig(fstate, "trades", default=[]) or []):
        lane, ts = item.get("research_lane"), parse_ts(item.get("ts"))
        if lane and ts:
            lane_evidence.setdefault(lane, []).append(ts - float(item.get("dur_min") or 0) * MIN)
    for item in (dig(fstate, "positions", default=[]) or []) + (dig(fstate, "orders", default=[]) or []):
        lane = item.get("research_lane")
        ts = parse_ts(item.get("order_created_ts") or item.get("created_ts") or item.get("entry_ts"))
        if lane and ts:
            lane_evidence.setdefault(lane, []).append(ts)
    for lane in set(on_lanes) | set(route_counts):
        lm = lane_mem.setdefault(lane, {})
        counts = route_counts.get(lane) or {}
        total = sum(int(counts.get(k) or 0) for k in ("closed", "expired", "open", "pending"))
        if lm.get("instance") == instance and total > int(lm.get("total") or 0):
            lm["last_order_ts"] = now
        if lm.get("instance") != instance or total != lm.get("total"):
            lm["instance"], lm["total"] = instance, total
        ev = [x for x in lane_evidence.get(lane, []) if x and x <= now]
        if ev:
            lm["last_order_ts"] = max(float(lm.get("last_order_ts") or 0.0), max(ev))
        lm["orders_48h"] = len([x for x in lane_evidence.get(lane, []) if now - x <= 48 * HOUR])
    if not reachable or fstate is None:
        add(check("trading.orders", "trading", SKIP, f"no /api/state ({errors.get('fly_state')})",
                  f"an order within {fmt_age(t['orders_red_sec'])} while tiles ON"))
    elif not on_lanes:
        add(check("trading.orders", "trading", GREEN, "all tiles OFF (no orders expected)",
                  f"an order within {fmt_age(t['orders_red_sec'])} while tiles ON"))
    else:
        quiet: dict[str, float | None] = {}
        for lane in on_lanes:
            last = lane_mem.get(lane, {}).get("last_order_ts")
            quiet[lane] = (now - float(last)) if last else None
        newest = min((q for q in quiet.values() if q is not None), default=None)
        all_quiet = newest is None or newest > t["orders_red_sec"]
        st = RED if (all_quiet and not paused) else GREEN
        per = ", ".join(f"{lane}:{fmt_age(q)}/{lane_mem.get(lane, {}).get('orders_48h', 0)}@48h"
                        for lane, q in quiet.items())
        quiet_tiles = [l for l, q in quiet.items() if q is None or q > t["tile_quiet_amber_sec"]]
        if st == GREEN and quiet_tiles:
            st = AMBER
        add(check("trading.orders", "trading", st,
                  f"newest order {fmt_age(newest)} ago across {len(on_lanes)} ON tiles; per tile last/48h: {per}",
                  f"any ON tile ordered within {fmt_age(t['orders_red_sec'])} (RED); each tile within "
                  f"{fmt_age(t['tile_quiet_amber_sec'])} (AMBER)",
                  "" if st == GREEN else
                  ("no tile is placing orders: AI failing, admission gates rejecting everything, or execution wedged"
                   if st == RED else f"quiet tiles {quiet_tiles}: regime gates may be legitimately closed; "
                                     "compare shared_ai_lane_counters reasons")))
    orphans = (dig(fstate, "orphan_order_ids", default=[]) or []) + (dig(fstate, "orphan_position_ids", default=[]) or [])
    if fstate is None:
        add(check("trading.orphans", "trading", SKIP, "no /api/state", "0 orphan orders/positions"))
    else:
        add(check("trading.orphans", "trading", RED if orphans else GREEN, f"{len(orphans)} orphan ids {orphans[:5]}",
                  "0 orphan orders/positions",
                  "" if not orphans else "order/position without a lifecycle owner; reconcile before any deploy"))
    expired = dict(mirror.get("expired") or {})
    filled = dict(mirror.get("filled") or {})
    for item in dig(fstate, "expired_orders", default=[]) or []:
        if item.get("trade_id"):
            expired[item["trade_id"]] = parse_ts(item.get("expired_ts") or item.get("time")) or now
    for item in (dig(fstate, "trades", default=[]) or []) + (dig(fstate, "positions", default=[]) or []):
        if item.get("trade_id"):
            filled[item["trade_id"]] = parse_ts(item.get("ts") or item.get("fill_ts")) or now
    both = {tid: max(expired[tid] or 0, filled[tid] or 0) for tid in set(expired) & set(filled)}
    recent_red = sorted(tid for tid, ts in both.items() if now - ts <= t["contradiction_red_window_sec"])
    recent_amber = sorted(tid for tid, ts in both.items() if now - ts <= t["contradiction_amber_window_sec"])
    unlinked = sum(int((v or {}).get("unlinked_lifecycle_rows") or 0) for v in route_counts.values())
    st = RED if recent_red else AMBER if (recent_amber or unlinked) else GREEN
    add(check("trading.lifecycle", "trading", st,
              f"{len(recent_amber)} expired+filled contradictions in 24h ({len(recent_red)} in "
              f"{fmt_age(t['contradiction_red_window_sec'])}) {recent_amber[:4]}; unlinked lifecycle rows={unlinked}",
              "0 trade ids both EXPIRED and FILLED",
              "" if st == GREEN else "fill-vs-TTL race (see #253/#255): fill thread latency lets TTL expire a filled order"))

    # ---------------- WebSocket / trade ticks
    if not reachable:
        add(check("ws.ticks", "ws", SKIP, "Fly unreachable", f"<= {t['ws_amber_sec']:.0f}s"))
    else:
        ws_age = dig(status, "ws_age")
        ws_age = float(ws_age) if isinstance(ws_age, (int, float)) else None
        progressing = dig(progress, "ws_progressing", default=True)
        st = GREEN
        if ws_age is None or ws_age > t["ws_red_sec"] or progressing is False:
            st = RED
        elif ws_age > t["ws_amber_sec"]:
            st = AMBER
        add(check("ws.ticks", "ws", st, f"trade tick {fmt_age(ws_age)} old, ws_progressing={progressing}",
                  f"<= {t['ws_amber_sec']:.0f}s AMBER / {t['ws_red_sec']:.0f}s RED",
                  "" if st == GREEN else "Bitfinex WS disconnected/stalled; REST fallback only"))

    # ---------------- Shipper
    transfer = dig(health, "volume", "transfer", default=None) or (inputs.get("head_snapshot") if
                                                                   dig(inputs.get("head_snapshot"), "ok") else None)
    if not transfer:
        add(check("shipper.progress", "shipper", SKIP if not reachable else RED, "no transfer block",
                  "segments shipping"))
    else:
        shipped = transfer.get("shipped_seq")
        last_seg = parse_ts(transfer.get("last_segment_at"))
        unshipped = int(transfer.get("unshipped_bytes") or 0)
        err = transfer.get("last_error")
        if shipped is not None and shipped != mem.get("shipped_seq"):
            mem["shipped_seq"], mem["shipped_seq_changed_ts"] = shipped, now
        seg_age = (now - last_seg) if last_seg else None
        status_age = transfer.get("segment_status_age_sec")
        backlog = unshipped > 0 or bool(err)
        st = GREEN
        if seg_age is not None and err and seg_age > t["shipper_stall_red_sec"]:
            st = RED
        elif seg_age is not None and backlog and seg_age > t["shipper_stall_backlog_red_sec"]:
            st = RED
        elif isinstance(status_age, (int, float)) and status_age > t["shipper_status_red_sec"]:
            st = RED
        elif (seg_age is not None and backlog and seg_age > t["shipper_stall_amber_sec"]) or err or \
                unshipped > t["unshipped_amber_bytes"]:
            st = AMBER
        if not transfer.get("segments_enabled", True):
            st = RED
        add(check("shipper.progress", "shipper", st,
                  f"shipped_seq={shipped} last segment {fmt_age(seg_age)} ago, unshipped={unshipped / 1e6:.1f}MB, "
                  f"last_error={err or 'none'}, status age={fmt_age(status_age)}",
                  f"new segment within {fmt_age(t['shipper_stall_red_sec'])} while last_error, within "
                  f"{fmt_age(t['shipper_stall_backlog_red_sec'])} while backlog (RED)",
                  "" if st == GREEN else
                  ("PLAN_RACE/hot stream aborting every cycle or shipper thread dead; ACK will freeze"
                   if st == RED else "backlog building or transient PLAN_RACE")))

    # ---------------- Laptop pull / ACK
    pull = inputs.get("pull_status") or {}
    puller = inputs.get("puller_status") or {}
    receipt = inputs.get("ack_receipt") or {}
    published = dig(transfer, "shipped_seq") if transfer else None
    applied = puller.get("applied_seq") if puller.get("applied_seq") is not None else pull.get("appliedSeq")
    fly_acked = dig(transfer, "laptop_acked_seq") if transfer else None
    finished = parse_ts(pull.get("finishedAt"))
    if applied is not None and published is not None and int(applied) >= int(published):
        mem["pull_caught_up_ts"] = now
    if fly_acked is not None and applied is not None and int(fly_acked) >= int(applied):
        mem["ack_caught_up_ts"] = now
    # While Fly ships every few minutes and polls ACKs every ~5m, the trailing
    # counter can chase the leading one indefinitely without ever matching at a
    # tick. Lag is only a custody risk when the trailing counter stops advancing.
    if applied is not None and applied != mem.get("applied_seq"):
        mem["applied_seq"], mem["applied_changed_ts"] = applied, now
    if fly_acked is not None and fly_acked != mem.get("fly_acked_seq"):
        mem["fly_acked_seq"], mem["fly_acked_changed_ts"] = fly_acked, now
    pull_lag = (int(published) - int(applied)) if (published is not None and applied is not None) else None
    ack_lag = (int(applied) - int(fly_acked)) if (applied is not None and fly_acked is not None) else None
    behind_for = now - float(max(mem.setdefault("pull_caught_up_ts", now), mem.get("applied_changed_ts") or 0))
    ack_behind_for = now - float(max(mem.setdefault("ack_caught_up_ts", now), mem.get("fly_acked_changed_ts") or 0))
    finished_age = (now - finished) if finished else None
    reasons = []
    if finished_age is None or finished_age > t["pull_finished_red_sec"]:
        reasons.append(f"no finished pull for {fmt_age(finished_age)}")
    if pull_lag is not None and (pull_lag > t["pull_lag_seq_red"] or (pull_lag > 0 and behind_for > t["pull_lag_red_sec"])):
        reasons.append(f"applied behind published by {pull_lag} for {fmt_age(behind_for)}")
    if ack_lag is not None and ack_lag > t["ack_lag_seq_red"]:
        reasons.append(f"Fly laptop_acked behind applied by {ack_lag} segments")
    elif ack_lag and ack_lag > 0 and ack_behind_for > t["ack_lag_red_sec"]:
        reasons.append(f"Fly laptop_acked behind applied and not advancing for {fmt_age(ack_behind_for)}")
    st = RED if reasons else GREEN
    if st == GREEN and pull_lag:
        st = AMBER if behind_for > 5 * MIN else GREEN
    add(check("laptop.pull_ack", "laptop", st,
              f"published={published} applied={applied} fly_acked={fly_acked} last pull {fmt_age(finished_age)} ago"
              f" (last receipt through {receipt.get('through_seq')})",
              f"applied advancing or ==published within {fmt_age(t['pull_lag_red_sec'])}, Fly ACK advancing within "
              f"{fmt_age(t['ack_lag_red_sec'])} and <= {t['ack_lag_seq_red']} behind applied",
              "; ".join(reasons) or ("" if st == GREEN else "pull catching up"),
              str(pull.get("error") or "")[:200]))
    sup = inputs.get("supervisor_tick_at")
    sup_age = (now - sup) if sup else None
    add(check("laptop.supervisor", "laptop", RED if sup_age is None or sup_age > t["supervisor_tick_red_sec"] else GREEN,
              f"last DoxxedLaptopChainSupervisor tick {fmt_age(sup_age)} ago",
              f"<= {fmt_age(t['supervisor_tick_red_sec'])}",
              "" if sup_age is not None and sup_age <= t["supervisor_tick_red_sec"]
              else "scheduled task disabled, laptop asleep, or tick erroring"))

    # ---------------- Analyzer
    an = inputs.get("analyzer_status") or {}
    cyc = inputs.get("cycle_status") or {}
    gen = parse_ts(an.get("lastCompletedGenerationAt") or an.get("lastSuccessAt"))
    gen_age = (now - gen) if gen else None
    st = RED if gen_age is None or gen_age > t["analyzer_gen_red_sec"] else AMBER if gen_age > t["analyzer_gen_amber_sec"] else GREEN
    add(check("analyzer.generation", "analyzer", st,
              f"last completed generation {fmt_age(gen_age)} ago (state={an.get('state')} exit={an.get('exitCode')})",
              f"<= {fmt_age(t['analyzer_gen_amber_sec'])} AMBER / {fmt_age(t['analyzer_gen_red_sec'])} RED",
              "" if st == GREEN else "analyzer crash/stall or cycle blocked on promotion/migration",
              str(an.get("detail") or "")[:200]))
    api = inputs.get("analyzer_api") if isinstance(inputs.get("analyzer_api"), Mapping) else None
    in_cycle = cyc.get("finishedAt") is None and cyc.get("startedAt") is not None
    if api is None:
        since = mem.setdefault("analyzer_api_down_since", now)
        down = now - float(since)
        st = AMBER if (in_cycle and down <= t["analyzer_api_down_red_sec"]) else RED
        add(check("analyzer.api", "analyzer", st, f":9001 unreachable for {fmt_age(down)} (cycle phase={cyc.get('phase')})",
                  f"answers; down <= {fmt_age(t['analyzer_api_down_red_sec'])} only while a cycle replaces it",
                  "dashboard replaced during the ANALYZER phase" if st == AMBER else "analyzer dashboard crashed"))
    else:
        mem.pop("analyzer_api_down_since", None)
        parity = api.get("source_revision_parity")
        parity_ok = parity.get("match", parity.get("ok", True)) if isinstance(parity, Mapping) else True
        ok = bool(api.get("ok")) and bool(api.get("runtime_sync_match", True)) and parity_ok is not False
        add(check("analyzer.api", "analyzer", GREEN if ok else AMBER,
                  f"ok={api.get('ok')} sync_match={api.get('runtime_sync_match')} revision_parity={parity_ok}",
                  "ready, sync match, revision parity",
                  "" if ok else "analyzer generation not current or revision/epoch parity mismatch"))
    started = parse_ts(cyc.get("startedAt"))
    if in_cycle and started:
        dur = now - started
        st = RED if dur > t["cycle_red_sec"] else AMBER if dur > t["cycle_amber_sec"] else GREEN
        obs = f"cycle running {fmt_age(dur)} (phase={cyc.get('phase')})"
    else:
        fin = parse_ts(cyc.get("finishedAt"))
        dur = (fin - started) if (fin and started) else None
        code = cyc.get("exitCode")
        st = AMBER if code not in (None, 0) else GREEN
        obs = f"last cycle {fmt_age(dur)} exit={code}"
    head = str(inputs.get("analyzer_head") or "")
    if fly_rev and head and not head.startswith(fly_rev):
        if inputs.get("analyzer_contains_fly"):
            obs += f"; analyzer rev {head[:12]} contains Fly {fly_rev[:12]}"
        else:
            st = max(st, AMBER, key=RANK.get)
            obs += f"; analyzer rev {head[:12]} does not contain Fly {fly_rev[:12]}"
    add(check("analyzer.cycle", "analyzer", st, obs,
              f"cycle <= {fmt_age(t['cycle_amber_sec'])}, exit 0, analyzer rev contains Fly rev",
              "" if st == GREEN else "slow promotion/migration, failed cycle, or v2c auto-ff not yet followed Fly"))

    # ---------------- Exports (worker eed92197)
    exp = inputs.get("exports") or {}
    if not exp.get("present"):
        add(check("exports.freshness", "exports", SKIP, "analyzer-exports\\latest not provisioned yet",
                  f"<= {fmt_age(t['exports_amber_sec'])}"))
    else:
        gen_at = exp.get("generated_at")
        age = (now - gen_at) if gen_at else None
        st = AMBER if age is None or age > t["exports_amber_sec"] else GREEN
        add(check("exports.freshness", "exports", st, f"latest export {fmt_age(age)} old ({exp.get('files')} files)",
                  f"<= {fmt_age(t['exports_amber_sec'])}",
                  "" if st == GREEN else "export step not running after analyzer generations"))

    # ---------------- Data streams (research collection + d9f889db streams)
    coll = dig(status, "collection", "research_coverage", "collection_health", default=None)
    cross = dig(status, "collection", "cross_venue_tape", default=None) or dig(inputs.get("runtime_snapshot"),
                                                                                "cross_venue_health")
    streams = dig(status, "data_streams", default=None) or dig(status, "collection", "data_streams", default=None)
    issues = []
    if isinstance(coll, Mapping) and coll.get("status") not in (None, "OK"):
        issues.append(f"collection {coll.get('status')}: {', '.join(coll.get('alarms') or [])}")
    if isinstance(cross, Mapping) and cross.get("status") not in (None, "OK", "DISABLED"):
        issues.append(f"cross-venue {cross.get('status')} stale={cross.get('stale_venues')}")
    xvl = dig(status, "collection", "xvl_evaluator", default=None) or dig(inputs.get("runtime_snapshot"),
                                                                           "xvl_evaluator_health")
    if isinstance(xvl, Mapping) and xvl.get("status") not in (None, "OK", "DISABLED", "STARTING"):
        issues.append(f"XVL evaluator {xvl.get('status')} ({xvl.get('reason')})")
    dead = dig(inputs.get("runtime_snapshot"), "ai_input_health", default={})
    if isinstance(dead, Mapping) and dead.get("status") == "DEAD_INPUT":
        issues.append(f"AI dead inputs {[d.get('path') for d in dead.get('dead_fields') or []][:5]}")
    per_stream = []
    if isinstance(streams, Mapping):
        for name, info in streams.items():
            if not isinstance(info, Mapping):
                continue
            age = info.get("age_sec", info.get("staleness_sec"))
            cov = info.get("coverage", info.get("coverage_pct"))
            stale = info.get("stale") is True or (isinstance(age, (int, float)) and age > t["streams_stale_amber_sec"])
            per_stream.append(f"{name}:{fmt_age(age) if age is not None else '?'}/{cov if cov is not None else '?'}")
            if stale or str(info.get("status", "OK")).upper() not in ("OK", "GREEN", "DISABLED"):
                issues.append(f"stream {name} stale/unhealthy")
    if status is None:
        add(check("streams.coverage", "streams", SKIP, "Fly unreachable", "all streams fresh"))
    else:
        add(check("streams.coverage", "streams", AMBER if issues else GREEN,
                  "; ".join(issues) or f"collection OK; streams {', '.join(per_stream) or 'n/a (d9f889db pending)'}",
                  f"collection OK, every stream fresher than {fmt_age(t['streams_stale_amber_sec'])}",
                  "" if not issues else "collector worker stalled, tape source missing, or venue feed stale"))

    # ---------------- Dashboards / roster parity
    registry = inputs.get("registry") or {}
    fly_lanes = [str(x.get("lane")) for x in dig(status, "active_tiles", default=[]) or [] if isinstance(x, Mapping)]
    reg_lanes = list(registry.get("lanes") or [])
    toggle_lanes = sorted(toggles)
    contradictions = []
    if reg_lanes and fly_lanes and reg_lanes != fly_lanes:
        contradictions.append(f"Fly roster {fly_lanes} != registry {reg_lanes}")
    if fly_lanes and toggle_lanes and sorted(fly_lanes) != toggle_lanes:
        contradictions.append(f"Fly toggles {toggle_lanes} != active tiles")
    api_health = inputs.get("analyzer_api") if isinstance(inputs.get("analyzer_api"), Mapping) else {}
    epoch = api_health.get("epoch_parity")
    if isinstance(epoch, Mapping) and epoch.get("match", epoch.get("ok", True)) is False:
        contradictions.append(f"analyzer epoch parity mismatch {str(epoch)[:120]}")
    ds = dig(fstate, "dashboard_truth", "deepseek", default=None)
    ai_age_now = (now - last_success) if last_success else None
    if isinstance(ds, Mapping) and str(ds.get("status")).upper() == "OK" and ai_expected and \
            (ai_age_now is None or ai_age_now > t["ai_success_red_sec"]):
        contradictions.append(f"dashboard says '{ds.get('label')}' but last AI success {fmt_age(ai_age_now)} ago")
    lpc = dig(fstate, "lane_position_counts", default={}) or {}
    for lane, counts in route_counts.items():
        lp = lpc.get(lane) or {}
        if lp and (int(lp.get("open") or 0) != int(counts.get("open") or 0)):
            contradictions.append(f"{lane} open {lp.get('open')} (positions) != {counts.get('open')} (tile route)")
    if contradictions:
        since = mem.setdefault("dashboard_contradiction_since", now)
        lasting = now - float(since)
        severe = any("dashboard says" in c for c in contradictions) or lasting > t["registry_mismatch_red_sec"]
        st = RED if severe else AMBER
    else:
        mem.pop("dashboard_contradiction_since", None)
        st = GREEN if (fly_lanes or reg_lanes) else SKIP
    add(check("dashboards.parity", "dashboards", st,
              "; ".join(contradictions) or f"{len(fly_lanes)} tiles agree across Fly API, toggles and registry",
              "Fly dashboard API == registry roster == toggles; truth labels match evidence",
              "" if not contradictions else "stale deploy/registry drift or a truth label built from attempts, not results"))

    # ---------------- Railway relay / API
    relay = inputs.get("relay_snapshot") or {}
    rail = inputs.get("railway_health") if isinstance(inputs.get("railway_health"), Mapping) else None
    observed_at = parse_ts(relay.get("observedAt"))
    snap_age = (now - observed_at) if observed_at else None
    armed = bool(relay.get("relayArmedAt")) or str(relay.get("relayExecutionMode") or relay.get("status") or "").upper() \
        not in ("PAUSED", "DISARMED", "")
    recon = relay.get("reconciliation") or {}
    executor = relay.get("relayExecutor") or {}
    if not relay.get("ok"):
        st, obs = AMBER, f"relay status unreadable ({relay.get('error')})"
    else:
        st = RED if armed or relay.get("positionMismatchAlert") or recon.get("alert") else GREEN
        if st == GREEN and (snap_age is None or snap_age > t["relay_snapshot_amber_sec"] or not executor.get("healthy")
                            or float(executor.get("heartbeatAgeMs") or 0) > t["relay_heartbeat_amber_ms"]):
            st = AMBER
        obs = (f"mode={relay.get('relayExecutionMode')} armedAt={relay.get('relayArmedAt')} executor={executor.get('status')} "
               f"hb={fmt_age((executor.get('heartbeatAgeMs') or 0) / 1000)} snapshot {fmt_age(snap_age)} old")
    add(check("railway.relay", "railway", st, obs,
              "relay PAUSED/disarmed, executor healthy, no reconciliation alert",
              "" if st == GREEN else ("relay ARMED or reconciliation mismatch - verify on Railway immediately"
                                      if st == RED else "relay executor heartbeat/snapshot stale")))
    if rail is None:
        add(check("railway.api", "railway", AMBER if inputs.get("errors", {}).get("railway_health") != "CONFIG_MISSING" else SKIP,
                  f"/health {errors.get('railway_health')}", "api ok, database ok",
                  "Railway API down or redeploying"))
    else:
        db = dig(rail, "services", "database")
        add(check("railway.api", "railway", GREEN if db == "ok" else RED, f"api={dig(rail, 'services', 'api')} database={db}",
                  "api ok, database ok", "" if db == "ok" else "Neon connection failing from Railway"))

    # ---------------- Neon
    neon = inputs.get("neon")
    if neon is None:
        add(check("neon.usage", "neon", SKIP, "not configured (set NEON_API_KEY + NEON_PROJECT_ID)",
                  "egress growth below budget"))
    elif neon.get("error"):
        add(check("neon.usage", "neon", AMBER, f"Neon API {neon['error']}", "readable"))
    else:
        prev = mem.get("neon_prev") or {}
        egress = neon.get("data_transfer_bytes")
        rate = None
        if prev.get("bytes") is not None and egress is not None and now > float(prev.get("ts") or now):
            rate = (float(egress) - float(prev["bytes"])) / ((now - float(prev["ts"])) / HOUR)
        mem["neon_prev"] = {"ts": now, "bytes": egress}
        budget = float(os.environ.get("NEON_EGRESS_BUDGET_BYTES_PER_HOUR") or 200 * 1024**2)
        st = AMBER if rate is not None and rate > budget else GREEN
        add(check("neon.usage", "neon", st,
                  f"egress this period {float(egress or 0) / 1e9:.2f}GB, rate {('%.0fMB/h' % (rate / 1e6)) if rate is not None else '?'}; "
                  f"compute {neon.get('compute_time_seconds')}s", f"egress <= {budget / 1e6:.0f}MB/h",
                  "" if st == GREEN else "polling loop or unbounded query (see #261)"))

    # ---------------- Bitfinex
    live_armed = dig(status, "live_armed", default=dig(health, "live_armed"))
    bfx_enabled = dig(status, "bitfinex_live_enabled", default=dig(health, "bitfinex_live_enabled"))
    force_paper = dig(status, "force_paper_mode", default=dig(health, "force_paper_mode"))
    exch_qty = recon.get("signedExchangePositionQty")
    audit = relay.get("exchangeOrderAudit") or {}
    active_orders = int(audit.get("activeOrderCount") or 0)
    problems = []
    if live_armed is True:
        problems.append("live_armed=true")
    if bfx_enabled is True:
        problems.append("bitfinex_live_enabled=true")
    if force_paper is False:
        problems.append("force_paper_mode=false")
    if exch_qty not in (None, 0, 0.0):
        problems.append(f"exchange position qty={exch_qty}")
    if active_orders:
        problems.append(f"{active_orders} active exchange orders")
    if not reachable and not relay.get("ok"):
        add(check("bitfinex.exposure", "bitfinex", AMBER, "cannot observe (Fly and relay unreadable)",
                  "disarmed, 0 exchange position, 0 exchange orders"))
    else:
        add(check("bitfinex.exposure", "bitfinex", RED if problems else GREEN,
                  "; ".join(problems) or f"disarmed (live_armed={live_armed}, force_paper={force_paper}), exchange qty={exch_qty}, "
                                         f"orders={active_orders}",
                  "disarmed, 0 exchange position, 0 exchange orders",
                  "" if not problems else "UNEXPECTED REAL EXPOSURE/ARMING - never force-close; escalate to Danish"))

    # ---------------- Proof checker
    active = inputs.get("proof_active")
    row = inputs.get("proof_last_row")
    if not isinstance(active, Mapping) and not row:
        add(check("proof.latest", "proof", SKIP, "no proof window active", "latest row PASS"))
    else:
        row_at = parse_ts(dig(row, "at"))
        row_age = (now - row_at) if row_at else None
        result = dig(active, "status", "result")
        row_status = dig(row, "status")
        st = GREEN
        if row_status == "FAIL" or (row_age is not None and row_age > t["proof_row_amber_sec"] and result != "PASSED"):
            st = AMBER
        add(check("proof.latest", "proof", st,
                  f"window {result}; latest row {row_status} {fmt_age(row_age)} ago failed={dig(row, 'failed_checks', default=[])}",
                  f"latest row PASS within {fmt_age(t['proof_row_amber_sec'])}",
                  "" if st == GREEN else "see the failing proof checks; the matching health checks carry the cause"))

    # ---------------- Disk
    disk = inputs.get("laptop_disk") or {}
    vol = dig(health, "volume", default={}) or {}
    parts, st = [], GREEN
    if disk:
        free = disk["free"]
        parts.append(f"laptop free {free / 1024**3:.1f}GB")
        st = RED if free < t["laptop_free_red_bytes"] else AMBER if free < t["laptop_free_amber_bytes"] else st
    if vol.get("free_bytes") is not None:
        ffree = float(vol["free_bytes"])
        parts.append(f"Fly volume free {ffree / 1024**3:.1f}GB ({vol.get('hours_to_full')}h to full)")
        fst = RED if ffree < t["fly_free_red_bytes"] else AMBER if ffree < t["fly_free_amber_bytes"] else GREEN
        st = max(st, fst, key=RANK.get)
    if transfer and transfer.get("max_store_bytes"):
        pct = 100.0 * float(transfer.get("store_bytes") or 0) / float(transfer["max_store_bytes"])
        parts.append(f"segment store {pct:.1f}% of cap")
        sst = RED if pct >= t["store_cap_red_pct"] else AMBER if pct >= t["store_cap_amber_pct"] else GREEN
        st = max(st, sst, key=RANK.get)
    add(check("disk.space", "disk", st if parts else SKIP, "; ".join(parts) or "unknown",
              f"laptop >= {t['laptop_free_amber_bytes'] / 1024**3:.0f}GB, Fly >= {t['fly_free_amber_bytes'] / 1024**3:.0f}GB, "
              f"store < {t['store_cap_amber_pct']:.0f}% cap",
              "" if st == GREEN else "data growth; DO NOT prune - raise volume or ask Danish"))
    return checks


def summarize(checks: list[dict[str, Any]], state: dict[str, Any], now: float) -> dict[str, Any]:
    last_good = state.setdefault("last_good", {})
    for c in checks:
        if c["status"] == GREEN:
            last_good[c["id"]] = now
        c["last_good_at"] = iso(last_good.get(c["id"]))
    rated = [c for c in checks if c["status"] != SKIP]
    verdict = max((c["status"] for c in rated), key=RANK.get, default=AMBER)
    failing = [c for c in checks if c["status"] in (RED, AMBER)]
    failing.sort(key=lambda c: -RANK[c["status"]])
    subsystems: dict[str, str] = {}
    for c in checks:
        prev = subsystems.get(c["subsystem"], SKIP)
        subsystems[c["subsystem"]] = max(prev, c["status"], key=RANK.get)
    return {
        "schema": SCHEMA,
        "generated_at": iso(now),
        "generated_ts": now,
        "host": socket.gethostname(),
        "verdict": verdict,
        "counts": {s: sum(1 for c in checks if c["status"] == s) for s in (RED, AMBER, GREEN, SKIP)},
        "subsystems": subsystems,
        "failing": [{k: c[k] for k in ("id", "status", "observed", "threshold", "hint", "runbook", "last_good_at")}
                    for c in failing],
        "checks": checks,
        "runbook": RUNBOOK,
    }


# ------------------------------------------------------------------- alarms

def alarm_transitions(report: dict[str, Any], state: dict[str, Any], now: float,
                      thresholds: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """Edge-triggered RED alarms with sustain, dedupe, re-notify and recovery.

    AMBER transitions are logged (``AMBER``/``AMBER_CLEAR``) but not pushed.
    """
    t = {**THRESHOLDS, **(thresholds or {})}
    alarms = state.setdefault("alarms", {})
    events: list[dict[str, Any]] = []
    present = set()
    for c in report["checks"]:
        cid, st = c["id"], c["status"]
        present.add(cid)
        a = alarms.setdefault(cid, {"open": False, "streak": 0, "amber": False})
        if st == RED:
            a["streak"] = int(a.get("streak") or 0) + 1
            if not a["open"] and a["streak"] >= SUSTAIN.get(cid, 1):
                a.update(open=True, opened_at=now, notified_at=now)
                events.append(_event("OPEN", c, now))
            elif a["open"] and now - float(a.get("notified_at") or now) >= t["renotify_sec"]:
                a["notified_at"] = now
                events.append(_event("STILL_RED", c, now, opened_at=a.get("opened_at")))
        else:
            a["streak"] = 0
            if a["open"] and st != SKIP:
                events.append(_event("RECOVERED", c, now, opened_at=a.get("opened_at")))
                a.update(open=False, opened_at=None, notified_at=None)
        if st == AMBER and not a.get("amber"):
            a["amber"] = True
            events.append(_event("AMBER", c, now))
        elif st in (GREEN,) and a.get("amber"):
            a["amber"] = False
            events.append(_event("AMBER_CLEAR", c, now))
    report["open_alarms"] = sorted(cid for cid, a in alarms.items() if a.get("open") and cid in present)
    return events


def _event(kind: str, c: Mapping[str, Any], now: float, opened_at: float | None = None) -> dict[str, Any]:
    return {"schema": ALARM_SCHEMA, "at": iso(now), "event": kind, "check": c["id"], "status": c["status"],
            "observed": c["observed"], "threshold": c["threshold"], "hint": c["hint"], "runbook": c["runbook"],
            **({"opened_at": iso(opened_at), "open_for": fmt_age(now - opened_at)} if opened_at else {})}


def alarm_text(events: list[dict[str, Any]]) -> str:
    parts = []
    for e in events:
        tag = {"OPEN": "RED", "STILL_RED": "STILL RED", "RECOVERED": "RECOVERED"}.get(e["event"], e["event"])
        parts.append(f"[{tag}] {e['check']}: {e['observed']}" + (f" -> {e['hint']}" if e["event"] != "RECOVERED" and e["hint"] else ""))
    return "\n".join(parts)


def load_channels(state_dir: Path) -> dict[str, Any]:
    """Webhook config: env DOXXED_ALERT_WEBHOOK_URL or <state>/health/alarm-channels.json."""
    cfg = read_json(state_dir / "health" / "alarm-channels.json") or {}
    if os.environ.get("DOXXED_ALERT_WEBHOOK_URL") and not cfg.get("webhook_url"):
        cfg["webhook_url"] = os.environ["DOXXED_ALERT_WEBHOOK_URL"]
    for key, env in (("telegram_bot_token", "DOXXED_ALERT_TELEGRAM_BOT_TOKEN"),
                     ("telegram_chat_id", "DOXXED_ALERT_TELEGRAM_CHAT_ID")):
        if os.environ.get(env) and not cfg.get(key):
            cfg[key] = os.environ[env]
    return cfg


def notify(events: list[dict[str, Any]], state_dir: Path, *, toast: Callable[[str, str], bool] | None = None,
           post: Callable[..., Any] = http_json) -> dict[str, Any]:
    """Push RED OPEN / STILL_RED / RECOVERED events. Returns per-channel results."""
    pushed = [e for e in events if e["event"] in ("OPEN", "STILL_RED", "RECOVERED")]
    result: dict[str, Any] = {"pushed": len(pushed)}
    if not pushed:
        return result
    text = alarm_text(pushed)
    red = any(e["event"] != "RECOVERED" for e in pushed)
    title = "Doxxed RED alarm" if red else "Doxxed recovered"
    result["toast"] = (toast or windows_toast)(title, text)
    cfg = load_channels(state_dir)
    if cfg.get("webhook_url"):
        _, err = post(cfg["webhook_url"], method="POST", timeout=15,
                      body={"content": f"**{title}**\n{text}"[:1900], "text": f"{title}\n{text}"[:3500]})
        result["webhook"] = err or "ok"
    else:
        result["webhook"] = "not_configured"
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        _, err = post(f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage", method="POST", timeout=15,
                      body={"chat_id": cfg["telegram_chat_id"], "text": f"{title}\n{text}"[:3900]})
        result["telegram"] = err or "ok"
    return result


def windows_toast(title: str, text: str) -> bool:
    if os.name != "nt":
        return False
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")
    body = esc(text[:600]).replace("\n", "&#10;")
    script = (
        "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime];"
        "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime];"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast scenario=\"reminder\"><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text>"
        f"<text>{body}</text></binding></visual></toast>');"
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, timeout=30, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def banner_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compact summary for the dashboards' banner (no secrets, bounded size)."""
    return {
        "schema": SCHEMA,
        "verdict": report["verdict"],
        "generated_at": report["generated_at"],
        "open_alarms": report.get("open_alarms", []),
        "failing": [{k: f.get(k) for k in ("id", "status", "observed", "threshold", "hint", "runbook", "last_good_at")}
                    for f in report["failing"][:12]],
        "counts": report["counts"],
        "source": f"laptop {report.get('host')}",
    }


def push_fly_banner(report: Mapping[str, Any], opts: argparse.Namespace) -> str:
    admin = os.environ.get("BOT_ADMIN_TOKEN") or load_vault(opts.vault).get("BOT_ADMIN_TOKEN")
    if not admin:
        return "no_admin_token"
    _, err = http_json(f"{opts.fly_url}/api/system-health/report", method="POST", timeout=15,
                       headers={"X-Bot-Admin-Token": admin}, body=banner_payload(report))
    return err or "ok"


# --------------------------------------------------------------------- tick

class TickLock:
    """Non-blocking exclusive lock file: exactly one watcher evaluates at a time."""

    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def __exit__(self, *exc: Any) -> None:
        if self.handle:
            try:
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.handle.close()


def health_dir(opts: argparse.Namespace) -> Path:
    return Path(opts.state_dir) / "health"


def run_once(opts: argparse.Namespace, *, alarms: bool) -> dict[str, Any]:
    hdir = health_dir(opts)
    state_path = hdir / "health-state.json"
    state = read_json(state_path) or {}
    now = utcnow()
    inputs = collect(opts, state, now)
    checks = evaluate(inputs, state)
    report = summarize(checks, state, now)
    report["source_errors"] = inputs.get("errors")
    if alarms:
        events = alarm_transitions(report, state, now)
        for e in events:
            append_jsonl(hdir / "alarms.jsonl", e)
        delivery = notify(events, Path(opts.state_dir)) if not opts.no_notify else {"pushed": 0, "muted": True}
        report["last_delivery"] = delivery
        report["fly_banner"] = push_fly_banner(report, opts) if not opts.no_fly_banner else "disabled"
        write_json_atomic(hdir / "system-health-latest.json", report)
        append_jsonl(hdir / f"verdicts-{datetime.now(timezone.utc):%Y%m}.jsonl",
                     {"at": report["generated_at"], "verdict": report["verdict"], "open_alarms": report["open_alarms"],
                      "failing": [f["id"] for f in report["failing"]]})
        write_json_atomic(state_path, state)
    return report


def tick(opts: argparse.Namespace) -> tuple[str, int]:
    hdir = health_dir(opts)
    latest = read_json(hdir / "system-health-latest.json") or {}
    age = utcnow() - float(latest.get("generated_ts") or 0)
    if age < THRESHOLDS["tick_min_interval_sec"] and not opts.force:
        ensure_server(opts)
        return f"SYSTEM_HEALTH skipped (last tick {age:.0f}s ago) verdict={latest.get('verdict')}", 0
    with TickLock(hdir / "tick.lock") as owned:
        if not owned:
            return "SYSTEM_HEALTH skipped (another watcher holds the lock)", 0
        report = run_once(opts, alarms=True)
    ensure_server(opts)
    line = (f"SYSTEM_HEALTH verdict={report['verdict']} red={report['counts'][RED]} amber={report['counts'][AMBER]} "
            f"open={','.join(report['open_alarms']) or 'none'} pushed={report['last_delivery'].get('pushed')} "
            f"fly_banner={report['fly_banner']}")
    return line, 0


def ensure_server(opts: argparse.Namespace) -> None:
    """Start the read-only local endpoint if nothing answers on its port."""
    try:
        with socket.create_connection(("127.0.0.1", opts.port), timeout=2):
            return
    except OSError:
        pass
    server = Path(__file__).resolve().parent / "system_health_server.py"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    cmd = [str(pythonw if pythonw.exists() else exe), str(server), "--state-dir", opts.state_dir, "--port", str(opts.port)]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(cmd, cwd=str(server.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        pass


def render(report: Mapping[str, Any]) -> str:
    icon = {RED: "RED  ", AMBER: "AMBER", GREEN: "ok   ", SKIP: "skip "}
    lines = [f"SYSTEM HEALTH: {report['verdict']}  ({report['generated_at']})  "
             f"red={report['counts'][RED]} amber={report['counts'][AMBER]} green={report['counts'][GREEN]}"]
    for c in sorted(report["checks"], key=lambda c: (-RANK[c["status"]], c["id"])):
        lines.append(f"  {icon[c['status']]} {c['id']:<22} {c['observed']}")
        if c["status"] in (RED, AMBER):
            lines.append(f"        threshold: {c['threshold']}")
            if c["hint"]:
                lines.append(f"        likely:    {c['hint']}")
            lines.append(f"        last good: {c.get('last_good_at') or 'never seen'}   fix: {c['runbook']}")
    if report.get("open_alarms") is not None:
        lines.append(f"open alarms: {', '.join(report['open_alarms']) or 'none'}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state-dir", default=os.environ.get("DOXXED_LAPTOP_CHAIN_STATE") or DEFAULT_STATE_DIR)
    p.add_argument("--analyzer-repo", default=DEFAULT_ANALYZER_REPO)
    p.add_argument("--mirror-tree", default=DEFAULT_MIRROR_TREE)
    p.add_argument("--shadow-root", default=DEFAULT_SHADOW_ROOT)
    p.add_argument("--exports", default=DEFAULT_EXPORTS)
    p.add_argument("--proof-dir", default=DEFAULT_PROOF_DIR)
    p.add_argument("--vault", default=DEFAULT_VAULT)
    p.add_argument("--fly-url", default=FLY_URL)
    p.add_argument("--analyzer-url", default=ANALYZER_URL)
    p.add_argument("--port", type=int, default=SERVER_PORT)
    p.add_argument("--json", action="store_true")
    p.add_argument("--tick", action="store_true", help="watcher tick: evaluate, alarm, publish")
    p.add_argument("--latest", action="store_true", help="print the last published verdict")
    p.add_argument("--force", action="store_true", help="tick even inside the minimum interval")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--no-fly-banner", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    if "onedrive" in str(Path(opts.state_dir).resolve()).lower():
        print("refusing a OneDrive state directory", file=sys.stderr)
        return 2
    if opts.tick:
        line, code = tick(opts)
        print(line)
        return code
    if opts.latest:
        report = read_json(health_dir(opts) / "system-health-latest.json")
        if not report:
            print("no published verdict yet", file=sys.stderr)
            return 2
    else:
        report = run_once(opts, alarms=False)
    print(json.dumps(report, indent=2, default=str) if opts.json else render(report))
    return {GREEN: 0, AMBER: 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
