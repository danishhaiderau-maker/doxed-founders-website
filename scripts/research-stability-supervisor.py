#!/usr/bin/env python3
"""Fail-closed supervisor for the desktop BTC research pipeline.

This process has deliberately narrow authority.  It may start a missing Fly
mirror loop or a missing desktop analyzer through their existing launchers.  It
cannot stop/restart trading, call Fly mutation endpoints, wipe data, or change
policy.  Every observation is written atomically for the dashboard/operator.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPORT_MAX_AGE_SECONDS = 45 * 60
SYNC_MAX_AGE_SECONDS = 10 * 60
MAX_PENDING_EVENT_DELTA = 100
READINESS_STARVATION_THRESHOLD_SECONDS = 15 * 60
REQUIRED_SCHEMA = "research_event_v2.2"
REQUIRED_COLLECTOR = "collector_v2.2"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def fetch_json(url: str, token: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"X-Bot-Admin-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("remote response is not a JSON object")
    return value


def process_inventory() -> list[dict[str, Any]]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    raw = json.loads(result.stdout or "[]")
    return raw if isinstance(raw, list) else [raw]


def classify_processes(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    groups = {"sync": [], "analyzer": [], "dashboard": [], "supervisor": []}
    for row in rows:
        cmd = str(row.get("CommandLine") or "").lower()
        pid = int(row.get("ProcessId") or 0)
        if pid <= 0:
            continue
        if "sync-fly-bot-data-loop.ps1" in cmd:
            groups["sync"].append(pid)
        if "analyzer_research_engine_v62.py" in cmd:
            groups["analyzer"].append(pid)
        if "research_dashboard.py" in cmd and "--standalone" in cmd:
            groups["dashboard"].append(pid)
        if "research-stability-supervisor.py" in cmd:
            groups["supervisor"].append(pid)
    return {key: sorted(set(value)) for key, value in groups.items()}


def read_current_events(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    # Release the Windows mirror handle before schema validation/counting. A
    # supervisor pass must never block publication of the next generation.
    payload = path.read_bytes()
    for line_number, raw_line in enumerate(payload.splitlines(), 1):
        line = raw_line.decode("utf-8-sig", errors="strict")
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"event line {line_number} is not an object")
        if value.get("schema") != REQUIRED_SCHEMA or value.get("collector_version") != REQUIRED_COLLECTOR:
            raise ValueError(f"event line {line_number} is not qualified v2.2")
        events.append(value)
    if not events:
        raise ValueError("no v2.2 events")
    epoch = str(events[-1].get("epoch_id") or "")
    current = [row for row in events if str(row.get("epoch_id") or "") == epoch]
    completed = [
        row for row in current
        if str(row.get("observation_status") or "").upper() in {"COMPLETE", "FUNNEL_COMPLETE"}
    ]
    all_episode_ids = {
        str(row.get("event_episode_id") or row.get("envelope", {}).get("event_episode_id") or "")
        for row in current
    }
    all_episode_ids.discard("")
    eligible_episode_ids = {
        str(row.get("event_episode_id") or row.get("envelope", {}).get("event_episode_id") or "")
        for row in completed
    }
    eligible_episode_ids.discard("")
    policy_epochs = {str(row.get("policy_epoch_id") or "") for row in current}
    signatures = {str(row.get("policy_signature") or "") for row in current}
    policy_epochs.discard("")
    signatures.discard("")
    return {
        "epoch_id": epoch,
        "current_events": len(current),
        "eligible_events": len(completed),
        "ineligible_events": len(current) - len(completed),
        "all_independent_episodes": len(all_episode_ids),
        "eligible_independent_episodes": len(eligible_episode_ids),
        "policy_epoch_ids": sorted(policy_epochs),
        "policy_signatures": sorted(signatures),
        "_all_event_ids": [str(row.get("event_id") or "") for row in events],
    }


def shared_cycle_snapshot_prefix_ok(
    reports: dict[str, dict[str, Any]], event_summary: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Prove that both reports used one immutable prefix of the live mirror."""
    snapshots = [report.get("cycle_snapshot") or {} for report in reports.values()]
    shared = bool(snapshots) and all(snapshot == snapshots[0] for snapshot in snapshots[1:])
    receipt = snapshots[0] if snapshots else {}
    row_count = int(receipt.get("row_count") or 0)
    event_ids = event_summary.get("_all_event_ids") or []
    prefix_terminal = event_ids[row_count - 1] if 0 < row_count <= len(event_ids) else None
    identity_ok = bool(
        receipt.get("schema") == "policy_cycle_snapshot_v1"
        and receipt.get("snapshot_id")
        and receipt.get("epoch_id") == event_summary.get("epoch_id")
        and receipt.get("policy_epoch_id") in event_summary.get("policy_epoch_ids", [])
        and receipt.get("policy_signature") in event_summary.get("policy_signatures", [])
    )
    ok = bool(
        shared and identity_ok and row_count > 0
        and receipt.get("last_event_id") == prefix_terminal
    )
    return ok, {
        "shared": shared,
        "identity_ok": identity_ok,
        "snapshot_id": receipt.get("snapshot_id"),
        "row_count": row_count,
        "mirror_row_count": len(event_ids),
        "last_event_id": receipt.get("last_event_id"),
        "prefix_terminal_event_id": prefix_terminal,
    }


def report_count_contract(filename: str, report: dict[str, Any]) -> dict[str, int]:
    """Return counts with the semantics declared by each report schema.

    Candidate selection operates only on replay-eligible paths, so its episode
    count is eligible-only.  Best-policy coverage deliberately includes
    negative/ineligible paths, so its episode count covers the whole epoch.
    Silently substituting one meaning for the other would create false health
    or false failure and is therefore rejected.
    """
    evidence = report.get("evidence") or {}
    if filename == "policy_candidate_oos_report.json":
        return {
            "current_events": int(evidence.get("current_events") or 0),
            "eligible_events": int(evidence.get("eligible_events") or 0),
            "eligible_independent_episodes": int(evidence.get("independent_episodes") or 0),
        }
    if filename == "best_policy_research_report.json":
        return {
            "current_events": int(evidence.get("current_epoch_events") or 0),
            "eligible_events": int(evidence.get("replay_eligible_events") or 0),
            "ineligible_events": int(evidence.get("replay_ineligible_events") or 0),
            "all_independent_episodes": int(evidence.get("independent_episode_count") or 0),
        }
    raise ValueError(f"unsupported report count contract: {filename}")


def bounded_pending_parity(
    expected: dict[str, dict[str, int]],
    observed: dict[str, dict[str, int]],
    *,
    reports_fresh: bool,
    mirror_after_reports: bool,
    snapshot_prefix_ok: bool,
    identity_ok: bool,
) -> tuple[bool, dict[str, Any]]:
    """Accept only append-only growth awaiting the next fresh analyzer cycle."""
    candidate_name = "policy_candidate_oos_report.json"
    best_name = "best_policy_research_report.json"
    candidate = observed[candidate_name]
    best = observed[best_name]
    candidate_expected = expected[candidate_name]
    best_expected = expected[best_name]

    baseline_consistent = (
        candidate["current_events"] == best["current_events"]
        and candidate["eligible_events"] == best["eligible_events"]
        and best["current_events"] == best["eligible_events"] + best["ineligible_events"]
        and candidate["eligible_events"] <= candidate["current_events"]
    )
    deltas = {
        "current_events": best_expected["current_events"] - best["current_events"],
        "eligible_events": best_expected["eligible_events"] - best["eligible_events"],
        "ineligible_events": best_expected["ineligible_events"] - best["ineligible_events"],
        "all_independent_episodes": (
            best_expected["all_independent_episodes"] - best["all_independent_episodes"]
        ),
        "eligible_independent_episodes": (
            candidate_expected["eligible_independent_episodes"]
            - candidate["eligible_independent_episodes"]
        ),
    }
    nonnegative = all(value >= 0 for value in deltas.values())
    bounded = (
        deltas["current_events"] <= MAX_PENDING_EVENT_DELTA
        and deltas["eligible_events"] <= deltas["current_events"]
        and deltas["ineligible_events"] <= deltas["current_events"]
        and deltas["eligible_events"] + deltas["ineligible_events"] == deltas["current_events"]
        and deltas["all_independent_episodes"] <= deltas["current_events"]
        and deltas["eligible_independent_episodes"] <= deltas["eligible_events"]
    )
    pending = (
        reports_fresh and (mirror_after_reports or snapshot_prefix_ok) and identity_ok
        and baseline_consistent and nonnegative and bounded
        and deltas["current_events"] > 0
    )
    return pending, {
        "status": "PENDING_NEXT_ANALYZER_CYCLE" if pending else "MISMATCH",
        "deltas": deltas,
        "reports_fresh": reports_fresh,
        "mirror_after_reports": mirror_after_reports,
        "snapshot_prefix_ok": snapshot_prefix_ok,
        "identity_ok": identity_ok,
        "baseline_consistent": baseline_consistent,
        "nonnegative": nonnegative,
        "bounded": bounded,
        "max_pending_event_delta": MAX_PENDING_EVENT_DELTA,
    }


def runtime_counts(payload: dict[str, Any]) -> dict[str, int | None]:
    def count(explicit: tuple[str, ...], collections: tuple[str, ...]) -> int | None:
        for key in explicit:
            if payload.get(key) is not None:
                try:
                    return int(payload[key])
                except (TypeError, ValueError):
                    return None
        for key in collections:
            if isinstance(payload.get(key), list):
                return len(payload[key])
        return None

    return {
        "virtual_count": count(
            ("virtual_count", "virtual_candidate_count", "active_signal_count"),
            ("virtual_chase_candidates", "active_signals"),
        ),
        "pending_count": count(
            ("pending_count", "pending_order_count"), ("pending_orders",),
        ),
        "position_count": count(
            ("position_count", "open_position_count"), ("positions",),
        ),
    }


def evaluate_runtime_readiness(
    status: dict[str, Any],
    prior: dict[str, Any],
    *,
    now: datetime,
    counts: dict[str, int | None],
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    runtime = status.get("runtime_readiness") or {}
    reasons = [str(value) for value in (runtime.get("readiness_reasons") or [])]
    signal_ready = bool(status.get("signal_generation_ready", runtime.get("signal_generation_ready")))
    process_alive = bool(status.get("process_alive"))
    data_reasons = {
        "NO_PRICE", "WS_NOT_READY", "REST_ENTRY_QUOTE_NOT_READY", "OHLCV_NOT_READY",
        "EMA_NOT_READY", "CANDLE_STALE", "BUFFERS_NOT_READY",
    }
    starvation_reasons = sorted(set(reasons) & data_reasons)
    paused_only = bool(set(reasons) & {"ADMIN_MANUAL_PAUSE", "EXECUTION_PAUSED"}) and not starvation_reasons
    stabilizing = (
        not signal_ready
        and bool(runtime.get("prerequisites_ready"))
        and "READINESS_STABILIZING" in reasons
        and not starvation_reasons
    )
    identity = str(status.get("git_rev") or status.get("bot_version") or "UNKNOWN")
    prior_identity = str(prior.get("runtime_identity") or "")
    prior_first = parse_time(prior.get("first_starved_at")) if prior_identity == identity else None

    if signal_ready or paused_only or stabilizing or not process_alive or not starvation_reasons:
        first = None
        duration = 0.0
    else:
        first = prior_first or now
        duration = max(0.0, (now - first).total_seconds())

    persistent = bool(first and duration >= READINESS_STARVATION_THRESHOLD_SECONDS)
    if not process_alive:
        state = "PROCESS_NOT_ALIVE"
    elif signal_ready:
        state = "READY"
    elif stabilizing:
        state = "STABILIZING"
    elif paused_only:
        state = "PAUSED_NOT_STARVATION"
    elif persistent:
        state = "PERSISTENT_COLLECTION_STARVATION"
    elif starvation_reasons:
        state = "TRANSIENT_NOT_READY"
    else:
        state = "NOT_READY_NON_DATA_REASON"

    next_state = {
        "schema": "research_runtime_readiness_state_v1",
        "runtime_identity": identity,
        "observed_at": now.isoformat(),
        "first_starved_at": first.isoformat() if first else None,
        "starved_duration_seconds": round(duration, 1),
        "state": state,
        "reasons": reasons,
    }
    detail = {
        **next_state,
        "threshold_seconds": READINESS_STARVATION_THRESHOLD_SECONDS,
        "process_alive": process_alive,
        "signal_generation_ready": signal_ready,
        "system_ready": bool(status.get("system_ready", runtime.get("system_ready"))),
        "ws_ready": bool(status.get("ws_ready", runtime.get("ws_transport_ready"))),
        "rest_entry_quote_ready": runtime.get("rest_entry_quote_ready"),
        "ohlcv_ready": status.get("ohlcv_ready", runtime.get("ohlcv_ready")),
        "ohlcv_age_sec": runtime.get("ohlcv_age_sec"),
        "readiness_reasons": reasons,
        **counts,
    }
    return process_alive and not persistent, detail, next_state


@dataclass
class Supervisor:
    repo: Path
    mirror: Path
    report_dir: Path
    fly_url: str
    token: str
    repair: bool = False
    now: Callable[[], datetime] = utc_now
    fetcher: Callable[[str, str, int], dict[str, Any]] = fetch_json
    process_reader: Callable[[], list[dict[str, Any]]] = process_inventory
    launcher: Callable[..., subprocess.Popen[Any]] = subprocess.Popen
    runtime_repo: Path | None = None
    readiness_state_file: Path | None = None

    def launch_missing(self, kind: str) -> bool:
        if not self.repair:
            return False
        if kind == "sync":
            owner_repo = self.runtime_repo or self.repo
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(owner_repo / "scripts" / "sync-fly-bot-data-loop.ps1")]
        elif kind == "analyzer":
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.repo / "scripts" / "start-home-analyzer.ps1"), "-NoWait", "-Port", "9001"]
        else:
            return False
        self.launcher(args, cwd=self.repo, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True

    def check(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        repairs: list[str] = []

        def add(name: str, ok: bool, detail: Any) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        source_revision = None
        try:
            manifest = self.fetcher(self.fly_url.rstrip("/") + "/api/data-sync/manifest", self.token, 20)
            source_revision = manifest.get("source_git_rev") or manifest.get("source_revision")
            add("fly_collector_manifest", bool(manifest.get("files")) and int(manifest.get("total_bytes") or 0) > 0, {
                "total_bytes": manifest.get("total_bytes"), "source_revision": source_revision,
                "fresh_collection_signal_ts": manifest.get("fresh_collection_signal_ts")})
        except Exception as exc:
            add("fly_collector_manifest", False, type(exc).__name__)
        try:
            size = self.fetcher(self.fly_url.rstrip("/") + "/api/data_size", self.token, 20)
            pct = float(size.get("volume_pct") or 0)
            add("fly_storage", pct < 85.0, {"volume_pct": pct, "cleanup_status": size.get("cleanup_status")})
        except Exception as exc:
            add("fly_storage", False, type(exc).__name__)

        readiness_path = self.readiness_state_file or self.repo / ".research-runtime-readiness-state.json"
        try:
            status = self.fetcher(self.fly_url.rstrip("/") + "/api/status", self.token, 20)
            count_payload = status
            counts = runtime_counts(count_payload)
            if any(value is None for value in counts.values()):
                try:
                    state_payload = self.fetcher(self.fly_url.rstrip("/") + "/api/state", self.token, 20)
                    state_counts = runtime_counts(state_payload)
                    counts = {key: counts[key] if counts[key] is not None else state_counts[key] for key in counts}
                except Exception:
                    pass
            try:
                prior_readiness = read_json(readiness_path)
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                prior_readiness = {}
            readiness_ok, readiness_detail, next_readiness = evaluate_runtime_readiness(
                status, prior_readiness, now=self.now(), counts=counts,
            )
            atomic_json(readiness_path, next_readiness)
            add("persistent_runtime_readiness", readiness_ok, readiness_detail)
        except Exception as exc:
            add("persistent_runtime_readiness", False, type(exc).__name__)

        heartbeat_path = (self.runtime_repo or self.repo) / ".fly-data-sync-loop.heartbeat.json"
        sync_revision = None
        try:
            heartbeat = read_json(heartbeat_path)
            stamp = parse_time(heartbeat.get("syncedAt"))
            age = (self.now() - stamp).total_seconds() if stamp else float("inf")
            sync_revision = heartbeat.get("sourceRevision") or heartbeat.get("source_revision")
            add("atomic_sync_heartbeat", heartbeat.get("ok") is True and age <= SYNC_MAX_AGE_SECONDS,
                {"age_seconds": round(age, 1), "ok": heartbeat.get("ok"), "sourceRevision": sync_revision})
        except Exception as exc:
            add("atomic_sync_heartbeat", False, type(exc).__name__)
        revision_match = bool(source_revision and sync_revision and (
            source_revision == sync_revision
            or source_revision.startswith(sync_revision)
            or sync_revision.startswith(source_revision)
        ))
        add("fly_sync_revision_parity", revision_match, {
            "fly_source_revision": source_revision,
            "sync_source_revision": sync_revision,
        })

        try:
            inventory = classify_processes(self.process_reader())
        except Exception as exc:
            inventory = {"sync": [], "analyzer": [], "dashboard": [], "supervisor": []}
            add("process_inventory", False, type(exc).__name__)
        else:
            add("process_inventory", True, inventory)
        for kind in ("sync", "analyzer", "dashboard"):
            count = len(inventory[kind])
            add(f"unique_{kind}_process", count == 1, {"count": count, "pids": inventory[kind]})
            if count == 0 and kind in {"sync", "analyzer"} and self.launch_missing(kind):
                repairs.append(f"started_missing_{kind}_through_safe_launcher")

        events_path = self.mirror / "research_events_v22.jsonl"
        event_summary: dict[str, Any] | None = None
        try:
            event_summary = read_current_events(events_path)
            mirror_age = (self.now().timestamp() - events_path.stat().st_mtime)
            # A finalized append-only event file legitimately remains unchanged
            # when no lifecycle matures. Transport freshness is independently
            # enforced by the atomic sync heartbeat above.
            public_summary = {
                key: value for key, value in event_summary.items()
                if not key.startswith("_")
            }
            add("mirror_schema_and_freshness", True,
                {**public_summary, "age_seconds": round(mirror_age, 1)})
        except Exception as exc:
            add("mirror_schema_and_freshness", False, f"{type(exc).__name__}: {exc}")

        reports: dict[str, dict[str, Any]] = {}
        report_times: dict[str, datetime] = {}
        report_freshness: dict[str, bool] = {}
        for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
            path = self.report_dir / filename
            try:
                report = read_json(path)
                generated = parse_time(report.get("generated_at"))
                age = (self.now() - generated).total_seconds() if generated else float("inf")
                fresh = age <= REPORT_MAX_AGE_SECONDS
                add(f"report_fresh:{filename}", fresh, {"age_seconds": round(age, 1)})
                reports[filename] = report
                if generated:
                    report_times[filename] = generated
                report_freshness[filename] = fresh
            except Exception as exc:
                add(f"report_fresh:{filename}", False, type(exc).__name__)

        if event_summary and len(reports) == 2:
            expected = {
                "policy_candidate_oos_report.json": {
                    "current_events": event_summary["current_events"],
                    "eligible_events": event_summary["eligible_events"],
                    "eligible_independent_episodes": event_summary["eligible_independent_episodes"],
                },
                "best_policy_research_report.json": {
                    "current_events": event_summary["current_events"],
                    "eligible_events": event_summary["eligible_events"],
                    "ineligible_events": event_summary["ineligible_events"],
                    "all_independent_episodes": event_summary["all_independent_episodes"],
                },
            }
            expected_epoch = event_summary["epoch_id"]
            expected_policy_epochs = event_summary["policy_epoch_ids"]
            expected_signatures = event_summary["policy_signatures"]
            identity_ok = all(
                report.get("epoch_id") == expected_epoch
                and report.get("policy_epoch_id") in expected_policy_epochs
                and report.get("evidence_policy_signature") in expected_signatures
                for report in reports.values()
            )
            observed = {name: report_count_contract(name, report) for name, report in reports.items()}
            exact = all(observed[name] == expected[name] for name in expected)
            mirror_time = datetime.fromtimestamp(events_path.stat().st_mtime, tz=timezone.utc)
            mirror_after_reports = (
                len(report_times) == 2
                and all(mirror_time > generated for generated in report_times.values())
            )
            snapshot_prefix_ok, snapshot_prefix_detail = shared_cycle_snapshot_prefix_ok(
                reports, event_summary,
            )
            pending, pending_detail = bounded_pending_parity(
                expected, observed,
                reports_fresh=len(report_freshness) == 2 and all(report_freshness.values()),
                mirror_after_reports=mirror_after_reports,
                snapshot_prefix_ok=snapshot_prefix_ok,
                identity_ok=identity_ok,
            )
            add("report_count_parity", exact or pending, {
                "status": "EXACT" if exact else pending_detail["status"],
                "expected": expected,
                "reports": observed,
                "pending": pending_detail,
                "cycle_snapshot_prefix": snapshot_prefix_detail,
            })
            add("report_epoch_policy_signature_parity", identity_ok, {
                "expected_epoch": expected_epoch, "expected_policy_epochs": expected_policy_epochs,
                "expected_policy_signatures": expected_signatures,
                "reports": {name: {key: report.get(key) for key in ("epoch_id", "policy_epoch_id", "evidence_policy_signature")}
                            for name, report in reports.items()}})

        return {
            "schema": "research_stability_supervisor_v1",
            "generated_at": self.now().isoformat(),
            "healthy": all(row["ok"] for row in checks),
            "repair_authority": "MISSING_LOCAL_SYNC_OR_ANALYZER_ONLY",
            "forbidden_actions": ["TRADING_RESTART", "FLY_RESTART", "DATA_WIPE", "POLICY_CHANGE", "LIVE_TRADE_ARM"],
            "repairs": repairs,
            "checks": checks,
        }


def default_paths(repo: Path) -> tuple[Path, Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return local / "DoxxedCrypto" / "fly-data-mirror", repo / "services" / "btc-conservative-agent"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-repo", type=Path)
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--fly-url", default="https://doxed-btc-bot.fly.dev")
    parser.add_argument("--repair-missing-local", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    mirror_default, report_default = default_paths(repo)
    status_file = args.status_file or repo / ".research-stability-supervisor.json"
    token = os.environ.get("BOT_ADMIN_TOKEN", "")
    if not token:
        raise SystemExit("BOT_ADMIN_TOKEN is required; load it through the existing vault launcher")
    supervisor = Supervisor(
        repo, args.mirror or mirror_default, args.report_dir or report_default,
        args.fly_url, token, repair=args.repair_missing_local,
        runtime_repo=args.runtime_repo.resolve() if args.runtime_repo else None,
    )
    while True:
        payload = supervisor.check()
        atomic_json(status_file, payload)
        if not args.loop:
            return 0 if payload["healthy"] else 2
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    sys.exit(main())
