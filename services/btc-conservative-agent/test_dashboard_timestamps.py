"""Side-effect-free regression checks for timestamps on the :7002 dashboard.

Run:
    cd services/btc-conservative-agent
    python test_dashboard_timestamps.py
"""
from pathlib import Path
import sys

SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}")
    return bool(condition)


checks = [
    (
        "active signals identify signal time",
        "<th>Signal Time (Melbourne)</th>" in SOURCE,
    ),
    (
        "positions identify fill/entry time",
        "<th>Fill / Entry Time (Melbourne)</th>" in SOURCE,
    ),
    (
        "pending orders identify creation time",
        "<th>Order Time (Melbourne)</th>" in SOURCE,
    ),
    (
        "position rows render the fill timestamp",
        "l.entry_ts_melbourne || formatMelbourneDateTime(l.entry_ts || l.fill_ts || l.open_ts)"
        in SOURCE,
    ),
    (
        "pending rows render the creation timestamp",
        "o.created_ts_melbourne || formatMelbourneDateTime(o.created_ts)"
        in SOURCE,
    ),
    (
        "position API rows include a Melbourne timestamp",
        'pos_copy["entry_ts_melbourne"]' in SOURCE,
    ),
    (
        "pending-order API rows include a Melbourne timestamp",
        'oc["created_ts_melbourne"]' in SOURCE,
    ),
    (
        "paused shadow separates shared AI-call and lane-recorded times",
        "<th>AI Call Time (Melbourne)</th>" in SOURCE
        and "<th>Lane Recorded (Melbourne)</th>" in SOURCE,
    ),
    (
        "paused shadow exposes the shared paid-call identity",
        "<th>Shared Call ID</th>" in SOURCE
        and "shared_ai_call_id" in SOURCE,
    ),
]

passed = sum(check(name, condition) for name, condition in checks)
failed = len(checks) - passed
print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
