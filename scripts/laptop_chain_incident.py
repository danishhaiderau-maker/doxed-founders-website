"""Escalate laptop research-chain incidents to one GitHub issue per incident.

Run once per ``DoxxedLaptopChainSupervisor`` tick, after
``laptop-chain-monitor.ps1`` has refreshed ``alerts\\active-alerts.json``.
It reuses the Fly monitor's dedup policy and issue lifecycle with the
``laptop-chain-incident`` label: the issue opens on the first alert, gets a
comment only on new alerts or re-alerts, and closes after recovery.

It also refreshes the ``LAPTOP_CHAIN_HEARTBEAT`` repository variable so the
scheduled Fly monitor can alert when the supervisor itself stops ticking
(a dead supervisor cannot report its own death). This script never fails the
supervisor tick: GitHub errors are logged and retried next tick.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_monitor_alerts as alerts  # noqa: E402
from fly_monitor_alerts import HOUR, Policy  # noqa: E402

LABEL = "laptop-chain-incident"
TITLE = "Laptop research chain incident"
DEFAULT_REPO = "danishhaiderau-maker/doxed-founders-website"
HEARTBEAT_VARIABLE = "LAPTOP_CHAIN_HEARTBEAT"
HEARTBEAT_EVERY_SEC = 15 * 60.0
ANALYZER_MAX_AGE_SEC = 2 * HOUR
MONITOR_STALE_SEC = 30 * 60.0
POLICIES: Mapping[str, Policy] = {
    # Emitted only after the analyzer has not completed for ANALYZER_MAX_AGE_SEC.
    "analyzer_stale": Policy(1, 0.0, 12 * HOUR, False),
    # The supervisor restarts the segment pull loop every tick; persisting
    # across two ticks and ten minutes means it cannot stay up.
    "segment_pull_dead": Policy(2, 10 * 60.0, 12 * HOUR, False),
    # The supervisor ticks but the local monitor is not refreshing alerts.
    "monitor_stale": Policy(2, 20 * 60.0, 12 * HOUR, False),
    "test_alert": Policy(1, 0.0, 0.0, False),
}


def parse_utc(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def findings(
    analyzer_status: Any,
    active_alerts: Any,
    now: float,
    *,
    analyzer_max_age_sec: float = ANALYZER_MAX_AGE_SEC,
) -> dict[str, str]:
    found: dict[str, str] = {}
    analyzer = analyzer_status if isinstance(analyzer_status, dict) else {}
    last_success = parse_utc(analyzer.get("lastSuccessAt"))
    if last_success is None or now - last_success > analyzer_max_age_sec:
        age = f"{(now - last_success) / 3600:.1f}h" if last_success is not None else "ever"
        found["analyzer_stale"] = (
            f"analyzer has not COMPLETED for {age} (limit {analyzer_max_age_sec / 3600:.0f}h); "
            f"last run state={analyzer.get('state')!r} exit={analyzer.get('exitCode')!r} "
            f"detail={str(analyzer.get('detail') or '')[:200]!r}"
        )

    checked = parse_utc(active_alerts.get("checkedAt")) if isinstance(active_alerts, dict) else None
    if checked is None or now - checked > MONITOR_STALE_SEC:
        age = f"{(now - checked) / 60:.0f} min old" if checked is not None else "missing"
        found["monitor_stale"] = f"laptop-chain-monitor active-alerts.json is {age}"
        return found
    for alert in active_alerts.get("alerts") or []:
        if isinstance(alert, dict) and alert.get("code") == "SEGMENT_PULL_DEAD":
            found["segment_pull_dead"] = f"segment pull loop is not running: {str(alert.get('detail') or '')[:200]}"
    return found


RELAY_WORKFLOW = "laptop-incident-relay.yml"
_NOTIFYING_POST = re.compile(r"^/issues(/\d+/comments)?$")


class GhCli:
    """Minimal GitHub REST client over the authenticated ``gh`` CLI.

    Issue creation and comments go through the relay workflow so they are
    authored by github-actions and notify the owner; GitHub suppresses
    notifications for the owner's own writes.
    """

    def __init__(self, repo: str, runner=subprocess.run) -> None:
        self.repo = repo
        self.runner = runner

    def call(self, path: str, method: str = "GET", body: Any = None) -> Any:
        if method == "POST" and body is not None and _NOTIFYING_POST.match(path):
            return self._relay(path, body)
        cmd = ["gh", "api", "-X", method, f"repos/{self.repo}{path}"]
        if body is not None:
            cmd += ["--input", "-"]
        result = self.runner(
            cmd,
            input=None if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            match = re.search(r"HTTP (\d{3})", result.stderr or "")
            code = int(match.group(1)) if match else 599
            raise urllib.error.HTTPError(path, code, f"gh api {method} failed", None, None)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _relay(self, path: str, body: Any) -> dict[str, Any]:
        result = self.runner(
            ["gh", "workflow", "run", RELAY_WORKFLOW, "--repo", self.repo,
             "-f", f"path={path}", "-f", f"body={json.dumps(body)}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise urllib.error.HTTPError(path, 599, "gh workflow run relay failed", None, None)
        return {"relayed": True}

    def set_heartbeat(self, now: float) -> None:
        result = self.runner(
            ["gh", "variable", "set", HEARTBEAT_VARIABLE, "--body", str(int(now)), "--repo", self.repo],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh variable set failed (exit {result.returncode})")


def run(args: argparse.Namespace, *, client: Any = None, now: float | None = None) -> dict[str, Any]:
    import fly_monitor_run as runner

    now = time.time() if now is None else now
    state_dir = Path(args.state_dir)
    state_path = state_dir / "laptop-chain-incident.state.json"
    saved = read_json(state_path) or {}
    state = alerts.normalize_state(saved.get("alerts"), POLICIES)
    before = copy.deepcopy(state)

    found = findings(
        read_json(state_dir / "analyzer-run.status.json"),
        read_json(state_dir / "alerts" / "active-alerts.json"),
        now,
        analyzer_max_age_sec=args.analyzer_max_age_min * 60.0,
    )
    if args.test_alert:
        found["test_alert"] = "synthetic laptop test alert (not a real incident)"
    decisions, resolved = alerts.evaluate(state, found, now=now, maintenance=False, policies=POLICIES)

    result: dict[str, Any] = {"decisions": decisions, "resolved": [r["key"] for r in resolved], "synced": False}
    heartbeat_at = saved.get("heartbeat_at") if isinstance(saved.get("heartbeat_at"), (int, float)) else None
    if not args.dry_run:
        client = client or GhCli(args.repo)
        try:
            runner.sync_issue(
                state, decisions, resolved, now,
                client=client, label=LABEL, title=TITLE,
                source="the laptop **DoxxedLaptopChainSupervisor** task (`scripts/laptop_chain_incident.py`)",
                link=f"laptop `{socket.gethostname()}`",
            )
            result["synced"] = True
        except (urllib.error.HTTPError, OSError, ValueError, subprocess.SubprocessError) as exc:
            # Keep the previous dedup state so the alert is retried next tick
            # instead of being recorded as delivered.
            state = before
            result["error"] = f"issue sync failed: {type(exc).__name__}: {exc}"
        if not args.no_heartbeat and (heartbeat_at is None or now - heartbeat_at >= HEARTBEAT_EVERY_SEC):
            try:
                client.set_heartbeat(now)
                heartbeat_at = now
                result["heartbeat"] = True
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                result["heartbeat_error"] = str(exc)
    if not args.dry_run:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"alerts": state, "heartbeat_at": heartbeat_at}, sort_keys=True), encoding="utf-8")
        os.replace(tmp, state_path)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("DOXXED_LAPTOP_CHAIN_STATE") or r"C:\DoxxedCrypto\laptop-chain",
    )
    parser.add_argument("--repo", default=os.environ.get("DOXXED_ALERT_REPO") or DEFAULT_REPO)
    parser.add_argument("--analyzer-max-age-min", type=float, default=ANALYZER_MAX_AGE_SEC / 60)
    parser.add_argument("--test-alert", action="store_true", help="inject one synthetic test_alert finding")
    parser.add_argument("--dry-run", action="store_true", help="evaluate only; no GitHub calls, no state write")
    parser.add_argument("--no-heartbeat", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if "onedrive" in str(Path(args.state_dir).resolve()).lower():
        print("refusing a OneDrive state directory", file=sys.stderr)
        return 0
    result = run(args)
    summary = ",".join(f"{d['key']}={d['action']}" for d in result["decisions"]) or "none"
    print(
        f"LAPTOP_INCIDENT decisions={summary} resolved={','.join(result['resolved']) or 'none'} "
        f"synced={result['synced']} heartbeat={result.get('heartbeat', False)} "
        f"error={result.get('error') or result.get('heartbeat_error') or ''}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
