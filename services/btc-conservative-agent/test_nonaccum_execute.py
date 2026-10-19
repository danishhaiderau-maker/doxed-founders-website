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
    for d in (f"{v3}/lifecycle_transfer_bundles/bb/transfer-{'b'*64}", f"{v3}/lifecycle_transfer_bundles/bb"):
        os.utime(d, (EPOCH - 500, EPOCH - 500))          # an old bundle dir untouched since before the epoch
    res = ex.run(root, EPOCH, sha, fake_get(), execute=True, confirm_sha=sha, sleep_s=0, batch=1, now=now)
    assert res["status"] == "DONE", res
    assert all(os.path.exists(p) for p in keep) and not any(os.path.exists(p) for p in gone)
    assert not os.path.exists(f"{v3}/lifecycle_transfer_bundles/bb")                    # emptied dir removed (rmdir only)
    assert res["current_bundle_dirs_before"] == res["current_bundle_dirs_after"] == 1
    t = res["totals"]
    assert (t["C1"]["removed"], t["C2"]["removed"], t["C3a"]["removed"]) == (1, 1, 1) and "C4" not in t
    lines = [json.loads(x) for f in ("relay_outbox_retired.jsonl", "relay_outbox_quarantine.jsonl") for x in open(f"{root}/runtime/{f}")]
    assert len(lines) == 4                                                               # C4 excluded: relay ledgers untouched


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
        assert ex.run(root, EPOCH, sha, fake_get(status=bad), execute=True, confirm_sha=sha, now=now)["status"] == "ABORTED"
    assert ex.run(root, EPOCH, sha, fake_get(checks={"exits-latency-guard": "RED"}), execute=True, confirm_sha=sha, now=now)["status"] == "ABORTED"   # not accepted
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
    before = _snapshot(root)
    logp = str(tmp_path / "exec.jsonl")
    res = ex.run(root, EPOCH, sha, fake_get(pending=2), classes=("C4",), execute=True, confirm_sha=sha, sleep_s=0, now=now, log_path=logp)
    assert res["status"] == "DONE" and res["totals"] == {}                               # C4 is always skipped now
    assert any(json.loads(x).get("event") == "c4_skipped" for x in open(logp))
    items = list(np_.build_all(root, EPOCH, now, include_c4=True) and np_.ITEMS["C4"])
    totals = {"C4": {"removed": 0, "bytes": 0, "skipped_changed_or_gone": 0}}
    with pytest.raises(ex.Abort, match="pending_total"):
        ex.rewrite_c4(root, EPOCH, items, True, lambda r: None, totals, lambda: 2)
    r2 = ex.run(root, EPOCH, sha, fake_get(rss=3 * 1024 ** 3), execute=False, now=now)
    assert r2["status"] == "ABORTED" and "RSS" in r2["reason"]


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


def test_c3a_rmdir_skips_dirs_touched_at_or_after_the_epoch_and_never_dirs_outside_planned_items(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    b = f"{root}/runtime/v3/lifecycle_transfer_bundles"
    # dir mtimes are fresh (>= epoch) in this fixture: the planned file goes, the (touched) dirs stay
    unrelated = f"{b}/zz/empty-unplanned-dir"
    os.makedirs(unrelated)
    res = ex.run(root, EPOCH, sha, fake_get(), classes=("C3a",), execute=True, confirm_sha=sha, sleep_s=0, now=now)
    assert res["status"] == "DONE" and os.path.isdir(f"{b}/bb/transfer-{'b'*64}") and os.path.isdir(unrelated)


def test_symlinked_component_and_bad_names_are_rejected(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    v3 = f"{root}/runtime/v3"
    assert ex._path_ok(f"{v3}/signal_snapshots_v1", f"{v3}/signal_snapshots_v1/{'6'*64}.json")
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "x.json").write_text("x")
    link = f"{v3}/lifecycle_transfer_bundles/ln"
    os.symlink(str(outside), link)
    assert not ex._path_ok(f"{v3}/lifecycle_transfer_bundles", f"{link}/x.json")
    assert not ex._path_ok(f"{v3}/lifecycle_transfer_bundles", f"{v3}/lifecycle_transfer_bundles/../x.json")
    with pytest.raises(ex.Abort):
        ex._file_targets("C3a", root, [("aa/not-a-transfer/b.json", 1, 1)])
    with pytest.raises(ex.Abort):
        ex._file_targets("C3a", root, [(f"aa/transfer-{'9'*64}/../../x.json", 1, 1)])
    assert ex._file_targets("C3a", root, [(f"aa/transfer-{'9'*64}/b.json", 1, 1)])
    # a symlink swapped in at the planned file path is skipped, never followed
    p = f"{v3}/signal_snapshots_v1/{'6'*64}.json"
    keep = tmp_path / "victim.json"; keep.write_text("v")
    os.unlink(p); os.symlink(str(keep), p)
    with pytest.raises(ex.Abort):
        totals = {"C2": {"removed": 0, "bytes": 0, "skipped_changed_or_gone": 0}}
        ex.delete_files("C2", root, EPOCH, [(f"{'6'*64}.json", 1, 1)], True, 10, 0, lambda: None, lambda r: None, totals)
    assert keep.exists()


def test_oserror_ends_with_summary_not_traceback(tmp_path):
    root, now, sha, doc = _setup(tmp_path)
    counts = {k: v["count"] for k, v in doc["plans"].items()}
    orig, orig_build = ex.P.ITEMS, ex.P.build_all
    orig_cur = ex.current_bundle_dirs
    def boom(r, e): raise OSError("bundle walk failed")
    ex.current_bundle_dirs = boom
    try:
        res = ex.run(root, EPOCH, sha, fake_get(), classes=("C1",), execute=True, confirm_sha=sha, sleep_s=0, now=now)
    finally:
        ex.P.ITEMS, ex.P.build_all, ex.current_bundle_dirs = orig, orig_build, orig_cur
    assert res["status"] == "ABORTED" and "OSError" in res["reason"]


def test_api_latency_over_limit_aborts_and_is_reported(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    clock = {"t": 0.0}
    real = ex.time.monotonic
    ex.time.monotonic = lambda: clock.__setitem__("t", clock["t"] + 6.0) or clock["t"]     # every call advances 6 s
    try:
        with pytest.raises(ex.Abort, match="API latency"):
            ex.gate(fake_get(), None, set())
    finally:
        ex.time.monotonic = real
    assert "api_latency_s" in ex.gate(fake_get(), None, set())


def test_c4_pending_rechecked_right_before_the_replace(tmp_path):
    root, now, sha, doc = _setup(tmp_path)
    np_.build_all(root, EPOCH, now, include_c4=True)
    items = list(np_.ITEMS["C4"])
    before = _snapshot(root)
    seq = iter([0, 3])                                                    # clean at the start, an event arrives before the replace
    totals = {"C4": {"removed": 0, "bytes": 0, "skipped_changed_or_gone": 0}}
    with pytest.raises(ex.Abort, match="right before the replace"):
        ex.rewrite_c4(root, EPOCH, items, True, lambda r: None, totals, lambda: next(seq, 3))
    assert _snapshot(root) == before and not any(f.endswith(".nonaccum.tmp") for f in os.listdir(f"{root}/runtime"))


def test_current_dirs_shrink_is_checked_per_class(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    cur = f"{root}/runtime/v3/lifecycle_transfer_bundles/cc"
    orig = ex.current_bundle_dirs
    n = {"c": 0}

    def shrinking(r, e):
        n["c"] += 1
        return orig(r, e) - (1 if n["c"] >= 3 else 0)      # drops after the first batches
    ex.current_bundle_dirs = shrinking
    try:
        res = ex.run(root, EPOCH, sha, fake_get(), execute=True, confirm_sha=sha, sleep_s=0, batch=1, now=now)
    finally:
        ex.current_bundle_dirs = orig
    assert res["status"] == "ABORTED" and "shrank" in res["reason"]


def test_first_gate_failure_and_bad_json_still_end_with_summary(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    res = ex.run(root, EPOCH, sha, fake_get(status={"live_armed": True}), sleep_s=0, now=now)
    assert res["status"] == "ABORTED" and "live_armed" in res["reason"] and res["totals"] == {}

    def bad(path):
        return json.loads("{not json")
    res = ex.run(root, EPOCH, sha, bad, sleep_s=0, now=now)
    assert res["status"] == "ABORTED" and "bad API response" in res["reason"]

    def timeout(path):
        raise TimeoutError("timed out")
    res = ex.run(root, EPOCH, sha, timeout, sleep_s=0, now=now)
    assert res["status"] == "ABORTED" and "OSError" in res["reason"]


def test_build_all_failure_ends_with_summary(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    orig = ex.P.build_all
    ex.P.build_all = lambda *a, **k: (_ for _ in ()).throw(OSError("scan failed"))
    try:
        res = ex.run(root, EPOCH, sha, fake_get(), sleep_s=0, now=now)
    finally:
        ex.P.build_all = orig
    assert res["status"] == "ABORTED" and "scan failed" in res["reason"]


def test_tmp_file_removed_when_replace_raises(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    np_.build_all(root, EPOCH, now, include_c4=True)
    items = list(np_.ITEMS["C4"])
    totals = {"C4": {"removed": 0, "bytes": 0, "skipped_changed_or_gone": 0}}
    real = os.replace
    def boom(a, b): raise OSError("replace failed")
    ex.os.replace = boom
    try:
        with pytest.raises(OSError, match="replace failed"):
            ex.rewrite_c4(root, EPOCH, items, True, lambda r: None, totals, lambda: 0)
    finally:
        ex.os.replace = real
    assert not any(f.endswith(".nonaccum.tmp") for f in os.listdir(f"{root}/runtime"))


def test_bundle_dirs_walked_once_per_class_not_per_batch(tmp_path):
    root, now, sha, _ = _setup(tmp_path)
    orig = ex.current_bundle_dirs
    n = {"c": 0}
    def counting(r, e):
        n["c"] += 1
        return orig(r, e)
    ex.current_bundle_dirs = counting
    try:
        res = ex.run(root, EPOCH, sha, fake_get(), execute=True, confirm_sha=sha, sleep_s=0, batch=1, now=now)
    finally:
        ex.current_bundle_dirs = orig
    assert res["status"] == "DONE" and n["c"] == 1 + 3 + 1     # before + one per class (C1,C2,C3a; C4 excluded) + final


def test_cli_exits_nonzero_on_aborted_and_zero_on_done(tmp_path, monkeypatch):
    root, now, sha, _ = _setup(tmp_path)
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "x")
    monkeypatch.setattr(ex.os, "nice", lambda n: 0)
    monkeypatch.setattr(ex, "http_json", lambda base, token: fake_get(status={"live_armed": True}))
    assert ex.main([root, str(EPOCH), "--expected-sha", sha]) == 2           # gate abort
    monkeypatch.setattr(ex, "http_json", lambda base, token: fake_get())
    assert ex.main([root, str(EPOCH), "--expected-sha", "0" * 64]) == 2      # sha mismatch
    assert ex.main([root, str(EPOCH), "--expected-sha", sha, "--sleep", "0"]) == 0   # dry run DONE
