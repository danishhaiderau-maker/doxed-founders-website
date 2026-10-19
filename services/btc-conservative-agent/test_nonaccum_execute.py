"""Guarded C1-C4 executor: dry-run default, sha re-verification, gates, protected names, current dirs kept, abort on RED."""
import json
import os

import pytest

import nonaccum_execute as ex
import nonaccum_plans as np_
from test_nonaccum_plans import EPOCH, _snapshot, _tree


def fake_get(status=None, checks=None, pending=0, rss=500 * 1024 ** 2):
    state = {"live_armed": False, "force_paper_mode": True, "rss": rss, "pending": pending, "checks": dict(checks or {})}
    if status:
        state.update(status)

    def get(path):
        if path == "/api/status":
            return {"live_armed": state["live_armed"], "force_paper_mode": state["force_paper_mode"],
                    "runtime_telemetry": {"latest": {"memory": {"rss_bytes": state["rss"]}}}, "relay_outbox": {"pending_total": state["pending"]}}
        return {"checks": {n: {"status": s} for n, s in state["checks"].items()}}
    get.state = state
    return get


def _setup(tmp_path):
    root = str(tmp_path)
    now = _tree(root)
    doc = np_.build_all(root, EPOCH, now)
    return root, now, doc["plan_sha256"], doc


def test_dry_run_changes_nothing_and_reports_counts(tmp_path):
    root, now, sha, doc = _setup(tmp_path)
    before = _snapshot(root)
    res = ex.run(root, EPOCH, sha, fake_get(), classes=("C1", "C2", "C3a", "C3b", "C4"), execute=False, sleep_s=0, now=now)
    assert res["status"] == "DONE" and _snapshot(root) == before
    assert res["totals"]["C1"]["removed"] == 1 and "C3b" not in res["totals"]      # tape excluded without --include-tape


def test_execute_requires_matching_confirm_sha_and_plan_sha(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    with pytest.raises(ex.Abort):
        ex.run(root, EPOCH, sha, fake_get(), execute=True, confirm_sha="nope", now=now)
    res = ex.run(root, EPOCH, "0" * 64, fake_get(), execute=True, confirm_sha="0" * 64, sleep_s=0, now=now)
    assert res["status"] == "ABORTED" and "plan sha" in res["reason"]


def test_execute_removes_only_planned_pre_epoch_items_and_keeps_current_and_protected(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    v3 = os.path.join(root, "runtime", "v3")
    rec = os.path.join(v3, "receipts", "emergency_record_idempotency_v1")
    keep = [f"{rec}/decision/{'2'*64}.json", f"{rec}/decision/{'3'*64}.json", f"{rec}/decision/complete.json", f"{rec}/decision/bootstrap.json",
            f"{rec}/round_robin.json", f"{rec}/append_heads/{'4'*64}.json", f"{rec}/evidence_failure/{'5'*64}.json",
            f"{v3}/signal_snapshots_v1/{'7'*64}.json", f"{v3}/signal_snapshots_v1/{'8'*64}.json",
            f"{v3}/lifecycle_transfer_bundles/aa/transfer-{'9'*64}/b.json", f"{v3}/lifecycle_transfer_bundles/cc/transfer-{'c'*64}/b.json",
            f"{root}/tape_1s/new.jsonl.gz", f"{root}/tape_1s/old.jsonl.gz", f"{root}/runtime/relay_switch.json"]
    gone = [f"{rec}/decision/{'1'*64}.json", f"{v3}/signal_snapshots_v1/{'6'*64}.json",
            f"{v3}/lifecycle_transfer_bundles/bb/transfer-{'b'*64}/b.json"]
    res = ex.run(root, EPOCH, sha, fake_get(), execute=True, confirm_sha=sha, sleep_s=0, batch=1, now=now)
    assert res["status"] == "DONE", res
    assert all(os.path.exists(p) for p in keep) and not any(os.path.exists(p) for p in gone)
    assert not os.path.exists(f"{v3}/lifecycle_transfer_bundles/bb")                    # emptied dir removed (rmdir only)
    assert res["current_bundle_dirs_before"] == res["current_bundle_dirs_after"] == 1
    t = res["totals"]
    assert (t["C1"]["removed"], t["C2"]["removed"], t["C3a"]["removed"], t["C4"]["removed"]) == (1, 1, 1, 3)
    lines = [json.loads(x) for f in ("relay_outbox_retired.jsonl", "relay_outbox_quarantine.jsonl") for x in open(f"{root}/runtime/{f}")]
    assert [x["created_at_unix"] for x in lines] == [EPOCH + 5]                          # only the current-epoch line survives


def test_tape_is_never_deleted(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    res = ex.run(root, EPOCH, sha, fake_get(), classes=("C3b",), execute=True, confirm_sha=sha, sleep_s=0, now=now)
    assert "C3b" not in res["totals"] and os.path.exists(f"{root}/tape_1s/old.jsonl.gz")
    with pytest.raises(ex.Abort):
        ex.run(root, EPOCH, sha, fake_get(), classes=("C3b",), execute=True, confirm_sha=sha, sleep_s=0, include_tape=True, now=now)
    assert os.path.exists(f"{root}/tape_1s/old.jsonl.gz")


def test_gates_live_on_force_paper_off_and_red_abort_before_and_during(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    before = _snapshot(root)
    for bad in ({"live_armed": True}, {"force_paper_mode": False}):
        with pytest.raises(ex.Abort):
            ex.run(root, EPOCH, sha, fake_get(status=bad), execute=True, confirm_sha=sha, now=now)
    with pytest.raises(ex.Abort):
        ex.run(root, EPOCH, sha, fake_get(checks={"exits-latency-guard": "RED"}), execute=True, confirm_sha=sha, now=now)   # not accepted
    assert _snapshot(root) == before
    # accepted baseline RED is fine; a NEW RED mid-run aborts after the batch and stops further deletion
    g = fake_get(checks={"exits-latency-guard": "RED", "tape-retention": "GREEN"})
    calls = {"n": 0}
    orig = g

    def get(path):
        if path == "/api/monitor/checks":
            calls["n"] += 1
            if calls["n"] >= 3:
                orig.state["checks"]["tape-retention"] = "RED"
        return orig(path)
    res = ex.run(root, EPOCH, sha, get, execute=True, confirm_sha=sha, sleep_s=0, batch=1, accepted_red=("exits-latency-guard",), now=now)
    assert res["status"] == "ABORTED" and "tape-retention" in res["reason"]


def test_rss_growth_and_pending_outbox_abort(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    res = ex.run(root, EPOCH, sha, fake_get(pending=2), classes=("C4",), execute=True, confirm_sha=sha, sleep_s=0, now=now)
    assert res["status"] == "ABORTED" and "pending_total" in res["reason"]
    with pytest.raises(ex.Abort):
        ex.run(root, EPOCH, sha, fake_get(rss=3 * 1024 ** 3), execute=False, now=now)


def test_changed_since_plan_files_are_skipped_and_smaller_sha_needs_counts(tmp_path):
    root, now, sha, doc = _setup(tmp_path)
    p = f"{root}/runtime/v3/signal_snapshots_v1/{'6'*64}.json"
    os.utime(p, (EPOCH + 999, EPOCH + 999))             # became "current" after planning
    res = ex.run(root, EPOCH, sha, fake_get(), classes=("C2",), execute=True, confirm_sha=sha, sleep_s=0, now=now)
    assert res["status"] == "ABORTED"                    # the rebuilt plan no longer matches the approved sha
    counts = {k: v["count"] for k, v in doc["plans"].items()}
    res = ex.run(root, EPOCH, sha, fake_get(), classes=("C2",), execute=True, confirm_sha=sha, sleep_s=0, now=now, expected_counts=counts)
    assert res["status"] == "DONE" and res["totals"]["C2"]["removed"] == 0 and os.path.exists(p)


def test_executor_source_has_no_archive_or_rmtree_and_no_order_calls():
    src = open(os.path.join(os.path.dirname(__file__), "nonaccum_execute.py"), encoding="utf-8").read()
    for tok in ("rmtree", "shutil.copy", "shutil.move", "tarfile", "zipfile", "gzip.open", "place_order", "cancel_order"):
        assert tok not in src, tok
