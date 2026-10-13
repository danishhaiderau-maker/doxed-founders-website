import os
import time

import integrity_gaps as ig
import keep_manifest as km

PINNED_LITERAL = "3e70040f172fce88451938614db0836897a0b391786b6646e3d8a9ed573f4a86"


def test_manifest_is_minimal_config_per_danish_1342():
    assert "13:42" in km.DECISION
    assert km.NEON_TABLES == () and km.R2_KEEP_PREFIXES == () and km.BOX == ()
    for f in ("bitfinex_live_state.json", "bitfinex_live_switch_state.json", "config-7002.json",
              "spread-gate.json", "policy.json"):
        assert km.is_kept(f"runtime/{f}"), f
    for f in ("live_execution_reports.jsonl", "live_copy_approvals.jsonl", "bitfinex_order_action_audit.jsonl",
              "order_intent.jsonl", "v3/ledgers/execution.jsonl", "v3/ledgers/lifecycle.jsonl",
              "ledger_reconciliation.json", "lane_pnl_ledger.json", "execution_settings_history.jsonl",
              "orders.json", "positions.json", "keep-20261013T002513.tgz"):
        assert not km.is_kept(f"runtime/{f}"), f


def test_manifest_hash_is_pinned():
    assert km.MANIFEST_SHA256 == PINNED_LITERAL


def test_nothing_in_r2_is_kept():
    for k in ("fly-mirror-segments/tree/live_execution_reports.jsonl.zst", "keep/keep.tgz",
              "fly-segments/v3/seg/1.tar.gz.zst"):
        assert not km.is_r2_kept(k)


def test_scan_reports_keep_separately(tmp_path):
    (tmp_path / "runtime" / "v3" / "ledgers").mkdir(parents=True)
    (tmp_path / "runtime" / "v3" / "ledgers" / "execution.jsonl").write_text('{"ts": 100}\n')
    (tmp_path / "runtime" / "live_execution_reports.jsonl").write_text('{"ts": 100}\n')
    (tmp_path / "runtime" / "config-7002.json").write_text("{}")
    (tmp_path / "runtime" / "bitfinex_live_switch_state.json").write_text("{}")
    got = ig.scan_local_oldest(str(tmp_path))
    assert set(got["files"]) == {"runtime/v3/ledgers/execution.jsonl", "runtime/live_execution_reports.jsonl"}
    assert set(got["kept"]) == {"runtime/config-7002.json", "runtime/bitfinex_live_switch_state.json"}
    assert got["kept_dirs"] == []


def test_stamp_receipt_counts_everything_old_as_unkept():
    rec = km.stamp_receipt({}, [("fly-mirror-segments/tree/live_drills.jsonl.zst", 1.0),
                               ("fly-segments/v3/seg/1.tar.gz.zst", 1.0), ("x", 99.0)], 10.0)
    assert rec == {"keep_manifest_sha256": km.MANIFEST_SHA256, "r2_objects_older_than_epoch_unkept": 2,
                   "r2_objects_older_than_epoch_kept": 0}


def test_g16_red_on_keep_manifest_mismatch_or_unverified():
    w = {"fly_files_older_than_epoch": 0, "r2_objects_older_than_epoch": 0,
         "r2_independent_older_than_epoch": 0, "box_files_older_than_epoch": 0, "fly_scan_complete": True,
         "receipt_keep_manifest_sha256": km.MANIFEST_SHA256, "r2_independent_keep_manifest_sha256": km.MANIFEST_SHA256}
    g = lambda: {r["id"]: r for r in ig.evaluate({"epoch_ts": 1.0, "wipe": w})["rows"]}["G16"]["status"]
    assert g() == "GREEN"
    w["receipt_keep_manifest_sha256"] = "old"
    assert g() == "RED"
    w["receipt_keep_manifest_sha256"] = km.MANIFEST_SHA256
    w["r2_independent_error"] = "KEEP_MANIFEST_UNVERIFIED"
    assert g() == "RED"
