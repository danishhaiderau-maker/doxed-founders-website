import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fly_reset_plan_live_gates as gates

NOW = dt.datetime(2026, 10, 3, 23, 40, tzinfo=dt.timezone.utc)
REV = "f187f4937ec7a1a0ce2fadddfed4181ea24521b2"
SNAP = [{"status": "created", "created_at": "2026-10-03T23:16:00Z"}]


def _live(epoch="ce-final-d", started="2026-10-03T23:25:00Z"):
    manifest = json.dumps({"epoch_id": epoch, "started_at_utc": started, "schema": "x"}, indent=2)
    return manifest + "\n" + REV + "\n"


def test_pretty_printed_manifest_passes_and_prints_the_execute_token():
    out = gates.evaluate(_live(), SNAP, "ce-final-d", NOW)
    assert out["execute_gate_failures"] == []
    assert out["execute_token"] == "RESET-AT-BOUNDARY:ce-final-d:f187f4937ec7"
    assert out["window_closes_utc"].startswith("2026-10-04T00:25:00")


def test_dispatch_ref_epoch_window_and_snapshot_gates_fail_closed():
    failures = gates.evaluate(_live(epoch="ce-final-c"), SNAP, "ce-final-d", NOW)["execute_gate_failures"]
    assert any(f.startswith("dispatch-ref fly.toml DATA_EPOCH_ID=ce-final-d but live data epoch=ce-final-c") for f in failures)
    assert any("epoch window" in f for f in gates.evaluate(_live(started="2026-10-03T22:30:00Z"), SNAP, "ce-final-d", NOW)["execute_gate_failures"])
    old = [{"status": "created", "created_at": "2026-10-03T10:00:00Z"}]
    assert any("snapshot" in f for f in gates.evaluate(_live(), old, "ce-final-d", NOW)["execute_gate_failures"])
    assert "live SOURCE_GIT_REV unavailable" in gates.evaluate("{}", SNAP, "x", NOW)["execute_gate_failures"]
