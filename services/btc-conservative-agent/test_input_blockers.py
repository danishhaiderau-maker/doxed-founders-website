import csv
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY
from research import input_blockers as ib
from research.exit_ladder_paper_cohort import (
    build_paper_exit_ladder_cohort,
    filter_for_exit_ladder,
    load_replay_index,
)

EPOCH = "epoch-v22-test"
LANE = ACTIVE_TILE_ORDER[0]
PREFIX = ACTIVE_TILE_REGISTRY[LANE]["id_prefix"]


def _trade(trade_id, lane=LANE, epoch=EPOCH, signature="paper-policy-x", version="ENTRY|EXIT"):
    return {
        "trade_id": trade_id, "research_lane": lane, "epoch_id": epoch,
        "policy_signature": signature, "cfg_policy_version": version,
        "close_ts": "2026-10-02T09:00:00+00:00", "net_pnl_usd": 0.01,
    }


def _replay(trade_id, complete=True, provenance="SHOWCASE_STRATEGY_EXIT", ticks=3):
    return {
        "schema": "signal_replay_v4", "trade_id": trade_id, "lane": "executed",
        "replay_complete": complete, "terminal_provenance": provenance,
        "ticks": [{"t": i, "p": 100 + i} for i in range(ticks)],
    }


class PaperExitLadderCohortTest(unittest.TestCase):
    def test_paper_cohort_is_relay_independent_and_quarantines_retired_tiles(self):
        rows = [
            _trade(f"{PREFIX}-ok"),
            _trade(f"{PREFIX}-alias"),
            _trade(f"{PREFIX}-incomplete"),
            _trade(f"{PREFIX}-missing"),
            _trade(f"{PREFIX}-old", epoch="epoch-v21"),
            _trade(f"{PREFIX}-manual"),
            _trade(f"{PREFIX}-noid", signature=""),
            _trade("fc3-retired", lane="FAMILY_CHANDELIER_3"),
            _trade("fc3-spoof", lane=LANE),
        ]
        replays = {
            f"{PREFIX}-ok": _replay(f"{PREFIX}-ok"),
            f"rev-{PREFIX}-alias": _replay(f"rev-{PREFIX}-alias"),
            f"{PREFIX}-incomplete": _replay(f"{PREFIX}-incomplete", complete=False),
            f"{PREFIX}-old": _replay(f"{PREFIX}-old"),
            f"{PREFIX}-manual": _replay(f"{PREFIX}-manual", provenance="MANUAL_CLOSE"),
            f"{PREFIX}-noid": _replay(f"{PREFIX}-noid"),
            "fc3-retired": _replay("fc3-retired"),
            "fc3-spoof": _replay("fc3-spoof"),
        }
        receipt = build_paper_exit_ladder_cohort(rows, replays, epoch_id=EPOCH)
        self.assertEqual(receipt["eligible_trade_ids"], [f"{PREFIX}-alias", f"{PREFIX}-ok"])
        self.assertEqual(receipt["exclusion_reason_counts"], {
            "EXCLUDED_TERMINAL_PROVENANCE": 1,
            "NON_REGISTRY_LANE": 2,
            "OTHER_OR_MISSING_EPOCH": 1,
            "POLICY_IDENTITY_MISSING": 1,
            "REPLAY_INCOMPLETE": 1,
            "REPLAY_MISSING": 1,
        })
        self.assertEqual(receipt["current_registry_trades"], 7)
        self.assertNotIn("BITFINEX_LINKAGE_MISSING", receipt["exclusion_reason_counts"])

    def test_unknown_epoch_fails_closed(self):
        receipt = build_paper_exit_ladder_cohort(
            [_trade(f"{PREFIX}-ok")], {f"{PREFIX}-ok": _replay(f"{PREFIX}-ok")}, epoch_id=None,
        )
        self.assertEqual(receipt["eligible_count"], 0)
        self.assertEqual(receipt["exclusion_reason_counts"], {"CURRENT_EPOCH_UNKNOWN": 1})

    def test_filter_for_exit_ladder_restricts_trades_and_replays(self):
        frame = pd.DataFrame([_trade(f"{PREFIX}-ok"), _trade("fc3-retired", lane="FAMILY_CHANDELIER_3")])
        replays = {f"{PREFIX}-ok": _replay(f"{PREFIX}-ok"), "fc3-retired": _replay("fc3-retired"),
                   "scan-1": _replay("scan-1")}
        trades, kept, receipt = filter_for_exit_ladder(frame, replays, epoch_id=EPOCH)
        self.assertEqual(list(trades["trade_id"]), [f"{PREFIX}-ok"])
        self.assertEqual(list(kept), [f"{PREFIX}-ok"])
        self.assertEqual(receipt["schema"], "paper_exit_ladder_cohort_v1")

    def test_replay_index_promotes_only_explicit_completion_across_rotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "signal_replay.jsonl.1").write_text(
                json.dumps(_replay("a", complete=True, ticks=5)) + "\n", encoding="utf-8")
            (data / "signal_replay.jsonl").write_text(
                json.dumps({**_replay("a", complete=None, ticks=0)}) + "\n"
                + json.dumps(_replay("b", complete="yes")) + "\nnot-json\n", encoding="utf-8")
            index = load_replay_index(data)
        self.assertTrue(index["a"]["replay_complete"])
        self.assertEqual(index["a"]["tick_count"], 5)
        self.assertFalse(index["b"]["replay_complete"])


def _write_fixture(report: Path, data: Path, *, accumulator_epoch=EPOCH):
    with open(data / ib.TRADES_CSV, "w", encoding="utf-8", newline="") as handle:
        rows = [_trade(f"{PREFIX}-ok"), _trade(f"{PREFIX}-pending"), _trade("fc3-retired", lane="FAMILY_CHANDELIER_3")]
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (data / "signal_replay.jsonl").write_text(
        json.dumps(_replay(f"{PREFIX}-ok")) + "\n"
        + json.dumps(_replay(f"{PREFIX}-pending", complete=False)) + "\n", encoding="utf-8")
    (data / "research_session.json").write_text(json.dumps({
        "bot_start_time": 1789983916.5, "collector_v22_epoch_ts": 1790624095.1,
        "collector_v22_epoch_id": EPOCH,
    }), encoding="utf-8")
    ledgers = data / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    (ledgers / "opportunity.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"shared_ai_call_id": "scan-a", "epoch_id": EPOCH, "opportunity_id": "opportunity:e1",
         "record_id": "opportunity:e1", "signal_ts": 100.0},
        {"shared_ai_call_id": "scan-dup", "epoch_id": EPOCH, "opportunity_id": "opportunity:e2",
         "record_id": "opportunity:e2", "signal_ts": 200.0},
        {"shared_ai_call_id": "scan-dup", "epoch_id": EPOCH, "opportunity_id": "opportunity:e3",
         "record_id": "opportunity:e3", "signal_ts": 200.0},
    ]) + "\n", encoding="utf-8")
    (data / ib.COUNTERFACTUAL_FILE).write_text("\n".join(json.dumps(row) for row in [
        {"trade_id": "scan-a", "epoch_id": None, "opportunity_id": None},
        {"trade_id": "scan-dup", "epoch_id": None},
        {"trade_id": "scan-unknown"},
    ]) + "\n", encoding="utf-8")
    (report / ib.EXIT_LADDER_REPORT).write_text(json.dumps({
        "schema": "exit_ladder_simulator_v3", "data_status": "NO_ELIGIBLE_REPLAYS",
        "raw_replays_available": 2, "eligible_replays_available": 0, "replays_matched_executed": 0,
    }), encoding="utf-8")
    (report / ib.CROSS_WORLD_REPORT).write_text(json.dumps({
        "join_contract": {"required_explicit_identities": ["epoch_id", "fill_id"]},
        "join_summary": {"status": "NOT_COMPUTABLE", "pairwise_computable_comparisons": 0},
        "worlds": {
            "OBSERVED_PAPER": {"status": "COMPUTABLE", "unique_joinable_rows": 2},
            "CONSERVATIVE_BBO_DEPTH": {"status": "NOT_COMPUTABLE", "missing_identity_counts": {"tape_id": 3}},
            "BITFINEX_COPY": {"status": "NO_EVIDENCE"},
        },
    }), encoding="utf-8")
    (report / ib.DATA_HEALTH_REPORT).write_text(json.dumps({
        "signal_replay": {"distinct_trades": 511, "distinct_complete": 114, "distinct_complete_pct": 22.31,
                          "row_reasons": {"CENSORED_PROCESS_SHUTDOWN": 47}},
        "repaired_streams": {"counterfactual": {"rows": 3, "with_epoch_id_pct": 0.0}},
    }), encoding="utf-8")
    (report / ib.PROTECTION_REPLAY_WINDOW_FILE).write_text(json.dumps({
        "schema": "protection_replay_window_summary_v1", "alert_level": "RED",
        "reason": "PROTECTION_REPLAY_COVERAGE_BELOW_HALF", "events_eligible": 2415,
        "events_selected": 150, "max_events": 150,
    }), encoding="utf-8")
    status = report / "acc_status.json"
    status.write_text(json.dumps({
        "epoch_id": accumulator_epoch, "epoch_start_iso": "2026-09-28T19:34:55+00:00", "total_trades": 2,
    }), encoding="utf-8")
    return status


class InputBlockersTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.report = Path(self._tmp.name) / "report"
        self.data = Path(self._tmp.name) / "data"
        self.report.mkdir()
        self.data.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_exit_ladder_blocked_by_real_copy_gate_reports_paper_cohort(self):
        _write_fixture(self.report, self.data)
        item = ib.exit_ladder_item(self.report, self.data, EPOCH)
        self.assertEqual(item["status"], "BLOCKED")
        self.assertEqual(item["reason_code"], "REAL_COPY_COHORT_GATE_WHILE_RELAY_DISARMED")
        self.assertEqual(item["evidence"]["paper_cohort"]["eligible_count"], 1)
        self.assertEqual(item["total"], 2)

    def test_exit_ladder_with_paper_cohort_receipt_is_degraded_on_partial_coverage(self):
        _write_fixture(self.report, self.data)
        report = json.loads((self.report / ib.EXIT_LADDER_REPORT).read_text(encoding="utf-8"))
        report.update({
            "replays_matched_executed": 1,
            "cohort_receipt": {"schema": "paper_exit_ladder_cohort_v1", "eligible_count": 1,
                               "current_registry_trades": 2, "eligible_trade_ids": [f"{PREFIX}-ok"]},
        })
        (self.report / ib.EXIT_LADDER_REPORT).write_text(json.dumps(report), encoding="utf-8")
        item = ib.exit_ladder_item(self.report, self.data, EPOCH)
        self.assertEqual((item["status"], item["reason_code"]), ("DEGRADED", "PARTIAL_REPLAY_COVERAGE"))

    def test_counterfactual_keys_resolve_only_from_unique_exact_ai_call(self):
        rows, receipt = ib.resolve_counterfactual_join_keys(
            [{"trade_id": "scan-a", "epoch_id": "keep-me"}, {"trade_id": "scan-dup"}, {"trade_id": "x-1"}],
            [
                {"shared_ai_call_id": "scan-a", "epoch_id": EPOCH, "opportunity_id": "o1", "signal_ts": 1.0},
                {"shared_ai_call_id": "scan-dup", "epoch_id": EPOCH, "opportunity_id": "o2"},
                {"shared_ai_call_id": "scan-dup", "epoch_id": EPOCH, "opportunity_id": "o3"},
                {"shared_ai_call_id": "x-1", "epoch_id": EPOCH, "opportunity_id": "o4"},
            ],
        )
        self.assertEqual(rows[0]["epoch_id"], "keep-me")
        self.assertEqual((rows[0]["opportunity_id"], rows[0]["signal_ts"]), ("o1", 1.0))
        self.assertNotIn("opportunity_id", rows[1])
        self.assertNotIn("opportunity_id", rows[2])
        self.assertEqual(receipt["resolution"], {
            "RESOLVED_EXACT_AI_CALL_ID": 1,
            "UNRESOLVED_AMBIGUOUS_AI_CALL": 1,
            "UNRESOLVED_NO_MATCHING_AI_CALL": 1,
        })

    def test_stale_accumulator_epoch_is_blocked(self):
        status = _write_fixture(self.report, self.data, accumulator_epoch="epoch-v21-old")
        item = ib.trade_accumulator_item(self.data, EPOCH, status_path=status)
        self.assertEqual((item["status"], item["reason_code"]), ("BLOCKED", "ACCUMULATOR_EPOCH_STALE"))
        status = _write_fixture(self.report, Path(tempfile.mkdtemp(dir=self._tmp.name)))
        self.assertEqual(ib.trade_accumulator_item(self.data, EPOCH, status_path=status)["status"], "OK")

    def test_collect_input_blockers_document(self):
        status = _write_fixture(self.report, self.data)
        original = ib.trade_accumulator_item
        ib.trade_accumulator_item = lambda data_dir, epoch_id: original(data_dir, epoch_id, status_path=status)
        try:
            document = ib.collect_input_blockers(self.report, self.data, write=True)
        finally:
            ib.trade_accumulator_item = original
        self.assertEqual(document["schema"], "analyzer_input_blockers_v1")
        self.assertEqual(document["epoch_id"], EPOCH)
        self.assertEqual(document["level"], "RED")
        by_input = {item["input"]: item for item in document["items"]}
        self.assertEqual(set(by_input), {
            "protection_replay_window", "exit_ladder_simulator", "cross_world_evidence",
            "trade_accumulator", "signal_replay", "counterfactual_stream",
        })
        self.assertEqual(by_input["protection_replay_window"]["status"], "DEGRADED")
        self.assertEqual(by_input["cross_world_evidence"]["reason_code"], "FEWER_THAN_TWO_COMPUTABLE_WORLDS")
        self.assertEqual(by_input["signal_replay"]["status"], "DEGRADED")
        self.assertEqual(by_input["signal_replay"]["evidence"]["current_registry_executed"]["trades"], 2)
        self.assertEqual(by_input["counterfactual_stream"]["eligible"], 1)
        for item in document["items"]:
            self.assertIn(item["status"], {"OK", "DEGRADED", "BLOCKED"})
            for key in ("input", "reason_code", "reason", "eligible", "total", "evidence"):
                self.assertIn(key, item)
        persisted = json.loads((self.report / ib.REPORT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(persisted["level"], "RED")

    def test_protection_window_falls_back_to_report_receipt(self):
        (self.report / "safe_policy_genome_v3_report.json").write_text(json.dumps({
            "candidate_screen": {"input_window": {
                "schema": "protection_replay_event_window_v1", "events_eligible": 10,
                "events_selected": 10, "truncated": False, "max_events": 150,
            }},
        }), encoding="utf-8")
        item = ib.protection_replay_item(self.report)
        self.assertEqual(item["status"], "OK")
        self.assertEqual(item["evidence"]["alert_level"], "GREEN")


if __name__ == "__main__":
    unittest.main()
