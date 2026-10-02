"""Safe, journalled, laptop-only auto-repair.

The laptop supervisor already restarts the :9001 dashboard, the segment pull
loop (ACK/copy retries) and runs v2c auto-ff; the watcher task restarts :9011.
So self-aware never restarts those itself: when their owner has stopped
advancing, it runs the owner's existing scheduled task early (the same thing
the scheduler does every few minutes), with a cooldown and a daily cap.

Anything touching trading, Fly, the relay or Bitfinex is FLAG-ONLY: it is
recorded with the reason and never executed. Every decision is journalled in
``repair-journal.jsonl`` (also queryable as ``raw_self_journal``).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .config import Paths
from .facts import iso, parse_ts, snapshot_age, watcher_check

COOLDOWN_SEC = 15 * 60
DAILY_CAP = 6


@dataclass(frozen=True)
class Action:
    id: str
    description: str
    mode: str  # AUTO (laptop-only, safe) | FLAG (never executed)
    when: Callable[[dict, dict], str | None]
    task: str | None = None


def _supervisor_stale(f: dict, findings: dict) -> str | None:
    w = watcher_check(f, "laptop.supervisor") or {}
    age = snapshot_age(f.get("runtime"), f["now"])
    if w.get("status") in ("AMBER", "RED") and (age is None or age > 15 * 60):
        return f"laptop.supervisor {w.get('status')} ({w.get('observed')}); Fly snapshots {age and round(age)}s old"
    return None


def _pull_stalled(f: dict, findings: dict) -> str | None:
    pull = f.get("pull") or {}
    done = parse_ts(pull.get("finishedAt"))
    if done and f["now"] - done > 15 * 60 and (findings.get("inv.custody") or {}).get("severity") in ("AMBER", "RED"):
        return f"segment pull last finished {round((f['now'] - done) / 60)} min ago and custody is not GREEN"
    return None


def _watcher_stale(f: dict, findings: dict) -> str | None:
    rep = f.get("watcher") or {}
    at = parse_ts(rep.get("generated_ts")) or parse_ts(rep.get("generated_at"))
    if at is None or f["now"] - at > 20 * 60:
        return f"watcher verdict is {round((f['now'] - (at or 0)) / 60)} min old"
    return None


def _trading_flag(f: dict, findings: dict) -> str | None:
    bad = [k for k in ("inv.expired_filled", "inv.fill_close") if (findings.get(k) or {}).get("severity") == "RED"]
    rel = f.get("relay") or {}
    if rel.get("relayArmedAt") or str(rel.get("relayExecutionMode") or "").upper() not in ("", "PAUSED", "DISARMED", "OFF"):
        bad.append(f"relay mode {rel.get('relayExecutionMode')}")
    return ("trading/relay evidence needs a human: " + ", ".join(bad)) if bad else None


ACTIONS: tuple[Action, ...] = (
    Action("nudge_supervisor", "Run DoxxedLaptopChainSupervisor now (it restarts :9001, the pull loop and auto-ff)",
           "AUTO", _supervisor_stale, "DoxxedLaptopChainSupervisor"),
    Action("nudge_pull_via_supervisor", "Segment pull/ACK stalled: run the supervisor now so it restarts the pull loop",
           "AUTO", _pull_stalled, "DoxxedLaptopChainSupervisor"),
    Action("nudge_watcher", "Health watcher stale: run DoxxedSystemHealthWatcher now", "AUTO", _watcher_stale,
           "DoxxedSystemHealthWatcher"),
    Action("trading_needs_human", "Trading, relay or Bitfinex anomaly: flag only, never auto-repaired", "FLAG",
           _trading_flag, None),
)


def _journal(paths: Paths, row: dict[str, Any]) -> None:
    paths.journal.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.journal, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _run_task(task: str) -> tuple[bool, str]:
    try:
        out = subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.returncode == 0, (out.stdout or out.stderr).strip()[:200]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


def run(paths: Paths, facts: dict, findings: list[dict], state: dict, *, enabled: bool = True,
        runner: Callable[[str], tuple[bool, str]] = _run_task) -> list[dict[str, Any]]:
    now = facts["now"]
    by_id = {f["id"]: f for f in findings}
    rs = state.setdefault("repairs", {})
    out = []
    for action in ACTIONS:
        reason = action.when(facts, by_id)
        if not reason:
            continue
        a = rs.setdefault(action.id, {"last": 0, "day": None, "count": 0})
        today = iso(now)[:10]
        if a["day"] != today:
            a.update(day=today, count=0)
        row = {"at": iso(now), "kind": "REPAIR", "action": action.id, "mode": action.mode, "reason": reason,
               "description": action.description}
        if action.mode == "FLAG":
            if now - a["last"] < COOLDOWN_SEC * 4:
                continue
            row["outcome"] = "FLAGGED_NOT_EXECUTED"
        elif not enabled:
            row["outcome"] = "SKIPPED_REPAIR_DISABLED"
        elif now - a["last"] < COOLDOWN_SEC:
            continue
        elif a["count"] >= DAILY_CAP:
            row["outcome"] = "SKIPPED_DAILY_CAP"
        else:
            ok, detail = runner(action.task)
            row.update(outcome="EXECUTED" if ok else "FAILED", detail=detail, task=action.task)
            a["count"] += 1
        a["last"] = now
        _journal(paths, row)
        out.append(row)
    return out
