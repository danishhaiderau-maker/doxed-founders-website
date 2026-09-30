"""Scheduled Fly BTC bot monitor with bounded retries and deduplicated alerts.

Run from the repository root by .github/workflows/fly-bot-monitor.yml. The job
fails only when an incident alert fires (first time, or a re-alert after the
policy interval); known or transitional conditions finish green with a
warning annotation. One GitHub issue labelled ``fly-monitor-incident`` tracks
the open incident and is closed on recovery.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "services" / "btc-conservative-agent"))

import fly_monitor_alerts as alerts  # noqa: E402
import fly_monitor_rules as rules  # noqa: E402
from fly_monitor_contract import (  # noqa: E402
    MonitorContractError,
    require_deployed_revision,
    require_strategy_progress,
    require_tile_registry,
    resolve_deployed_revision,
)

FLY_BASE = "https://doxed-btc-bot.fly.dev"
HEALTH_URL = "https://doxed-btc-bot.fly.dev/health"
READY_URL = "https://doxed-btc-bot.fly.dev/ready"
DEPLOY_RUNS_PATH = "/actions/workflows/fly-bot-deploy.yml/runs?per_page=50"
INCIDENT_LABEL = "fly-monitor-incident"
INCIDENT_TITLE = "Fly BTC bot monitor incident"
PROBE_ATTEMPTS = 3
PROBE_TIMEOUT_SEC = 20
PROBE_BACKOFF_SEC = (5, 15)
TRANSPORT_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)


class ProbeUnavailable(RuntimeError):
    pass


def probe(url: str, *, accept: Callable[[int, Any], bool]) -> tuple[int, Any]:
    """GET JSON with bounded retries; return the last (status, payload).

    Non-2xx responses with a JSON body (e.g. /ready 503) are real answers and
    are returned after retries so their reasons reach the report.
    """
    last: tuple[int, Any] | None = None
    last_error = ""
    for attempt in range(PROBE_ATTEMPTS):
        if attempt:
            time.sleep(PROBE_BACKOFF_SEC[min(attempt - 1, len(PROBE_BACKOFF_SEC) - 1)])
        try:
            with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SEC) as response:
                last = (response.status, json.load(response))
        except urllib.error.HTTPError as exc:
            try:
                last = (exc.code, json.loads(exc.read() or b"null"))
            except (ValueError, *TRANSPORT_ERRORS):
                last = (exc.code, None)
        except (ValueError, *TRANSPORT_ERRORS) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if accept(*last):
            return last
    if last is None:
        raise ProbeUnavailable(f"{url} unavailable after {PROBE_ATTEMPTS} attempts ({last_error})")
    return last


class GitHub:
    def __init__(self) -> None:
        self.api = f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}"
        self.headers = {
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def call(self, path: str, method: str = "GET", body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            request = urllib.request.Request(self.api + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == 2:
                    raise
            except TRANSPORT_ERRORS:
                if attempt == 2:
                    raise
            time.sleep(3 * (attempt + 1))
        raise AssertionError("unreachable")

    def deploy_state(self) -> dict[str, Any]:
        # The branch-filtered listing is search-backed and intermittently
        # returned months-old runs; filter the plain newest-first list instead.
        runs = [
            run
            for run in self.call(DEPLOY_RUNS_PATH)["workflow_runs"]
            if run.get("head_branch") == "master"
        ]
        deployed, in_flight = resolve_deployed_revision(
            runs, lambda run_id: self.call(f"/actions/runs/{run_id}/jobs?per_page=100")["jobs"]
        )
        active = [run for run in runs if run.get("status") != "completed"]
        return {"deployed": deployed, "in_flight": in_flight, "deploy_active": bool(active)}


def git_resolve(reported: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{reported}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    actual = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", actual):
        return ""
    return actual


def on_master(actual: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", actual, "HEAD"], check=False).returncode == 0


def collect(state: dict[str, Any], now: float) -> tuple[dict[str, str], bool, list[str]]:
    """Probe everything; return (findings, maintenance, informational notes)."""
    findings: dict[str, str] = {}
    notes: list[str] = []
    maintenance = False

    github = GitHub()
    deploy: dict[str, Any] | None = None
    try:
        deploy = github.deploy_state()
        maintenance = deploy["deploy_active"]
        notes.append(
            f"latest successful deploy {deploy['deployed'][:12]}; "
            f"in-flight {deploy['in_flight'][:12] or 'none'}; deploy run active={deploy['deploy_active']}"
        )
    except (MonitorContractError, KeyError, ValueError, *TRANSPORT_ERRORS) as exc:
        findings["monitor_error"] = f"cannot read Fly deploy runs: {type(exc).__name__}: {exc}"

    health: dict[str, Any] | None = None
    try:
        status, payload = probe(
            HEALTH_URL, accept=lambda s, p: s == 200 and isinstance(p, dict) and p.get("process_alive") is True
        )
    except ProbeUnavailable as exc:
        findings["unreachable"] = str(exc)
    else:
        if status != 200 or not isinstance(payload, dict):
            findings["unreachable"] = f"Fly /health returned HTTP {status}"
        else:
            health = payload

    paused: bool | None = None
    if health is not None:
        known_contract = health.get("probe_contract") == "PROCESS_LIVENESS_ONLY"
        alive = health.get("process_alive") is True
        if not known_contract:
            findings["process_down"] = "Fly /health returned an unknown probe contract"
        elif not alive:
            findings["process_down"] = f"Fly process liveness failed (boot={health.get('boot')!r})"
        explicitly_unsafe = (
            health.get("force_paper_mode") is False
            or health.get("live_armed") is True
            or health.get("bitfinex_live_enabled") is True
        )
        explicitly_safe = (
            health.get("force_paper_mode") is True
            and health.get("live_armed") is False
            and health.get("bitfinex_live_enabled") is False
        )
        if explicitly_unsafe or (known_contract and alive and not explicitly_safe):
            findings["safety"] = (
                "Fly bot execution safety drift: "
                f"force_paper_mode={health.get('force_paper_mode')} live_armed={health.get('live_armed')} "
                f"bitfinex_live_enabled={health.get('bitfinex_live_enabled')}"
            )
        paused = bool(health.get("execution_paused") or health.get("manual_admin_pause"))
        if health.get("pause_owner") == "DEPLOY_MAINTENANCE":
            maintenance = True
        notes.append(
            f"Fly {health.get('source_git_rev')} alive={alive} paused={paused} "
            f"pause_owner={health.get('pause_owner')}"
        )

    paused_for = alerts.track_pause(state, paused, now)
    if paused and paused_for >= alerts.PAUSED_ALERT_SEC:
        findings["paper_paused"] = (
            f"paper execution paused for {paused_for / 3600:.1f}h "
            f"(pause_owner={health.get('pause_owner') if health else None}, "
            f"reason={health.get('execution_reason') if health else None!r})"
        )
    findings.update(rules.deploy_stuck_findings(rules.track_deploy_pause(state, health, now), health))
    findings.update(rules.disk_findings(health))
    findings.update(rules.transfer_findings(health))
    findings.update(rules.collection_findings(health))
    findings.update(rules.laptop_heartbeat_findings(os.environ.get("LAPTOP_CHAIN_HEARTBEAT"), now))
    if health is not None:
        volume = health.get("volume")
        if isinstance(volume, dict):
            notes.append(
                f"volume used_pct={volume.get('used_pct')} free_bytes={volume.get('free_bytes')} "
                f"growth_bytes_per_hour={volume.get('growth_bytes_per_hour')}"
            )
        else:
            notes.append("Fly /health has no volume block (revision predates disk metrics)")

    if health is not None and health.get("process_alive") is True and deploy is not None:
        reported = str(health.get("source_git_rev") or health.get("git_rev") or "")
        actual = git_resolve(reported) if re.fullmatch(r"[0-9a-f]{7,40}", reported) else ""
        if not actual:
            findings["revision_drift"] = f"Fly returned an unknown source revision: {reported!r}"
        else:
            try:
                require_deployed_revision(actual, deployed=deploy["deployed"], in_flight=deploy["in_flight"])
            except MonitorContractError:
                # The runs API is eventually consistent right after a deploy.
                time.sleep(20)
                try:
                    deploy = github.deploy_state()
                    require_deployed_revision(actual, deployed=deploy["deployed"], in_flight=deploy["in_flight"])
                except MonitorContractError as exc:
                    findings["revision_drift"] = str(exc)
                except (KeyError, ValueError, *TRANSPORT_ERRORS) as exc:
                    findings["monitor_error"] = f"cannot re-read Fly deploy runs: {type(exc).__name__}"
            if "revision_drift" not in findings and not on_master(actual):
                findings["revision_drift"] = f"Fly revision is not on master history: {actual[:12]}"

    ready: dict[str, Any] | None = None
    try:
        status, payload = probe(
            READY_URL, accept=lambda s, p: s == 200 and isinstance(p, dict) and p.get("ok") is True
        )
    except ProbeUnavailable as exc:
        if "unreachable" not in findings:
            findings["not_ready"] = str(exc)
    else:
        if not isinstance(payload, dict):
            findings["not_ready"] = f"Fly /ready returned HTTP {status} without a JSON body"
        else:
            ready = payload
            runtime = payload.get("runtime_readiness") or {}
            progress = payload.get("strategy_progress") or {}
            if status != 200 or payload.get("ok") is not True or payload.get("process_ready") is not True:
                findings["not_ready"] = (
                    f"Fly /ready HTTP {status} status={payload.get('status')!r} "
                    f"readiness_reasons={runtime.get('readiness_reasons')} "
                    f"progress_reasons={progress.get('reasons')} market_data_mode={payload.get('market_data_mode')}"
                )
            elif isinstance(progress, dict):
                try:
                    require_strategy_progress(payload)
                except MonitorContractError as exc:
                    findings["not_ready"] = str(exc)
    findings.update(rules.cadence_findings(ready, paused=paused, now=now))

    if ready is not None and isinstance(ready.get("active_tiles"), list):
        import combo_pathway_config as registry

        try:
            require_tile_registry(
                ready,
                expected_version=registry.EXECUTION_FIX_VERSION,
                expected_signature=registry.active_tile_registry_signature(),
                expected_lanes=registry.ACTIVE_TILE_ORDER,
            )
        except MonitorContractError as exc:
            findings["registry_drift"] = str(exc)

    return findings, maintenance, notes


def issue_body(
    state: dict[str, Any],
    now: float,
    *,
    label: str = INCIDENT_LABEL,
    source: str = "the scheduled **Monitor Fly BTC bot** workflow",
    link: str | None = None,
) -> str:
    lines = [
        f"<!-- {label} -->",
        f"Opened by {source}. This issue is edited in place;",
        "comments are only added when a condition first alerts, re-alerts, or recovers.",
        "",
        "| Condition | Since (UTC) | Last alert (UTC) | Latest detail |",
        "|---|---|---|---|",
    ]
    for key, entry in sorted(alerts.active_alerted(state).items()):
        lines.append(
            f"| `{key}` | {_ts(entry.get('first_seen'))} | {_ts(entry.get('last_alert'))} | "
            f"{str(entry.get('last_message', '')).replace('|', '/')[:400]} |"
        )
    lines += ["", f"Last checked {_ts(now)} by {link or 'run ' + _run_url()}"]
    return "\n".join(lines)


def sync_issue(
    state: dict[str, Any],
    decisions: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    now: float,
    *,
    client: Any = None,
    label: str = INCIDENT_LABEL,
    title: str = INCIDENT_TITLE,
    source: str = "the scheduled **Monitor Fly BTC bot** workflow",
    link: str | None = None,
) -> None:
    """Keep exactly one open issue per ``label`` in step with the dedup state."""
    github = client if client is not None else GitHub()
    where = link or _run_url()
    body = lambda: issue_body(state, now, label=label, source=source, link=link)  # noqa: E731
    open_issues = github.call(f"/issues?state=open&labels={label}&per_page=5")
    issue = open_issues[0] if open_issues else None
    fired = [d for d in decisions if d["action"] == "alert"]
    active = alerts.active_alerted(state)

    if fired and issue is None:
        try:
            github.call("/labels", "POST", {"name": label, "color": "b60205"})
        except urllib.error.HTTPError as exc:
            if exc.code != 422:
                raise
        github.call("/issues", "POST", {"title": title, "body": body(), "labels": [label]})
        return
    if issue is None:
        return
    if fired:
        text = "\n".join(f"- **{d['key']}**: {d['message']}" for d in fired)
        github.call(f"/issues/{issue['number']}/comments", "POST", {"body": f"Alert ({where}):\n{text}"})
    if active:
        github.call(f"/issues/{issue['number']}", "PATCH", {"body": body()})
        return
    recovered = ", ".join(f"`{r['key']}`" for r in resolved) or "all conditions"
    github.call(f"/issues/{issue['number']}/comments", "POST", {"body": f"Recovered: {recovered} ({where})."})
    github.call(f"/issues/{issue['number']}", "PATCH", {"state": "closed", "state_reason": "completed"})


def _ts(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(value))


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"


def _escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def informational_keys() -> frozenset[str]:
    """Transfer lag only annotates runs until the segment pipeline is declared live."""
    if os.environ.get("FLY_MONITOR_SEGMENTS_LIVE", "").strip() == "1":
        return frozenset()
    return frozenset({"transfer_lag"})


def main() -> int:
    state_path = Path(os.environ.get("FLY_MONITOR_STATE", ".fly-monitor-state/state.json"))
    try:
        state = alerts.normalize_state(json.loads(state_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        state = alerts.empty_state()
    now = time.time()
    try:
        try:
            findings, maintenance, notes = collect(state, now)
        except Exception as exc:  # a broken monitor alerts through the same dedup policy
            traceback.print_exc()
            findings, maintenance, notes = {"monitor_error": f"monitor crashed: {type(exc).__name__}: {exc}"}, False, []
        if os.environ.get("FLY_MONITOR_TEST_ALERT", "").strip() == "1":
            findings["test_alert"] = "synthetic test alert requested via workflow_dispatch (not a real incident)"
        decisions, resolved = alerts.evaluate(
            state, findings, now=now, maintenance=maintenance, informational=informational_keys()
        )
    finally:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    for note in notes:
        print(note)
    if maintenance:
        print("Guarded deploy / DEPLOY_MAINTENANCE active: transitional conditions are suppressed "
              f"for up to {alerts.MAINTENANCE_GRACE_SEC / 60:.0f} minutes.")
    for decision in decisions:
        level = {"alert": "error", "info": "notice"}.get(decision["action"], "warning")
        print(f"::{level} title=fly-monitor {decision['key']} ({decision['action']})::{_escape(decision['message'])}")
    for item in resolved:
        print(f"Recovered: {item['key']}")
    if not [d for d in decisions if d["action"] != "info"]:
        print("Fly bot healthy, paper-only, disarmed, on the latest deployed revision, and strategy progressing.")

    try:
        sync_issue(state, decisions, resolved, now)
    except (KeyError, ValueError, *TRANSPORT_ERRORS) as exc:
        print(f"::warning title=fly-monitor issue sync::{type(exc).__name__}: {exc}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("### Fly monitor\n\n")
            out.write("\n".join(f"- {n}" for n in notes) + "\n\n")
            for decision in decisions:
                out.write(f"- **{decision['key']}** ({decision['action']}): {decision['message']}\n")
            if not decisions:
                out.write("All checks passed.\n")

    return 1 if any(d["action"] == "alert" for d in decisions) else 0


if __name__ == "__main__":
    raise SystemExit(main())
