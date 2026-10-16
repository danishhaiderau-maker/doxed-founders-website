import os
import time

import pytest

import keep_manifest as km
# FLAG (#678 additive commit): integrity_gaps.py lives only in #670. Tests that need it skip here and run
# unchanged once #670 is on master (verified green on d1518674 + this delta: 22 passed).

PINNED_LITERAL = "7846d4af8daf50129321c53fb3a2e903b579b1131ab4ae1db9860060a0bbcc41"


def test_manifest_is_minimal_config_per_danish_1342():
    assert "13:42" in km.DECISION
    assert km.NEON_TABLES == () and km.BOX == ()
    assert km.R2_KEEP_PREFIXES == ("epoch-archive/",)   # HM #678 B2: the one dedicated archive prefix
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


def test_only_the_dedicated_archive_prefix_is_kept_in_r2():
    for k in ("fly-mirror-segments/tree/live_execution_reports.jsonl.zst", "keep/keep.tgz",
              "fly-segments/v3/seg/1.tar.gz.zst", "analysis-archive/epoch-archive/x.zst",
              "bot-data-retention/epoch-archive/x.zst", "fly-mirror-segments/epoch-archive/x.zst",
              "xepoch-archive/x", "_quarantine/epoch-archive/x"):
        assert not km.is_r2_kept(k), k
    assert km.is_r2_kept("epoch-archive/2026-10-16/receipt.json.zst")
    # the prefix is top-level: it is not nested under any other pinned root
    for root in ("analysis-archive/", "bot-data-", "fly-mirror-segments/"):
        assert not "epoch-archive/".startswith(root)


def test_scan_reports_keep_separately(tmp_path):
    ig = pytest.importorskip("integrity_gaps")
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
    ig = pytest.importorskip("integrity_gaps")
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
