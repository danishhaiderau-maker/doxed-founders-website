import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from research_v3_bridge import dual_write_v22_record
from research_v3_bridge import dual_write_provisional_source
from research_v3_bridge import dual_write_lane_decision
from research_v3_bridge import dual_write_lane_entry_resolution
from research_v3_bridge import dual_write_paper_close, dual_write_paper_fill, dual_write_paper_order_intent, paper_policy_identity_for_sources
from research_v3_bridge import reconcile_overdue_expected_order_decisions
from research_v3_bridge import reconcile_terminal_v22_into_v3
from research_v3_store import V3EvidenceStore


def _event(event_id="cont-1", episode_id="episode-1"):
    return {
        "event_id": event_id,
        "event_episode_id": episode_id,
        "epoch_id": "epoch-v3-test",
        "envelope": {"signal_ts": 1000, "raw_direction": "LONG", "executed_direction": "LONG", "policy_signature": "policy-a", "policy_epoch_id": "pe-a"},
        "event_episode": {"shared_ai_call_id": "scan-1"},
        "feature_snapshot_at_signal": {"symbol": "tBTCF0:USTF0", "adx": 30},
        "pre_signal_context": {"1m": {"bars": 1}},
        "decision_tree_snapshot": {"AI": {"result": "PASS"}},
        "primary_outcome": "ACCEPTED_UNFILLED",
        "observation_status": "FUNNEL_COMPLETE",
        "ranking_eligible": True,
        "canonical_tape": {"path_1m": [{"t": 1000, "o": 100, "h": 101, "l": 99, "c": 100}], "canonical_tape_start": 1000, "canonical_tape_end": 1060, "coverage": {"complete": True}},
        "research_execution_basis": {"qty": 0.1},
        "research_chase_schedule": {"authoritative": True, "intervals": []},
        "entry_children": [{"id": 1}, {"id": 2}],
        "replay_eligibility": {"eligible": True},
    }


class V3BridgeTests(unittest.TestCase):
    def _lost_expected_order(self, tmp):
        dual_write_lane_decision(
            {
                "trade_id": "scan-lost", "shared_ai_call_id": "scan-lost",
                "shared_ai_call_ts_epoch": 1000, "raw_direction": "LONG",
                "research_lane": "CONTINUOUS",
            },
            lane="CONTINUOUS", policy_decision="ACCEPT",
            execution_disposition="ORDER_ELIGIBLE", exact_reason="APPROVE",
            epoch_id="epoch-v3-test", data_dir=tmp,
            lane_policy={"policy_id": "CONTINUOUS", "entry_ttl_sec": 60},
        )
        # Model the historical interruption: decision survived, pre-order
        # awaiting state and its lifecycle row did not.
        V3EvidenceStore(tmp, epoch_id="epoch-v3-test").ledger_path("lifecycle").unlink()

    def test_restart_ledger_reconciliation_heals_already_lost_expectation_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._lost_expected_order(tmp)
            first = reconcile_overdue_expected_order_decisions(
                epoch_id="epoch-v3-test", data_dir=tmp, observed_ts=2000,
                runtime_revision="repair-rev",
            )
            second = reconcile_overdue_expected_order_decisions(
                epoch_id="epoch-v3-test", data_dir=tmp, observed_ts=2001,
                runtime_revision="repair-rev",
            )
            self.assertEqual(first["reconciled"], 1)
            self.assertEqual(second["reconciled"], 0)
            row = json.loads(V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("lifecycle").read_text().strip())
            self.assertEqual(row["entry_resolution"], "NO_ORDER")
            self.assertEqual(row["policy_signature"], json.loads(
                V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
                .ledger_path("decision").read_text().strip()
            )["policy_signature"])
            self.assertEqual(
                row["restart_recovery_provenance"]["runtime_revision"],
                "repair-rev",
            )

    def test_restart_ledger_reconciliation_refuses_active_or_not_overdue_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._lost_expected_order(tmp)
            decision = json.loads(V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("decision").read_text().strip())
            active = reconcile_overdue_expected_order_decisions(
                epoch_id="epoch-v3-test", data_dir=tmp, observed_ts=2000,
                active_rows=[decision], runtime_revision="repair-rev",
            )
            early = reconcile_overdue_expected_order_decisions(
                epoch_id="epoch-v3-test", data_dir=tmp, observed_ts=1001,
                runtime_revision="repair-rev",
            )
            self.assertEqual(active["resolved_or_active"], 1)
            self.assertEqual(early["not_overdue"], 1)
            self.assertFalse(V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("lifecycle").exists())

    def test_lane_entry_receipts_are_append_only_and_no_order_has_no_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = {
                "trade_id": "scan-resolution", "shared_ai_call_id": "scan-resolution",
                "shared_ai_call_ts_epoch": 1000, "raw_direction": "LONG",
                "research_lane": "CONTINUOUS", "entry_ttl_sec": 600,
            }
            receipt = dual_write_lane_decision(
                source, lane="CONTINUOUS", policy_decision="ACCEPT",
                execution_disposition="ORDER_ELIGIBLE", exact_reason="APPROVE",
                epoch_id="epoch-v3-test", data_dir=tmp,
                lane_policy={"policy_id": "CONTINUOUS", "entry_ttl_sec": 600},
            )
            self.assertEqual(len(receipt["writes"]), 3)
            rows = [json.loads(line) for line in V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("lifecycle").read_text().splitlines()]
            self.assertEqual(rows[0]["entry_resolution"], "AWAITING")
            self.assertEqual(rows[0]["resolution_deadline_ts"], 1780)

            recovery_source = {**source, "_restart_recovery_provenance": {
                "schema": "paper_awaiting_restart_provenance_v1",
                "snapshot_git_rev": "abc123",
            }}
            dual_write_lane_entry_resolution(
                recovery_source, lane="CONTINUOUS", entry_resolution="NO_ORDER",
                exact_reason="MAX_ACTIVE_SIGNALS", epoch_id="epoch-v3-test",
                data_dir=tmp, lane_policy={"policy_id": "CONTINUOUS", "entry_ttl_sec": 600},
                observed_ts=1001,
            )
            rows = [json.loads(line) for line in V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("lifecycle").read_text().splitlines()]
            terminal = next(row for row in rows if row["entry_resolution"] == "NO_ORDER")
            self.assertTrue(terminal["terminal"])
            self.assertEqual(terminal["outcome_state"], "NO_TRADE")
            self.assertEqual(
                terminal["restart_recovery_provenance"]["snapshot_git_rev"],
                "abc123",
            )
            self.assertFalse(any(key in terminal for key in ("pnl", "pnl_usd", "net_usd")))

    def test_shared_call_episode_is_stable_across_symbol_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = dual_write_lane_decision(
                {
                    "trade_id": "scan-symbol-alias", "shared_ai_call_id": "scan-symbol-alias",
                    "shared_ai_call_ts_epoch": 1000, "symbol": "tBTCF0:USTF0",
                    "raw_direction": "LONG", "executed_direction": "LONG",
                },
                lane="OFFSET_029_ATR_TP_25", policy_decision="ACCEPT",
                execution_disposition="ORDER_ELIGIBLE", exact_reason="APPROVE",
                epoch_id="epoch-v3-test", data_dir=tmp,
                lane_policy={"policy_id": "PATIENT", "paper_only": True},
            )
            submit = dual_write_paper_order_intent(
                {
                    "trade_id": "patient-child", "created_ts": 1001,
                    "signal_dir": "LONG", "limit_price": 99.71, "qty": 0.1,
                    "research_lane": "OFFSET_029_ATR_TP_25", "paper_only": True,
                },
                {
                    "trade_id": "patient-child", "shared_ai_call_id": "scan-symbol-alias",
                    "created_ts_ts": 1000, "symbol": "BTCUSD", "raw_direction": "LONG",
                    "research_lane": "OFFSET_029_ATR_TP_25", "policy_id": "PATIENT",
                    "paper_only": True,
                },
                epoch_id="epoch-v3-test", data_dir=tmp,
            )
            self.assertEqual(decision["episode_id"], submit["episode_id"])
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["ledger_counts"]["opportunity"], 1)

    def test_shared_call_records_independent_lane_decisions_without_duplicate_opportunity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = {
                "trade_id": "scan-lanes-1", "shared_ai_call_id": "scan-lanes-1",
                "shared_ai_call_ts_epoch": 1000, "raw_direction": "LONG",
                "executed_direction": "LONG", "long_score": 62, "short_score": 38,
                "score_gap": 24, "feature_snapshot_at_signal": {"adx": 31},
            }
            continuous = dual_write_lane_decision(
                source, lane="CONTINUOUS", policy_decision="ACCEPT",
                execution_disposition="ORDER_ELIGIBLE", exact_reason="SCORE_TIER_APPROVE",
                epoch_id="epoch-v3-test", data_dir=tmp,
                lane_policy={"policy_id": "CONTINUOUS", "paper_only": True},
            )
            patient = dual_write_lane_decision(
                source, lane="OFFSET_029_ATR_TP_25", policy_decision="REJECT",
                execution_disposition="AI_REJECTED_NO_ORDER", exact_reason="AI_REJECT",
                epoch_id="epoch-v3-test", data_dir=tmp,
                lane_policy={
                    "policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
                    "entry_offset_pct": 0.29, "paper_only": True,
                },
            )
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            decisions = [json.loads(line) for line in store.ledger_path("decision").read_text().splitlines()]
            self.assertEqual(continuous["episode_id"], patient["episode_id"])
            self.assertEqual(store.verify()["ledger_counts"]["opportunity"], 1)
            self.assertEqual(len(decisions), 2)
            self.assertEqual({row["research_lane"] for row in decisions}, {
                "CONTINUOUS", "OFFSET_029_ATR_TP_25",
            })
            self.assertEqual(len({row["policy_signature"] for row in decisions}), 2)
            patient_row = next(row for row in decisions if row["research_lane"] == "OFFSET_029_ATR_TP_25")
            self.assertEqual(patient_row["outcome_state"], "REJECTED")
            self.assertFalse(patient_row["order_intent_expected"])
            self.assertEqual(store.verify()["ledger_counts"]["order_intent"], 0)

    def test_rejected_shared_lanes_reuse_one_pre_signal_market_context_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp) / "market_microstructure_1s.jsonl"
            tape.write_text("\n".join([
                json.dumps({"bucket_ts": 998, "last": 100.0, "best_bid": 99.9, "best_ask": 100.1}),
                json.dumps({"bucket_ts": 1000, "last": 100.2, "best_bid": 100.1, "best_ask": 100.3}),
            ]) + "\n", encoding="utf-8")
            source = {
                "trade_id": "scan-context", "shared_ai_call_id": "scan-context",
                "shared_ai_call_ts_epoch": 1000, "symbol": "tBTCF0:USTF0",
                "raw_direction": "NO_TRADE", "executed_direction": "NO_TRADE",
                "feature_snapshot_at_signal": {"adx": 14, "regime": "RANGE"},
            }
            receipts = [
                dual_write_lane_decision(
                    source, lane=lane, policy_decision="REJECT",
                    execution_disposition="AI_REJECTED_NO_ORDER",
                    exact_reason="NO_TRADE", epoch_id="epoch-v3-test", data_dir=tmp,
                    lane_policy={"policy_id": lane, "paper_only": True},
                )
                for lane in ("CONTINUOUS", "FAMILY_ATR_TRAIL")
            ]
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            verification = store.verify()
            self.assertEqual(verification["ledger_counts"]["opportunity"], 1)
            self.assertEqual(verification["ledger_counts"]["decision"], 2)
            self.assertEqual(verification["ledger_counts"]["market_segment"], 1)
            decisions = [
                json.loads(line)
                for line in store.ledger_path("decision").read_text().splitlines()
            ]
            refs = [row["market_context_segment_refs"][0] for row in decisions]
            self.assertEqual(refs[0]["sha256"], refs[1]["sha256"])
            self.assertEqual(refs[0]["source"], "LIVE_MICROSTRUCTURE_1S_PRE_SIGNAL")
            self.assertTrue(all(
                row["market_context_segment_coverage"]["future_exit_path_included"] is False
                for row in decisions
            ))
            lifecycles = [
                json.loads(line)
                for line in store.ledger_path("lifecycle").read_text().splitlines()
            ]
            self.assertTrue(all(row["market_context_segment_refs"] for row in lifecycles))
            context_row = json.loads(
                store.ledger_path("market_segment").read_text().strip()
            )
            self.assertEqual(context_row["event_id"], "market-context:episode-" + hashlib.sha256(b"shared:scan-context").hexdigest()[:20])
            self.assertEqual(context_row["tape_id"], f"tape:{refs[0]['sha256']}")
            self.assertEqual(len(receipts[0]["writes"]), 4)
            self.assertTrue(receipts[1]["writes"][1]["duplicate"])

    def test_accepted_but_disabled_lane_is_no_trade_not_zero_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dual_write_lane_decision(
                {
                    "trade_id": "scan-disabled", "shared_ai_call_id": "scan-disabled",
                    "shared_ai_call_ts_epoch": 1000, "raw_direction": "SHORT",
                    "executed_direction": "SHORT", "raw_ai_decision": "APPROVE",
                },
                lane="OFFSET_029_ATR_TP_25", policy_decision="ACCEPT",
                execution_disposition="LANE_DISABLED_NO_ORDER",
                exact_reason="PAPER_LANE_TOGGLE_OFF_NO_SHADOW",
                epoch_id="epoch-v3-test", data_dir=tmp,
                lane_policy={"policy_id": "PATIENT", "paper_only": True},
            )
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            row = json.loads(store.ledger_path("decision").read_text().strip())
            self.assertTrue(receipt["store_verification"]["passed"])
            self.assertEqual(row["policy_decision"], "ACCEPT")
            self.assertEqual(row["execution_disposition"], "LANE_DISABLED_NO_ORDER")
            self.assertEqual(row["outcome_state"], "NO_TRADE")
            self.assertNotEqual(row["outcome_state"], "REALIZED_ZERO_PNL")

    def test_lane_decision_retry_is_write_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = {
                "source": {
                    "trade_id": "scan-retry", "shared_ai_call_id": "scan-retry",
                    "shared_ai_call_ts_epoch": 1000, "raw_direction": "LONG",
                },
                "lane": "CONTINUOUS", "policy_decision": "REJECT",
                "execution_disposition": "POLICY_REJECTED_NO_ORDER",
                "exact_reason": "REJECT", "epoch_id": "epoch-v3-test", "data_dir": tmp,
                "lane_policy": {"policy_id": "CONTINUOUS"},
            }
            dual_write_lane_decision(**args)
            retry = dual_write_lane_decision(**args)
            self.assertTrue(all(write["duplicate"] for write in retry["writes"]))
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["ledger_counts"]["decision"], 1)

    def test_lane_decision_and_later_order_share_exact_policy_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {
                "trade_id": "patient-child", "shared_ai_call_id": "scan-policy-match",
                "created_ts_ts": 1000, "raw_direction": "LONG", "final_direction": "LONG",
                "research_lane": "OFFSET_029_ATR_TP_25",
                "policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
                "raw_policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
                "entry_limit_policy": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
                "deterministic_entry_offset_pct": 0.0029,
                "exit_config": {"policy": "ATR_TP_2.5"},
                "paper_only": True, "relay_eligible": False,
            }
            dual_write_lane_decision(
                {**signal, "trade_id": "scan-policy-match"},
                lane="OFFSET_029_ATR_TP_25", policy_decision="ACCEPT",
                execution_disposition="ORDER_ELIGIBLE", exact_reason="APPROVE",
                epoch_id="epoch-v3-test", data_dir=tmp, lane_policy=signal,
            )
            dual_write_paper_order_intent(
                {
                    "trade_id": "patient-child", "created_ts": 1001,
                    "signal_dir": "LONG", "limit_price": 99.71, "qty": 0.1,
                    "research_lane": "OFFSET_029_ATR_TP_25", "paper_only": True,
                    "relay_eligible": False,
                },
                signal, epoch_id="epoch-v3-test", data_dir=tmp,
            )
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            decision = json.loads(store.ledger_path("decision").read_text().strip())
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            self.assertEqual(decision["policy_signature"], intent["policy_signature"])
            self.assertEqual(decision["policy_epoch_id"], intent["policy_epoch_id"])
            self.assertEqual(decision["episode_id"], intent["episode_id"])
            lifecycle = [
                json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()
            ]
            submitted = next(row for row in lifecycle if row.get("entry_resolution") == "ORDER_SUBMITTED")
            self.assertTrue(submitted["entry_resolution_terminal"])
            self.assertFalse(submitted["terminal"])
            self.assertEqual(submitted["policy_signature"], decision["policy_signature"])

    def test_frozen_policy_spec_repairs_mismatched_base_signature_on_terminal_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = {
                "trade_id": "patient-child", "shared_ai_call_id": "scan-terminal",
                "created_ts_ts": 1000, "raw_direction": "LONG",
                "research_lane": "OFFSET_029_ATR_PROTECTED",
                "policy_identity_schema": "paper_policy_identity_v3",
                "policy_id": "PATIENT_PROTECTED",
                "policy_signature": "policy-base-control",
                "policy_epoch_id": "policy-epoch-base-control",
                "paper_policy_spec": {
                    "schema": "paper_policy_identity_spec_v3",
                    "policy_id": "PATIENT_PROTECTED",
                    "research_lane": "OFFSET_029_ATR_PROTECTED",
                    "entry_limit_policy": "OFFSET_0.29_PROTECTED",
                    "entry_offset_fraction": 0.0029,
                    "declared_entry_ttl_sec": 1800.0,
                    "entry_reconciliation_allowance_sec": 180,
                    "exit_config": {"policy": "PROTECTED"},
                    "paper_only": True,
                    "relay_eligible": False,
                    "base_policy_signature": "policy-base-control",
                },
            }
            frozen = paper_policy_identity_for_sources("epoch-v3-test", source)
            self.assertTrue(frozen["policy_signature"].startswith("paper-policy-"))
            self.assertNotEqual(frozen["policy_signature"], source["policy_signature"])

            dual_write_lane_entry_resolution(
                source, lane="OFFSET_029_ATR_PROTECTED",
                entry_resolution="NO_ORDER", exact_reason="TTL_EXPIRED",
                epoch_id="epoch-v3-test", data_dir=tmp, observed_ts=1001,
            )
            row = json.loads(V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("lifecycle").read_text().strip())
            self.assertEqual(row["policy_signature"], frozen["policy_signature"])
            self.assertEqual(row["policy_epoch_id"], frozen["policy_epoch_id"])
            self.assertEqual(row["policy_id"], "PATIENT_PROTECTED")

    def test_continuous_relay_capability_default_keeps_decision_and_order_identity_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = {
                "trade_id": "scan-continuous", "shared_ai_call_id": "scan-continuous",
                "created_ts_ts": 1000, "raw_direction": "LONG", "final_direction": "LONG",
                "research_lane": "CONTINUOUS", "policy_id": "CONTINUOUS",
                "entry_limit_policy": "deterministic_0.1pct_offset_v1",
                "deterministic_entry_offset_pct": 0.001,
                "exit_config": {"policy": "SCENARIO_C"}, "paper_only": False,
            }
            dual_write_lane_decision(
                source, lane="CONTINUOUS", policy_decision="ACCEPT",
                execution_disposition="ORDER_ELIGIBLE", exact_reason="APPROVE",
                epoch_id="epoch-v3-test", data_dir=tmp,
                lane_policy={**source, "relay_eligible": True, "paper_only": True},
            )
            dual_write_paper_order_intent(
                {"trade_id": "cont-child", "created_ts": 1001, "signal_dir": "LONG",
                 "limit_price": 99.9, "qty": 0.1, "research_lane": "CONTINUOUS"},
                {**source, "trade_id": "cont-child"},
                epoch_id="epoch-v3-test", data_dir=tmp,
            )
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            decision = json.loads(store.ledger_path("decision").read_text().strip())
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            self.assertEqual(decision["policy_signature"], intent["policy_signature"])
            self.assertEqual(decision["policy_epoch_id"], intent["policy_epoch_id"])
            self.assertEqual(intent["shared_ai_call_id"], "scan-continuous")
            self.assertTrue(decision["paper_policy_spec"]["relay_eligible"])
            self.assertTrue(intent["paper_policy_spec"]["relay_eligible"])
            self.assertTrue(intent["relay_eligible"])
            # Relay capability must never rewrite the signed source evidence
            # as non-paper.  The order source deliberately carries the old
            # capability-shaped false value above; identity normalization must
            # still keep decision and submit on one paper policy signature.
            self.assertTrue(decision["paper_policy_spec"]["paper_only"])
            self.assertTrue(intent["paper_policy_spec"]["paper_only"])
            self.assertEqual(
                decision["paper_policy_spec"], intent["paper_policy_spec"],
            )

    def test_terminal_record_uses_same_shared_call_episode_as_provisional_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event("scan-child", "legacy-stable-episode")
            event["event_episode"] = {}
            event["feature_snapshot_at_signal"]["source_features"] = {
                "shared_ai_call_id": "scan-parent", "raw_direction": "LONG"
            }
            provisional = dual_write_provisional_source("scan-child", {
                "created_ts_ts": 1000, "raw_direction": "LONG",
                "shared_ai_call_id": "scan-parent",
            }, epoch_id="epoch-v3-test", data_dir=tmp)

            terminal = dual_write_v22_record(event, data_dir=tmp)

            self.assertEqual(terminal["episode_id"], provisional["episode_id"])
            rows = [json.loads(line) for line in
                    (Path(tmp) / "v3" / "ledgers" / "opportunity.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["shared_ai_call_id"], "scan-parent")

    def test_production_compact_ohlcv_rows_are_normalized_without_losing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event("cont-compact")
            event["canonical_tape"]["path_1m"] = [
                [1787298900000.0, 76447.0, 76555.0, 76433.0, 76527.0, 0.46732612]
            ]
            receipt = dual_write_v22_record(event, data_dir=tmp)
            self.assertTrue(receipt["store_verification"]["passed"])
            files = list((Path(tmp) / "v3" / "market_segments").rglob("*.json"))
            self.assertEqual(len(files), 1)
            row = json.loads(files[0].read_text(encoding="utf-8"))["rows"][0]
            self.assertEqual(row, {"t": 1787298900000.0, "o": 76447.0, "h": 76555.0,
                                   "l": 76433.0, "c": 76527.0, "v": 0.46732612})

    def test_bot_wires_terminal_reconciliation_to_canonical_runtime_root(self):
        source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
        self.assertIn("v3_data_dir = os.getcwd()", source)
        self.assertIn("reconcile_terminal_v22_into_v3(", source)
        self.assertIn("data_dir=v3_data_dir", source)
        self.assertNotIn('os.path.join(DATA_DIR, "research_events_v22.jsonl")', source)

    def test_paper_submit_and_fill_are_visible_before_terminal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {
                "trade_id": "o29atr-1", "created_ts_ts": 1000,
                "raw_direction": "LONG", "final_direction": "LONG",
                "symbol": "tBTCF0:USTF0", "shared_ai_call_id": "scan-paper-1",
                "signal_price": 101.0, "research_lane": "OFFSET_029_ATR_TP_25",
                "paper_only": True, "relay_eligible": False,
                "policy_id": "OFFSET_029_ATR_TP_25", "policy_signature": "policy-paper",
                "policy_epoch_id": "policy-epoch-paper",
                "entry_limit_policy": "OFFSET_0.29_CHASE_w234_s25_i60",
                "entry_offset_fraction": 0.0029,
                "context": {"cycle_3m_universe": {"atr14_pct_3m": 0.081}},
            }
            order = {
                "trade_id": "o29atr-1", "created_ts": 1001, "signal_dir": "LONG",
                "signal_price": 101.0, "limit_price": 100.7071, "qty": 0.2,
                "research_lane": "OFFSET_029_ATR_TP_25", "paper_only": True,
                "relay_eligible": False, "chase_schedule_authoritative": True,
                "research_chase_schedule": {"authoritative": True, "intervals": [{"step": 0}]},
                "source_order_market_evidence": {"latest_observation": {
                    "verdict": "EXECUTABLE", "gate_policy": "BBO_DEPTH_V1",
                    "activation_ts": 1002, "generation": 2, "book_ts": 1009,
                    "book_age_sec": 1.0, "best_bid": 100.70, "best_ask": 100.71,
                    "side_correct_executable_quote": 100.71,
                    "visible_executable_qty": 4.0, "recent_aggressor_qty": 1.0,
                }},
                "fill_time_revalidation": {
                    "performed": True, "checked_ts": "1970-01-01T00:16:49Z",
                    "signal_age_sec": 9.0, "result": "PASSED", "reason": None,
                },
            }
            submit = dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            position = {"trade_id": "o29atr-1", "entry_ts": 1010, "entry": 100.7071, "qty": 0.2,
                        "fill_model": "LIMIT_TOUCH", "atr14_pct_3m": 0.083}
            fill = dual_write_paper_fill(order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertTrue(submit["store_verification"]["passed"])
            self.assertTrue(fill["store_verification"]["passed"])
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            execution = json.loads(store.ledger_path("execution").read_text().strip())
            self.assertEqual(submit["policy_signature"], intent["policy_signature"])
            self.assertEqual(fill["policy_signature"], execution["policy_signature"])
            self.assertEqual(intent["intent_kind"], "ACTUAL_PAPER_LIMIT_SUBMIT")
            self.assertEqual(intent["requested_qty"], 0.2)
            self.assertTrue(intent["chase_schedule_authoritative"])
            self.assertEqual(intent["atr14_pct_at_signal"], 0.081)
            self.assertEqual(intent["atr14_pct_basis"], "SIGNAL_TIME_3M_ATR14")
            self.assertEqual(intent["entry_children_count"], 1)
            self.assertEqual(intent["entry_children"][0]["offset_pct"], 0.29)
            self.assertTrue(intent["policy_signature"].startswith("paper-policy-"))
            self.assertTrue(intent["policy_epoch_id"].startswith("paper-policy-epoch-"))
            self.assertEqual(intent["base_policy_signature"], "policy-paper")
            self.assertEqual(intent["base_policy_epoch_id"], "policy-epoch-paper")
            self.assertEqual(execution["execution_world"], "SHOWCASE_PAPER_OBSERVED")
            self.assertFalse(execution["authenticated_exchange_actual"])
            self.assertEqual(execution["filled_qty"], 0.2)
            self.assertEqual(execution["execution_basis"], "CONSERVATIVE_BBO_DEPTH")
            self.assertTrue(execution["conservative_fill_supported"])
            self.assertEqual(execution["fill_gate_verdict"], "EXECUTABLE")
            self.assertEqual(execution["limit_generation"], 2)
            self.assertEqual(execution["remaining_qty"], 0.0)
            self.assertFalse(execution["partial_fill"])
            self.assertEqual(execution["atr14_pct_at_fill"], 0.083)
            self.assertEqual(execution["atr14_pct_basis"], "FILL_TIME_3M_ATR14")
            self.assertEqual(execution["fill_time_revalidation"]["result"], "PASSED")
            self.assertTrue(execution["fill_time_revalidation"]["performed"])
            self.assertEqual(intent["episode_id"], execution["episode_id"])
            self.assertEqual(intent["opportunity_id"], f"opportunity:{intent['episode_id']}")
            self.assertEqual(execution["opportunity_id"], intent["opportunity_id"])
            self.assertEqual(execution["schedule_id"], intent["schedule_id"])
            self.assertEqual(
                execution["fill_id"],
                "fill:epoch-v3-test:o29atr-1:paper-primary",
            )
            lifecycle_rows = [json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()]
            fill_lifecycle = next(row for row in lifecycle_rows if row.get("observation_status") == "PAPER_POSITION_OPEN")
            submit_lifecycle = next(row for row in lifecycle_rows if row.get("observation_status") == "PAPER_ORDER_SUBMITTED")
            self.assertEqual(submit_lifecycle["outcome_state"], "CENSORED")
            self.assertFalse(fill_lifecycle["terminal"])
            self.assertEqual(fill_lifecycle["ranking_blocker"], "EXIT_PATH_NOT_MATURED")
            self.assertEqual(store.verify()["market_segment_count"], 0)
            self.assertFalse(store.ledger_path("market_segment").exists())
            self.assertEqual(submit_lifecycle["effective_execution_mode"], "PAPER_OBSERVED")
            self.assertEqual(intent["effective_execution_mode"], "PAPER_OBSERVED")
            self.assertEqual(intent["policy_execution_scope"], "PAPER_RESEARCH_ONLY")
            self.assertEqual(intent["relay_capability"], "NOT_RELAY_ELIGIBLE")
            for row in (execution, fill_lifecycle):
                self.assertEqual(row["research_lane"], "OFFSET_029_ATR_TP_25")
                self.assertEqual(row["shared_ai_call_id"], "scan-paper-1")
                self.assertEqual(row["policy_id"], intent["policy_id"])
                self.assertEqual(row["policy_signature"], intent["policy_signature"])
                self.assertEqual(row["policy_epoch_id"], intent["policy_epoch_id"])

    def test_paper_submit_reads_direct_shared_signal_cycle_atr_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {
                "trade_id": "o29atr-direct-cycle", "created_ts_ts": 1000,
                "raw_direction": "LONG", "final_direction": "LONG",
                "symbol": "tBTCF0:USTF0", "shared_ai_call_id": "scan-direct-cycle",
                "signal_price": 101.0, "research_lane": "OFFSET_029_ATR_TP_25",
                "paper_only": True, "relay_eligible": False,
                "cycle_3m_universe": {"atr14_pct_3m": 0.079},
            }
            order = {
                "trade_id": "o29atr-direct-cycle", "created_ts": 1001,
                "signal_dir": "LONG", "signal_price": 101.0,
                "limit_price": 100.7071, "qty": 0.2,
                "research_lane": "OFFSET_029_ATR_TP_25",
                "paper_only": True, "relay_eligible": False,
            }

            receipt = dual_write_paper_order_intent(
                order, signal, epoch_id="epoch-v3-direct-cycle", data_dir=tmp,
            )

            self.assertTrue(receipt["store_verification"]["passed"])
            intent = json.loads(
                V3EvidenceStore(tmp, epoch_id="epoch-v3-direct-cycle")
                .ledger_path("order_intent")
                .read_text()
                .strip()
            )
            self.assertEqual(intent["atr14_pct_at_signal"], 0.079)
            self.assertEqual(intent["atr14_pct_basis"], "SIGNAL_TIME_3M_ATR14")

    def test_paper_submit_reads_frozen_ai_input_cycle_atr_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {
                "trade_id": "o29atr-ai-input-cycle", "created_ts_ts": 1000,
                "raw_direction": "LONG", "final_direction": "LONG",
                "symbol": "tBTCF0:USTF0", "shared_ai_call_id": "scan-ai-input-cycle",
                "signal_price": 101.0, "research_lane": "OFFSET_029_ATR_TP_25",
                "paper_only": True, "relay_eligible": False,
                "ai_input": {"cycle_3m_universe": {"atr14_pct_3m": 0.082}},
            }
            order = {
                "trade_id": "o29atr-ai-input-cycle", "created_ts": 1001,
                "signal_dir": "LONG", "signal_price": 101.0,
                "limit_price": 100.7071, "qty": 0.2,
                "research_lane": "OFFSET_029_ATR_TP_25",
                "paper_only": True, "relay_eligible": False,
            }

            dual_write_paper_order_intent(
                order, signal, epoch_id="epoch-v3-ai-input-cycle", data_dir=tmp,
            )
            intent = json.loads(
                V3EvidenceStore(tmp, epoch_id="epoch-v3-ai-input-cycle")
                .ledger_path("order_intent")
                .read_text()
                .strip()
            )
            self.assertEqual(intent["atr14_pct_at_signal"], 0.082)
            self.assertEqual(intent["atr14_pct_basis"], "SIGNAL_TIME_3M_ATR14")

    def test_different_paper_lanes_never_share_inherited_control_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = {
                "created_ts_ts": 1000, "raw_direction": "LONG",
                "final_direction": "LONG", "policy_signature": "policy-control",
                "policy_epoch_id": "policy-epoch-control",
            }
            continuous = {**base, "trade_id": "cont-1", "shared_ai_call_id": "scan-1",
                          "research_lane": "CONTINUOUS"}
            patient = {**base, "trade_id": "patient-1", "shared_ai_call_id": "scan-2",
                       "research_lane": "OFFSET_029_ATR_TP_25",
                       "policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
                       "paper_only": True, "relay_eligible": False}
            dual_write_paper_order_intent(
                {"trade_id": "cont-1", "created_ts": 1001, "signal_dir": "LONG",
                 "limit_price": 99.9, "qty": 0.1, "research_lane": "CONTINUOUS"},
                continuous, epoch_id="epoch-v3-test", data_dir=tmp)
            dual_write_paper_order_intent(
                {"trade_id": "patient-1", "created_ts": 1001, "signal_dir": "LONG",
                 "limit_price": 99.71, "qty": 0.1,
                 "research_lane": "OFFSET_029_ATR_TP_25", "paper_only": True,
                 "relay_eligible": False},
                patient, epoch_id="epoch-v3-test", data_dir=tmp)
            rows = [json.loads(line) for line in
                    V3EvidenceStore(tmp, epoch_id="epoch-v3-test").ledger_path("order_intent").read_text().splitlines()]
            self.assertEqual({row["base_policy_signature"] for row in rows}, {"policy-control"})
            self.assertEqual(len({row["policy_signature"] for row in rows}), 2)
            self.assertEqual(len({row["policy_epoch_id"] for row in rows}), 2)
            self.assertEqual({row["policy_id"] for row in rows}, {
                "CONTINUOUS", "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
            })

    def test_paper_lifecycle_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {"trade_id": "p-1", "created_ts_ts": 1000, "raw_direction": "SHORT", "final_direction": "SHORT", "shared_ai_call_id": "scan-1"}
            order = {"trade_id": "p-1", "created_ts": 1001, "signal_dir": "SHORT", "limit_price": 101, "qty": 0.1}
            position = {"trade_id": "p-1", "entry_ts": 1010, "entry": 101, "qty": 0.1}
            dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            again = dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            dual_write_paper_fill(order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp)
            fill_again = dual_write_paper_fill(order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp)
            self.assertTrue(all(row["duplicate"] for row in again["writes"]))
            self.assertTrue(all(row["duplicate"] for row in fill_again["writes"]))
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["ledger_counts"]["order_intent"], 1)
            self.assertEqual(store.verify()["ledger_counts"]["execution"], 1)

    def test_missing_policy_signature_gets_stable_explicit_paper_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {"trade_id": "p-2", "created_ts_ts": 1000, "raw_direction": "LONG", "shared_ai_call_id": "scan-2", "policy_id": "PATIENT", "research_lane": "PATIENT"}
            order = {"trade_id": "p-2", "created_ts": 1001, "signal_dir": "LONG", "limit_price": 99, "qty": 0.1}
            dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            dual_write_paper_fill(order, signal, {"trade_id": "p-2", "entry_ts": 1010, "entry": 99, "qty": 0.1}, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            execution = json.loads(store.ledger_path("execution").read_text().strip())
            self.assertTrue(intent["policy_signature"].startswith("paper-policy-"))
            self.assertTrue(intent["policy_epoch_id"].startswith("paper-policy-epoch-"))
            self.assertEqual(intent["policy_signature"], execution["policy_signature"])
            self.assertEqual(intent["policy_epoch_id"], execution["policy_epoch_id"])

    def test_paper_close_records_realized_result_without_claiming_exchange_actual(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {"trade_id": "p-3", "created_ts_ts": 1000, "raw_direction": "LONG", "shared_ai_call_id": "scan-3", "policy_id": "PATIENT", "research_lane": "PATIENT"}
            position = {"trade_id": "p-3", "entry_ts": 1010, "entry": 99, "qty": 0.1, "dir": "LONG", "research_lane": "PATIENT"}
            outcome = {"trade_id": "p-3", "close_ts": "1970-01-01T00:17:00Z", "exit": 100, "net_pnl_usd": 1.0, "gross_pnl_usd": 1.0, "trading_fees_usd": 0.0, "funding_fees_usd": 0.0, "exit_reason": "ATR_TP_2_5"}
            receipt = dual_write_paper_close(position, signal, outcome, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            execution = json.loads(store.ledger_path("execution").read_text().strip())
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())
            self.assertTrue(receipt["store_verification"]["passed"])
            self.assertEqual(execution["net_pnl_usd"], 1.0)
            self.assertEqual(execution["trading_fees_usd"], 0.0)
            self.assertFalse(execution["authenticated_exchange_actual"])
            self.assertTrue(lifecycle["terminal"])
            self.assertFalse(lifecycle["ranking_eligible"])
            self.assertEqual(lifecycle["outcome_state"], "REALIZED_PROFIT")
            self.assertEqual(lifecycle["observation_status"], "PAPER_POSITION_CLOSED")
            self.assertEqual(lifecycle["effective_execution_mode"], "PAPER_OBSERVED")
            for row in (execution, lifecycle):
                self.assertEqual(row["research_lane"], "PATIENT")
                self.assertEqual(row["shared_ai_call_id"], "scan-3")
                self.assertEqual(row["policy_id"], "PATIENT")
                self.assertTrue(row["policy_signature"].startswith("paper-policy-"))

    def test_paper_close_freezes_one_second_market_path_for_policy_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp) / "market_microstructure_1s.jsonl"
            tape.write_text("\n".join(json.dumps({
                "schema": "market_microstructure_1s_v1",
                "symbol": "tBTCF0:USTF0",
                "bucket_ts": ts,
                "last": 100.0 + (ts - 1000),
                "bid": 99.5 + (ts - 1000),
                "ask": 100.5 + (ts - 1000),
                "bid_qty": 2.0,
                "ask_qty": 3.0,
                "fresh": True,
                "valid_bbo": True,
            }) for ts in range(999, 1005)) + "\n", encoding="utf-8")
            signal = {
                "trade_id": "p-path", "created_ts_ts": 1000,
                "raw_direction": "LONG", "shared_ai_call_id": "scan-path",
                "policy_id": "PATIENT", "research_lane": "PATIENT",
                "symbol": "tBTCF0:USTF0",
            }
            position = {
                "trade_id": "p-path", "entry_ts": 1001, "entry": 100,
                "qty": 0.1, "dir": "LONG", "research_lane": "PATIENT",
            }
            outcome = {
                "trade_id": "p-path", "close_ts": 1004, "exit": 104,
                "net_pnl_usd": 4.0, "exit_reason": "ATR_TP_2_5",
                "entry_context": {"regime": "BULL", "adx": 28.0, "sr_state": "FREE_RANGE", "ema9": 99.0},
                "exit_context": {"regime": "BULL", "adx": 31.0, "sr_state": "AT_RESISTANCE", "ema9": 103.0},
                "exit_market_receipt": {"basis": "SIDE_CORRECT_BBO_DEPTH", "best_bid": 103.9, "best_ask": 104.0},
                "partial_exit_receipts": [{"ts": 1003, "action": "TP1", "price": 103,
                                             "closed_qty": 0.025, "remaining_fraction": 0.75,
                                             "realized_gross_usd": 0.075}],
            }

            receipt = dual_write_paper_close(
                position, signal, outcome, epoch_id="epoch-v3-test", data_dir=tmp,
            )
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())
            market_row = json.loads(store.ledger_path("market_segment").read_text().strip())
            execution = json.loads(store.ledger_path("execution").read_text().strip())
            ref = lifecycle["market_segment_refs"][0]
            envelope = json.loads((Path(tmp) / ref["relative_path"]).read_text())

            self.assertTrue(receipt["store_verification"]["passed"])
            self.assertEqual(receipt["store_verification"]["market_segment_count"], 1)
            self.assertEqual(ref["row_count"], 5)
            self.assertEqual(market_row["coverage"]["max_gap_sec"], 1.0)
            self.assertTrue(market_row["coverage"]["two_second_or_better"])
            self.assertEqual(envelope["rows"][0]["ts"], 1000.0)
            self.assertEqual(envelope["rows"][0]["price"], 100.0)
            self.assertEqual(envelope["rows"][-1]["ask_qty"], 3.0)
            self.assertEqual(lifecycle["ranking_blocker"], "POLICY_REPLAY_PENDING")
            self.assertEqual(lifecycle["opportunity_id"], f"opportunity:{receipt['episode_id']}")
            self.assertEqual(lifecycle["tape_id"], f"tape:{ref['sha256']}")
            self.assertEqual(market_row["tape_id"], lifecycle["tape_id"])
            self.assertEqual(execution["entry_context"]["regime"], "BULL")
            self.assertEqual(execution["exit_context"]["sr_state"], "AT_RESISTANCE")
            self.assertEqual(execution["exit_market_receipt"]["basis"], "SIDE_CORRECT_BBO_DEPTH")
            self.assertEqual(execution["partial_exits"][0]["closed_qty"], 0.025)
            self.assertEqual(execution["protection_trajectory"]["partial_exit_count"], 1)
            self.assertEqual(execution["path_extrema"]["basis"], "OBSERVED_1S_PRICE_PATH")
            self.assertEqual(execution["path_extrema"]["mfe_pct"], 4.0)
            self.assertEqual(execution["path_extrema"]["mae_pct"], 1.0)
            self.assertEqual(execution["path_extrema"]["time_to_mfe_sec"], 3.0)
            self.assertEqual(execution["path_extrema"]["time_to_mae_sec"], 0.0)

    def test_paper_close_fails_path_qualification_when_tape_has_large_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "market_microstructure_1s.jsonl").write_text(
                "\n".join(json.dumps({"bucket_ts": ts, "last": 100 + ts}) for ts in (1000, 1004)) + "\n",
                encoding="utf-8",
            )
            signal = {"trade_id": "p-gap", "created_ts_ts": 1000,
                      "raw_direction": "LONG", "shared_ai_call_id": "scan-gap"}
            position = {"trade_id": "p-gap", "entry_ts": 1000, "entry": 100, "qty": 0.1}
            outcome = {"trade_id": "p-gap", "close_ts": 1004, "exit": 101,
                       "net_pnl_usd": 1.0, "exit_reason": "TEST"}

            dual_write_paper_close(
                position, signal, outcome, epoch_id="epoch-v3-test", data_dir=tmp,
            )
            lifecycle = json.loads(
                V3EvidenceStore(tmp, epoch_id="epoch-v3-test").ledger_path("lifecycle").read_text().strip()
            )
            self.assertFalse(lifecycle["market_segment_coverage"]["two_second_or_better"])
            self.assertEqual(lifecycle["ranking_blocker"], "MARKET_PATH_INCOMPLETE")

    def test_close_reuses_frozen_submit_identity_instead_of_recomputing_sparse_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {
                "trade_id": "cont-1", "created_ts_ts": 1000,
                "raw_direction": "LONG", "shared_ai_call_id": "scan-frozen",
                "research_lane": "CONTINUOUS", "policy_id": "CONTINUOUS",
                "policy_signature": "base-control", "policy_epoch_id": "base-epoch",
                "entry_limit_policy": "deterministic_0.1pct_offset_v1",
                "entry_offset_fraction": 0.001,
            }
            order = {
                "trade_id": "cont-1", "created_ts": 1001,
                "signal_dir": "LONG", "limit_price": 99.9, "qty": 0.1,
                "research_lane": "CONTINUOUS",
            }
            submit = dual_write_paper_order_intent(
                order, signal, epoch_id="epoch-v3-test", data_dir=tmp,
            )
            frozen = paper_policy_identity_for_sources("epoch-v3-test", signal, order)
            position = {
                "trade_id": "cont-1", "entry_ts": 1010, "entry": 99.9,
                "qty": 0.1, "dir": "LONG", "research_lane": "CONTINUOUS",
                "shared_ai_call_id": "scan-frozen", **frozen,
            }
            dual_write_paper_fill(
                order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp,
            )
            dual_write_paper_close(
                position,
                {"trade_id": "cont-1", "shared_ai_call_id": "scan-frozen"},
                {"trade_id": "cont-1", "close_ts": 1020, "exit": 100.1,
                 "net_pnl_usd": 0.2, "exit_reason": "TEST"},
                epoch_id="epoch-v3-test", data_dir=tmp,
            )
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            intent = json.loads(store.ledger_path("order_intent").read_text().splitlines()[0])
            execution_rows = [json.loads(line) for line in store.ledger_path("execution").read_text().splitlines()]
            lifecycle_rows = [json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()]
            attributable = execution_rows + [
                row for row in lifecycle_rows
                if row.get("observation_status") in {"PAPER_POSITION_OPEN", "PAPER_POSITION_CLOSED"}
            ]
            self.assertEqual(frozen["policy_signature"], intent["policy_signature"])
            self.assertEqual({row["policy_signature"] for row in attributable}, {intent["policy_signature"]})
            self.assertEqual({row["policy_epoch_id"] for row in attributable}, {intent["policy_epoch_id"]})

    def test_shared_signal_identity_cannot_override_lane_owned_fill_and_close_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared_signal = {
                "trade_id": "shared-control", "created_ts_ts": 1000,
                "raw_direction": "LONG", "shared_ai_call_id": "scan-two-lanes",
                "research_lane": "CONTINUOUS", "policy_id": "CONTROL",
                "policy_signature": "control-signature", "policy_epoch_id": "control-epoch",
                "policy_identity_schema": "paper_policy_identity_v3",
                "paper_policy_spec": {"paper_only": True, "policy_id": "CONTROL"},
            }
            lane_order = {
                "trade_id": "lane-3", "created_ts": 1001, "signal_dir": "LONG",
                "limit_price": 99.9, "qty": 0.1, "research_lane": "OFFSET_029_ATR_PROTECTED",
                "policy_id": "LANE_3_POLICY", "policy_signature": "lane-3-signature",
                "policy_epoch_id": "lane-3-epoch", "policy_identity_schema": "paper_policy_identity_v3",
                "paper_policy_spec": {
                    "paper_only": True, "policy_id": "LANE_3_POLICY",
                    "research_lane": "OFFSET_029_ATR_PROTECTED",
                },
            }
            lane_position = {
                **lane_order, "entry_ts": 1010, "entry": 99.9, "dir": "LONG",
            }
            expected_lane_identity = paper_policy_identity_for_sources(
                "epoch-v3-test", lane_order,
            )
            dual_write_paper_fill(
                lane_order, shared_signal, lane_position,
                epoch_id="epoch-v3-test", data_dir=tmp,
            )
            dual_write_paper_close(
                lane_position, shared_signal,
                {"trade_id": "lane-3", "close_ts": 1020, "exit": 100.1,
                 "net_pnl_usd": 0.2, "exit_reason": "TEST"},
                epoch_id="epoch-v3-test", data_dir=tmp,
            )
            rows = [
                json.loads(line)
                for line in V3EvidenceStore(tmp, epoch_id="epoch-v3-test").ledger_path("execution").read_text().splitlines()
            ]
            self.assertEqual(
                {row["policy_signature"] for row in rows},
                {expected_lane_identity["policy_signature"]},
            )
            self.assertEqual({row["policy_id"] for row in rows}, {"LANE_3_POLICY"})

    def test_provisional_without_stable_identity_is_deferred_without_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dual_write_provisional_source("cont-pending", {
                "created_ts_ts": 1000, "final_direction": "LONG", "symbol": "BTCUSD",
            }, epoch_id="epoch-v3-test", data_dir=tmp)
            self.assertTrue(receipt["deferred"])
            self.assertEqual(receipt["reason"], "CAUSAL_IDENTITY_PENDING")
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["ledger_counts"]["opportunity"], 0)
            self.assertEqual(store.verify()["ledger_counts"]["lifecycle"], 0)

    def test_provisional_opportunity_is_available_before_terminal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dual_write_provisional_source("cont-early", {
                "created_ts_ts": 1000,
                "final_direction": "LONG",
                "symbol": "tBTCF0:USTF0",
                "shared_ai_call_id": "scan-early",
                "observation_status": "WAITING_120M",
            }, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertTrue(receipt["store_verification"]["passed"])
            opportunity = json.loads(store.ledger_path("opportunity").read_text().strip())
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())
            self.assertTrue(opportunity["first_observed_as_provisional"])
            self.assertFalse(lifecycle["terminal"])
            self.assertEqual(lifecycle["ranking_blocker"], "PATH_NOT_MATURED")

    def test_one_episode_many_branches_has_one_opportunity(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = dual_write_v22_record(_event("cont-1"), data_dir=tmp)
            b = dual_write_v22_record(_event("scan-1"), data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertTrue(a["store_verification"]["passed"])
            self.assertTrue(b["store_verification"]["passed"])
            self.assertEqual(len(store.ledger_path("opportunity").read_text().splitlines()), 1)
            self.assertEqual(len(store.ledger_path("decision").read_text().splitlines()), 2)

    def test_large_market_path_is_stored_once_and_referenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event()
            dual_write_v22_record(event, data_dir=tmp)
            dual_write_v22_record({**event, "event_id": "cont-2"}, data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["market_segment_count"], 1)
            lifecycle = [json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()]
            self.assertEqual(lifecycle[0]["market_segment_refs"][0]["sha256"], lifecycle[1]["market_segment_refs"][0]["sha256"])

    def test_unfilled_is_not_realized_zero_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event()
            event["exact_reason"] = "SIGNAL_TTL_EXPIRED"
            event["fill_time_revalidation"] = {
                "performed": True, "result": "BLOCKED",
                "reason": "FILL_REVALIDATION_DIRECTION_CHANGED",
            }
            dual_write_v22_record(event, data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())
            self.assertEqual(lifecycle["outcome_state"], "NO_FILL")
            self.assertNotEqual(lifecycle["outcome_state"], "REALIZED_ZERO_PNL")
            self.assertTrue(lifecycle["terminal_no_fill"])
            self.assertTrue(lifecycle["terminal_ttl_expired"])
            self.assertEqual(lifecycle["terminal_reason"], "SIGNAL_TTL_EXPIRED")
            self.assertEqual(lifecycle["fill_time_revalidation"]["result"], "BLOCKED")

    def test_source_fill_without_shared_ai_identity_is_not_normalized_as_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event("scan-legacy-fill", "episode-legacy-fill")
            event["event_episode"] = {
                "shared_ai_call_id": None,
                "grouping_basis": "TIME_DIRECTION_SYMBOL_FALLBACK",
            }
            event["base_policy_id"] = "CONTROL_V1"
            event["envelope"] = {
                **event["envelope"],
                "policy_id": "CONTROL_V1",
            }
            event["live_fill_ts"] = 1005.0
            event["live_fill_price"] = 100.25

            receipt = dual_write_v22_record(event, data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())

            self.assertFalse(store.ledger_path("execution").exists())
            self.assertFalse(receipt["execution_normalized"])
            self.assertEqual(
                receipt["execution_normalization_blocker"],
                "SOURCE_FILL_CAUSAL_IDENTITY_INCOMPLETE",
            )
            self.assertFalse(lifecycle["ranking_eligible"])
            self.assertEqual(
                lifecycle["ranking_blocker"],
                "SOURCE_FILL_CAUSAL_IDENTITY_INCOMPLETE",
            )

    def test_source_fill_with_complete_identity_preserves_policy_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event("scan-attributable-fill", "episode-attributable-fill")
            event["base_policy_id"] = "CONTROL_V1"
            event["envelope"] = {
                **event["envelope"],
                "policy_id": "CONTROL_V1",
            }
            event["live_fill_ts"] = 1005.0
            event["live_fill_price"] = 100.25

            receipt = dual_write_v22_record(event, data_dir=tmp)
            execution = json.loads(V3EvidenceStore(
                tmp, epoch_id="epoch-v3-test",
            ).ledger_path("execution").read_text().strip())

            self.assertTrue(receipt["execution_normalized"])
            self.assertEqual(execution["shared_ai_call_id"], "scan-1")
            self.assertEqual(execution["policy_id"], "CONTROL_V1")
            self.assertEqual(execution["policy_signature"], "policy-a")
            self.assertEqual(execution["policy_epoch_id"], "pe-a")
            self.assertEqual(execution["research_lane"], "CONTROL_V1")

    def test_order_intent_preserves_exact_entry_children_for_v3_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event()
            event["atr14_pct"] = 0.42
            event["envelope"]["signal_price"] = 100.0
            dual_write_v22_record(event, data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            self.assertEqual(intent["entry_children"], event["entry_children"])
            self.assertEqual(intent["entry_children_count"], 2)
            self.assertEqual(intent["atr14_pct"], 0.42)
            self.assertEqual(intent["signal_price"], 100.0)
            self.assertEqual(intent["executed_direction"], "LONG")

    def test_terminal_reconciliation_backfills_only_current_epoch_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = _event("scan-current", "episode-current")
            foreign = {**_event("scan-foreign", "episode-foreign"), "epoch_id": "epoch-old"}
            foreign["envelope"] = {**foreign["envelope"], "epoch_id": "epoch-old"}
            source = V3EvidenceStore(tmp, epoch_id="epoch-v3-test").root / "research_events_v22.jsonl"
            source.write_text(
                "\n".join(json.dumps(row, separators=(",", ":")) for row in (current, foreign)) + "\n",
                encoding="utf-8",
            )
            dual_write_provisional_source("scan-current", {
                "created_ts_ts": 1000,
                "raw_direction": "LONG",
                "symbol": "tBTCF0:USTF0",
                "shared_ai_call_id": "scan-1",
                "observation_status": "WAITING_ENTRY_WINDOW",
            }, epoch_id="epoch-v3-test", data_dir=tmp)

            first = reconcile_terminal_v22_into_v3(data_dir=tmp, epoch_id="epoch-v3-test")
            second = reconcile_terminal_v22_into_v3(data_dir=tmp, epoch_id="epoch-v3-test")
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            lifecycle = [json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()]

            self.assertTrue(first["passed"])
            self.assertEqual(first["backfilled"], 1)
            self.assertEqual(first["foreign_epoch"], 1)
            self.assertEqual(second["backfilled"], 0)
            self.assertEqual(second["already_present"], 1)
            self.assertEqual(sum(row["record_id"] == "lifecycle:scan-current:terminal" for row in lifecycle), 1)
            self.assertFalse(any(row.get("event_id") == "scan-foreign" for row in lifecycle))


if __name__ == "__main__":
    unittest.main()
