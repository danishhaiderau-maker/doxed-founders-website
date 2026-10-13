import os
import time

import integrity_gaps as ig
import keep_manifest as km

PINNED_LITERAL = "bf133474a20ba10978d27a49806dcf60f7a095dbc31d3507fd2803d898a4277e"


def test_manifest_matches_danish_1322_decision():
    assert "13:22" in km.DECISION and "KEEP" in km.DECISION
    assert "runtime/v3/**" not in km.FLY and "keep-*.tgz" in km.FLY
    assert {"SignalCycle", "SignalCycleEvent", "SignalCycleParticipant", "SignalCycleParticipantReduction",
            "RelayPositionReductionAudit"} == set(km.NEON_TABLES)
    for f in ("live_execution_reports.jsonl", "live_copy_approvals.jsonl", "lane_pnl_ledger.json",
              "execution_settings_history.jsonl", "bitfinex_order_action_audit.jsonl",
              "bitfinex_live_state.json", "bitfinex_live_switch_state.json", "relay_lifecycle_evidence_v1.json",
              "relay_status_snapshot_v1.json", "relay_outbox_quarantine.jsonl", "relay_outbox_retired.jsonl",
              "live_copy_account_transitions.json", "live_copy_resolved_alerts.json", "ledger_reconciliation.json",
              "shadow_vs_live_entry.jsonl", "live_drills.jsonl", "mandatory-reserve.alarm-journal.json",
              "copy_only_exchange_fills_report.json", "bitfinex_copy_fidelity_lifecycles_report.json",
              "order_intent.jsonl"):
        assert km.is_kept(f"runtime/{f}"), f


def test_manifest_hash_is_pinned():
    assert km.MANIFEST_SHA256 == PINNED_LITERAL


def test_scan_reports_keep_separately_and_checks_tgz(tmp_path):
    old = time.time() - 30 * 86400
    (tmp_path / "runtime" / "v3" / "lifecycle_worker").mkdir(parents=True)
    (tmp_path / "runtime" / "v3" / "lifecycle_worker" / "s.json").write_text("{}")
    (tmp_path / "runtime" / "live_execution_reports.jsonl").write_text('{"ts": 100}\n')
    (tmp_path / "keep-20261013T002513.tgz").write_bytes(b"x")
    (tmp_path / "old-backup.tgz").write_bytes(b"x")
    (tmp_path / "runtime" / "book_tape_1s.jsonl").write_text('{"ts": 100}\n')
    for p in (tmp_path / "keep-20261013T002513.tgz", tmp_path / "old-backup.tgz"):
        os.utime(p, (old, old))
    got = ig.scan_local_oldest(str(tmp_path))
    assert set(got["files"]) == {"old-backup.tgz", "runtime/book_tape_1s.jsonl"}       # unlisted old -> offenders
    assert set(got["kept"]) == {"keep-20261013T002513.tgz", "runtime/live_execution_reports.jsonl"}
    assert got["kept_dirs"] == ["runtime/v3/lifecycle_worker"]


def test_v3_narrowed_to_live_execution_lifecycle():
    for rel in ("runtime/v3/ledgers/execution.jsonl", "runtime/v3/ledgers/order_intent.jsonl.1",
                "runtime/v3/ledgers/lifecycle.jsonl.3", "runtime/v3/lifecycle_bundle_index/lifecycle_index.sqlite3"):
        assert km.is_kept(rel), rel
    for rel in ("runtime/v3/ledgers/opportunity.jsonl", "runtime/v3/ledgers/decision.jsonl",
                "runtime/v3/ledgers/market_segment.jsonl", "runtime/v3/qualification_horizon_index.sqlite3",
                "runtime/v3/market_segments/a.json", "runtime/v3/signal_snapshots_v1/b.json"):
        assert not km.is_kept(rel), rel


def test_live_state_candidates_and_paper_portfolio_excluded():
    assert km.is_kept("runtime/orders.json", content={"venue": "bitfinex", "exchange_order_id": 1})
    assert not km.is_kept("runtime/positions.json", content={"type": "PaperPortfolio", "venue": "bitfinex"})
    assert not km.is_kept("runtime/orders.json")


def test_r2_keep_same_rule():
    assert km.is_r2_kept("fly-mirror-segments/tree/v3/ledgers/order_intent.jsonl.zst")
    assert km.is_r2_kept("fly-mirror-segments/tree/live_execution_reports.jsonl.1.zst")
    assert km.is_r2_kept("keep/keep-20261013.tgz")
    assert not km.is_r2_kept("fly-mirror-segments/tree/venue_bbo_1m.jsonl.zst")
    assert not km.is_r2_kept("fly-segments/v3/seg/000000000990.tar.gz.zst")


def test_g16_red_on_keep_manifest_mismatch():
    w = {"fly_files_older_than_epoch": 0, "r2_objects_older_than_epoch": 0,
         "r2_independent_older_than_epoch": 0, "box_files_older_than_epoch": 0, "fly_scan_complete": True,
         "receipt_keep_manifest_sha256": km.MANIFEST_SHA256, "r2_independent_keep_manifest_sha256": km.MANIFEST_SHA256}
    g = {r["id"]: r for r in ig.evaluate({"epoch_ts": 1.0, "wipe": w})["rows"]}["G16"]
    assert g["status"] == "GREEN"
    w["receipt_keep_manifest_sha256"] = "old"
    g = {r["id"]: r for r in ig.evaluate({"epoch_ts": 1.0, "wipe": w})["rows"]}["G16"]
    assert g["status"] == "RED"
