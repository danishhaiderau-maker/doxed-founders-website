"""HM #848 nit: with the 1s tape R3-quarantined, shadow_exit_backfill reports BLOCKED (like --skip-tape), not a crash."""
import json

from research import shadow_exit_backfill as sxb


def test_quarantined_tape_is_blocked_not_a_crash(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert sxb.main(["--mirror", str(tmp_path), "--tier-a", str(tmp_path / "tier_a"), "--out-dir", str(out)]) == 0
    receipt = json.loads((out / "backfill_receipt.json").read_text())
    assert receipt["tape_status"].startswith("BLOCKED_R3_QUARANTINE")
    assert receipt["tape_seconds"] is None and "tape_episodes" not in receipt
