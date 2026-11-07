"""Synthetic acceptance for the fixture-only CPU lifecycle reference.

Oracles below are handwritten constants. They do not call the engine's
pricing or liquidation helpers to invent the expected value. A mutation
receipt records the injected fault and the assertion that failed.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.event_replay_v1.accounting import FeeDispositionError
from research.event_replay_v1.engine import FixtureError, replay
from research.event_replay_v1.guards import (
    MAX_COMMAND_SECONDS,
    MAX_EVENTS,
    MAX_RECEIPT_BYTES,
    MAX_RSS_BYTES,
    MAX_SUITE_SECONDS,
    MAX_WORKERS,
    CapStop,
    GuardError,
    ResourceLimits,
    ResourceObservation,
    RuntimeGuard,
    enforce_observation,
)
from research.event_replay_v1.pricer import PRICER_ID

PACKAGE = ROOT / "research" / "event_replay_v1"
FIXTURE_PATH = PACKAGE / "fixtures" / "lifecycle.json"
LOCK_PATH = PACKAGE / "dependencies.lock.json"
SUITE_STARTED = time.monotonic()
EXPECTED_FILES = [
    "research/event_replay_v1/README.md",
    "research/event_replay_v1/__init__.py",
    "research/event_replay_v1/accounting.py",
    "research/event_replay_v1/dependencies.lock.json",
    "research/event_replay_v1/engine.py",
    "research/event_replay_v1/entry.py",
    "research/event_replay_v1/exits.py",
    "research/event_replay_v1/fixture_cli.py",
    "research/event_replay_v1/fixtures/lifecycle.json",
    "research/event_replay_v1/guards.py",
    "research/event_replay_v1/order_state.py",
    "research/event_replay_v1/pricer.py",
    "research/event_replay_v1/risk.py",
    "research/event_replay_v1/tests/test_acceptance.py",
]
AUTHORITATIVE_BASE = "40087a65f2157f8c6701ea3670adc5c29a28c87d"
STAGE_A_DESIGN = "ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122"


def _load():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _project(result, scenario_id, until):
    scenario = result["scenarios"][scenario_id]

    def keep(rows):
        return [row for row in rows if row.get("time_us", 0) <= until]

    payload = {
        "actions": keep(scenario["actions"]),
        "cancels": keep(scenario["cancels"]),
        "exits": keep(scenario["exits"]),
        "fills": keep(scenario["fills"]),
        "orders": keep(scenario["orders"]),
        "signals": {
            key: value
            for key, value in scenario["signals"].items()
            if value["time_us"] <= until
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class AcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = _load()
        cls.result = replay(cls.fixture, workers=1)
        cls.signals = {row["signal_id"]: row for row in cls.result["accounting"]["signals"]}

    @classmethod
    def tearDownClass(cls):
        elapsed = time.monotonic() - SUITE_STARTED
        enforce_observation(
            ResourceObservation(suite_seconds=elapsed, workers=1, events=0, rss_bytes=0)
        )

    def scenario(self, scenario_id):
        return self.result["scenarios"][scenario_id]

    def fills(self, scenario_id, role=None):
        rows = self.scenario(scenario_id)["fills"]
        if role is not None:
            rows = [row for row in rows if row["role"] == role]
        return rows

    def test_manifest_is_exactly_fourteen_new_files(self):
        found = sorted(
            path.relative_to(ROOT).as_posix()
            for path in PACKAGE.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
        self.assertEqual(found, EXPECTED_FILES)

    def test_scope_labels_live_off_and_stage_a_blocked(self):
        result = self.result
        self.assertEqual(result["compatibility"]["stage_a"], "BLOCKED")
        self.assertEqual(
            result["compatibility"]["fixture_stub"],
            "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED",
        )
        self.assertIsNone(result["compatibility"]["approved_stage_a_implementation_pin"])
        self.assertEqual(result["compatibility"]["stage_a_design_card_sha256"], STAGE_A_DESIGN)
        self.assertEqual(result["compatibility"]["stage_a_design_role"], "APPROVED_DESIGN_ONLY")
        self.assertFalse(result["compatibility"]["producer_schemas_redefined"])
        self.assertEqual(result["compatibility"]["interface_compatibility"], "BLOCKED")
        self.assertEqual(result["authoritative_base"], AUTHORITATIVE_BASE)
        self.assertEqual(result["live"], "OFF")
        self.assertIsNone(result["realistic_v1"])
        self.assertIsNone(result["real_policy"]["n"])
        self.assertIsNone(result["real_policy"]["dates"])
        self.assertIsNone(result["real_policy"]["maxhold"])
        self.assertIsNone(result["real_policy"]["cost_threshold"])
        self.assertEqual(result["real_policy"]["status"], "NULL/BLOCKED")
        self.assertFalse(result["r57_actual_sweep"])
        self.assertFalse(result["cpu_reference_is_r57_actual_sweep"])
        self.assertFalse(result["research_complete"])
        self.assertFalse(result["profitability_claim"])
        self.assertFalse(result["findings_claim"])
        self.assertTrue(result["synthetic_not_venue_proof"])
        self.assertFalse(result["tie_priority_is_live_policy"])
        self.assertEqual(
            result["fixture_tie_priority"],
            [
                "HARD_STOP",
                "LIQUIDATION",
                "THESIS_CUT",
                "LADDER",
                "BREAKEVEN",
                "TRAIL",
                "GIVEBACK",
                "TARGET",
                "TIME",
            ],
        )

    def test_one_pricer_for_every_strategy(self):
        pricers = {
            fill["pricer_id"]
            for scenario in self.result["scenarios"].values()
            for fill in scenario["fills"]
        }
        strategies = {row["strategy_id"] for row in self.signals.values()}
        self.assertEqual(pricers, {PRICER_ID})
        self.assertEqual(strategies, {"fixture-strategy-A", "fixture-strategy-B"})

    def test_explicit_zero_fee_and_blocked_costs(self):
        accounting = self.result["accounting"]
        self.assertEqual(self.fixture["trading_fee"], "0")
        self.assertIsNone(self.fixture["fee_verification_evidence"])
        self.assertEqual(accounting["trading_fee"], "0")
        self.assertTrue(accounting["trading_fee_explicit"])
        self.assertFalse(accounting["trading_fee_default_applied"])
        self.assertIsNone(accounting["fee_verification_evidence"])
        self.assertEqual(accounting["fee_verification_status"], "UNVERIFIED")
        self.assertFalse(accounting["venue_wide_fee_claim"])
        self.assertIn("NOT_VENUE_WIDE", accounting["fee_scope"])
        self.assertEqual(accounting["funding_charges"]["status"], "EXCLUDED_NOT_MODELLED")
        self.assertIsNone(accounting["funding_charges"]["amount"])
        self.assertFalse(accounting["funding_charges"]["modelled"])
        self.assertFalse(accounting["funding_charges"]["collection_gate"])
        self.assertIsNone(accounting["measured_spread_cost"])
        self.assertIsNone(accounting["measured_depth_cost"])
        self.assertIsNone(accounting["measured_latency_cost"])
        self.assertEqual(accounting["measured_spread_cost_status"], "NULL/BLOCKED")
        self.assertEqual(accounting["measured_depth_cost_status"], "NULL/BLOCKED")
        self.assertEqual(accounting["measured_latency_cost_status"], "NULL/BLOCKED")
        self.assertEqual(accounting["queue_delay_value_class"], "SYNTHETIC")
        self.assertFalse(accounting["memos_subtracted_from_net"])
        self.assertFalse(accounting["double_charged"])
        self.assertEqual(
            Decimal(accounting["net_pnl"]),
            Decimal(accounting["price_pnl"]) - Decimal(accounting["trading_fee"]),
        )

    def test_missing_or_nonzero_fee_is_not_defaulted(self):
        missing = json.loads(json.dumps(self.fixture))
        del missing["trading_fee"]
        with self.assertRaises(FeeDispositionError) as caught:
            replay(missing, workers=1)
        self.assertEqual(str(caught.exception), "TRADING_FEE_EXPLICIT_VALUE_REQUIRED")
        nonzero = json.loads(json.dumps(self.fixture))
        nonzero["trading_fee"] = "0.1"
        with self.assertRaises(FeeDispositionError) as caught:
            replay(nonzero, workers=1)
        self.assertEqual(str(caught.exception), "TRADING_FEE_DISPOSITION_REQUIRES_EXPLICIT_ZERO")
        funded = json.loads(json.dumps(self.fixture))
        funded["funding_charges"]["amount"] = 0
        with self.assertRaises(FeeDispositionError) as caught:
            replay(funded, workers=1)
        self.assertEqual(str(caught.exception), "FUNDING_EXCLUDED_NOT_MODELLED_AMOUNT_MUST_BE_NULL")

    def test_canaries_never_enter_output(self):
        raw = FIXTURE_PATH.read_text(encoding="utf-8")
        self.assertIn("CANARY-SECRET-FIXTURE-ONLY", raw)
        self.assertIn("CANARY-RAW-PAYLOAD-FIXTURE-ONLY", raw)
        blob = json.dumps(self.result)
        self.assertNotIn("CANARY-SECRET-FIXTURE-ONLY", blob)
        self.assertNotIn("CANARY-RAW-PAYLOAD-FIXTURE-ONLY", blob)
        self.assertNotIn("CANARY-", blob)

    def test_exchange_timestamp_is_not_replaced_by_receive_time(self):
        observation = self.scenario("S-PUB")["observations"][0]
        self.assertIsNone(observation["exchange_timestamp_utc"])
        self.assertEqual(observation["receive_time_utc"], "2099-01-01T00:00:01Z")
        self.assertNotEqual(
            observation["exchange_timestamp_utc"], observation["receive_time_utc"]
        )
        self.assertEqual(observation["stage_a_stub"], "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED")
        self.assertIsNone(observation["approved_stage_a_pin"])

    def test_unlabelled_stage_a_stub_is_rejected(self):
        broken = json.loads(json.dumps(self.fixture))
        for event in broken["scenarios"][0]["events"]:
            if event["type"] == "observation":
                event["stage_a_stub"] = "approved-looking"
        with self.assertRaises(FixtureError) as caught:
            replay(broken, workers=1)
        self.assertEqual(str(caught.exception), "STAGE_A_STUB_LABEL_REQUIRED")

    def test_strict_after_close_publication_and_receive_order(self):
        self.assertEqual(self.signals["s-boundary"]["status"], "NOT_STRICTLY_AFTER_CLOSE")
        self.assertEqual(self.signals["s-boundary"]["realized_pnl"], "0")
        self.assertTrue(self.signals["s-boundary"]["is_miss"])
        self.assertEqual(self.fills("S-BOUNDARY"), [])
        self.assertEqual(self.signals["s-pub"]["status"], "INVALID_CAUSAL")
        self.assertEqual(self.fills("S-PUB"), [])
        self.assertEqual(self.signals["srecv-early"]["status"], "INVALID_CAUSAL")
        self.assertEqual(self.signals["srecv-ok"]["status"], "CLOSED")
        self.assertEqual(self.signals["srecv-ok"]["realized_pnl"], "0")

    def test_future_perturbation_keeps_prior_bytes(self):
        until = 2100
        original = _project(self.result, "S0", until)
        sliced = copy.deepcopy(self.fixture)
        for scenario in sliced["scenarios"]:
            if scenario["scenario_id"] == "S0":
                scenario["events"] = [
                    event for event in scenario["events"] if event["time_us"] <= until
                ]
        perturbed = copy.deepcopy(self.fixture)
        for scenario in perturbed["scenarios"]:
            if scenario["scenario_id"] != "S0":
                continue
            scenario["events"].extend(
                [
                    {
                        "event_id": "s0-late-obs",
                        "type": "observation",
                        "time_us": 4200,
                        "seq": 20,
                        "source_id": "fixture-source",
                        "series_id": "fixture-series",
                        "source_time_us": 4200,
                        "publication_us": 4200,
                        "bar_close_us": 4300,
                        "receive_time_utc": "2099-01-01T00:00:01Z",
                        "exchange_timestamp_utc": None,
                        "connection_epoch": "fixture-epoch",
                        "release_status": "first-release",
                        "validity": "VALID",
                        "fixture_only": True,
                        "compatibility": "BLOCKED",
                        "stage_a_stub": "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED",
                        "schema_id": "fixture-only.normalized_observation.unpinned",
                        "approved_stage_a_pin": None,
                        "linkage": {
                            "segment_member_record": "fixture-segment:fixture-member:late",
                            "whole_segment_digest": "fixture-digest-late",
                            "prefix_extent": 1,
                            "prefix_digest": "fixture-prefix-late",
                            "member_byte_range": [0, 1],
                            "member_digest": "fixture-member-late",
                        },
                    },
                    {
                        "event_id": "s0-late-book",
                        "type": "book",
                        "time_us": 4500,
                        "seq": 21,
                        "availability_us": 4500,
                        "valid_from_us": 4500,
                        "valid_to_us": 6000,
                        "stale": False,
                        "bids": [["1", "1"]],
                        "asks": [["1", "1"]],
                        "exchange_timestamp_utc": None,
                        "value_class": "SYNTHETIC",
                    },
                    {
                        "event_id": "s0-late",
                        "type": "signal",
                        "time_us": 5000,
                        "seq": 22,
                        "signal_id": "s0-late",
                        "strategy_id": "fixture-strategy-A",
                        "side": "LONG",
                        "qty": "1",
                        "bar_close_us": 5000,
                        "arrival_delay_us": 10,
                        "style": "MARKETABLE",
                        "delay_value_class": "SYNTHETIC",
                        "entry_offset_bps": "0",
                        "regime": "FIXTURE_REGIME_NOT_LIVE_POLICY",
                        "observation_ids": ["s0-late-obs"],
                        "protections": {},
                        "thesis_cut": False,
                    },
                ]
            )
        sliced_result = replay(sliced, workers=1)
        perturbed_result = replay(perturbed, workers=1)
        self.assertEqual(original, _project(sliced_result, "S0", until))
        self.assertEqual(original, _project(perturbed_result, "S0", until))
        self.assertIn("s0-late", perturbed_result["scenarios"]["S0"]["signals"])
        self.assertEqual(self.fills("S0", "ENTRY")[0]["book_id"], "s0-book-entry")
        self.assertNotEqual(self.fills("S0", "ENTRY")[0]["book_id"], "s0-book-future")

    def test_hand_calculated_multilevel_taker_walk(self):
        entry_notional = Decimal(100) * 2 + Decimal(101) * 2
        self.assertEqual(entry_notional, Decimal(402))
        entry = self.fills("S1", "ENTRY")[0]
        exit_fill = self.fills("S1", "EXIT")[0]
        self.assertEqual(entry["parts"], [
            {"price": "100", "qty": "2"},
            {"price": "101", "qty": "2"},
        ])
        self.assertEqual(Decimal(entry["notional"]), entry_notional)
        self.assertEqual(Decimal(entry["price"]), Decimal("100.5"))
        self.assertEqual(entry["mechanism"], "OPPOSITE_SIDE_DEPTH_WALK")
        self.assertEqual(Decimal(exit_fill["price"]), Decimal(110))
        self.assertEqual(Decimal(self.signals["s1"]["realized_pnl"]), Decimal(440) - entry_notional)
        self.assertEqual(self.signals["s1"]["realized_pnl"], "38")
        self.assertEqual(Decimal(entry["depth_slippage"]), (Decimal("100.5") - Decimal(100)) * 4)
        self.assertNotEqual(Decimal(entry["price"]), (Decimal(98) + Decimal(100)) / 2)
        self.assertNotEqual(entry["mechanism"], "PASSIVE_RESTING_LIMIT")

    def test_stale_and_missing_depth_reject(self):
        self.assertEqual(self.signals["s2"]["status"], "INVALID_FOR_FILLS")
        self.assertEqual(self.signals["s3"]["status"], "INVALID_FOR_FILLS")
        self.assertEqual(self.fills("S2"), [])
        self.assertEqual(self.fills("S3"), [])
        self.assertEqual(self.signals["s2"]["realized_pnl"], "0")
        self.assertEqual(self.signals["s3"]["realized_pnl"], "0")

    def test_queue_consumed_versus_touch_and_traded_through(self):
        queue = self.fills("S4", "ENTRY")[0]
        self.assertEqual(queue["evidence"], "QUEUE_CONSUMPTION")
        self.assertEqual(queue["mechanism"], "PASSIVE_RESTING_LIMIT")
        self.assertEqual(queue["price"], "100")
        self.assertEqual(queue["queue_value_class"], "SYNTHETIC")
        self.assertEqual(self.fills("S5"), [])
        self.assertEqual(self.signals["s5"]["status"], "UNFILLED")
        through = self.fills("S6", "ENTRY")[0]
        self.assertEqual(through["evidence"], "TRADED_THROUGH")
        self.assertEqual(through["mechanism"], "PASSIVE_RESTING_LIMIT")
        self.assertEqual(through["price"], "100")
        self.assertNotEqual(through["price"], "99")
        self.assertEqual(self.scenario("S6")["duplicate_events_ignored"], 1)
        offset = self.fills("S-OFFSET", "ENTRY")[0]
        self.assertEqual(offset["price"], "99")
        self.assertEqual(offset["mechanism"], "PASSIVE_RESTING_LIMIT")
        self.assertNotEqual(offset["price"], "98")
        self.assertNotEqual(offset["price"], "100")

    def test_cancel_race_partial_duplicate_and_chase_conservation(self):
        entry = next(order for order in self.scenario("S7")["orders"] if order["order_id"] == "s7:entry")
        self.assertEqual(entry["filled_qty"], "4")
        self.assertEqual(entry["cancelled_qty"], "6")
        self.assertEqual(entry["rejected_qty"], "0")
        self.assertTrue(entry["conserved"])
        self.assertEqual(
            Decimal(entry["filled_qty"]) + Decimal(entry["cancelled_qty"]),
            Decimal(entry["original_qty"]),
        )
        self.assertEqual(self.scenario("S7")["signals"]["s7"]["duplicate_acks_ignored"], 1)
        self.assertEqual(self.scenario("S7")["duplicate_events_ignored"], 1)
        passive = next(order for order in self.scenario("S8")["orders"] if order["order_id"] == "s8:entry")
        chase = next(order for order in self.scenario("S8")["orders"] if order["order_id"] == "s8:chase")
        self.assertEqual(passive["filled_qty"], "2")
        self.assertEqual(passive["cancelled_qty"], "3")
        self.assertEqual(chase["original_qty"], "3")
        self.assertEqual(chase["filled_qty"], "3")
        self.assertEqual(chase["parent_order_id"], "s8:entry")
        self.assertFalse(chase["rounded_up"])
        cost = Decimal(2) * 100 + Decimal(3) * 110
        self.assertEqual(cost, Decimal(530))
        self.assertEqual(self.signals["s8"]["realized_pnl"], "20")
        for scenario in self.result["scenarios"].values():
            for order in scenario["orders"]:
                self.assertTrue(order["conserved"])
                self.assertFalse(order["rounded_up"])

    def test_every_protection_tie_adverse_gap_and_delayed_stop(self):
        winners = {
            exit_row["winner"]
            for scenario in self.result["scenarios"].values()
            for exit_row in scenario["exits"]
        }
        self.assertTrue(
            {
                "HARD_STOP",
                "LIQUIDATION",
                "THESIS_CUT",
                "LADDER",
                "BREAKEVEN",
                "TRAIL",
                "GIVEBACK",
                "TARGET",
                "TIME",
            }.issubset(winners)
        )
        ladder = self.scenario("S9")["exits"][0]
        self.assertEqual(ladder["winner"], "LADDER")
        self.assertIn("TARGET", ladder["suppressed"])
        self.assertEqual(ladder["considered"], ["LADDER", "TARGET"])
        ladder_fill = self.fills("S9", "EXIT")[0]
        self.assertEqual(ladder_fill["price"], "139")
        self.assertNotEqual(ladder_fill["price"], "115")
        self.assertNotEqual(ladder_fill["price"], "140")
        self.assertEqual(self.signals["s9"]["status"], "INCOMPLETE")
        self.assertEqual(self.signals["s9"]["realized_pnl"], "39")
        self.assertIsNotNone(self.signals["s9"]["realized_pnl"])
        stop = self.fills("S11", "EXIT")[0]
        self.assertEqual(self.scenario("S11")["exits"][0]["winner"], "HARD_STOP")
        self.assertEqual(self.scenario("S11")["exits"][0]["trigger_price"], "90")
        self.assertEqual(self.scenario("S11")["exits"][0]["observed_price"], "89")
        self.assertEqual(stop["price"], "80")
        self.assertNotEqual(stop["price"], "90")
        self.assertNotEqual(stop["price"], "89")
        be_fill = self.fills("S10", "EXIT")[0]
        self.assertEqual(self.scenario("S10")["exits"][0]["winner"], "BREAKEVEN")
        self.assertEqual(be_fill["price"], "79")
        self.assertNotEqual(be_fill["price"], "100")
        self.assertEqual(self.scenario("S-TRAIL")["exits"][0]["winner"], "TRAIL")
        self.assertNotEqual(self.fills("S-TRAIL", "EXIT")[0]["price"], "105")
        self.assertEqual(self.scenario("S-GIVEBACK")["exits"][0]["winner"], "GIVEBACK")
        self.assertNotEqual(self.fills("S-GIVEBACK", "EXIT")[0]["price"], "110")
        self.assertEqual(self.scenario("S-TARGET")["exits"][0]["winner"], "TARGET")
        self.assertNotEqual(self.fills("S-TARGET", "EXIT")[0]["price"], "110")
        self.assertEqual(self.scenario("S-TIME")["exits"][0]["winner"], "TIME")
        self.assertEqual(self.scenario("S-THESIS")["exits"][0]["winner"], "THESIS_CUT")

    def test_liquidation_unknown_terms_dust_and_no_roundup(self):
        self.assertEqual(self.signals["s12"]["liq_price"], "99.5")
        self.assertEqual(self.signals["s13"]["liq_price"], "100.5")
        self.assertEqual(self.signals["s12"]["liquidation_status"], "MODELLED_FIXTURE_IDENTITY_NOT_VENUE_PROOF")
        self.assertEqual(self.scenario("S12")["exits"][0]["winner"], "LIQUIDATION")
        self.assertEqual(self.scenario("S13")["exits"][0]["winner"], "LIQUIDATION")
        self.assertEqual(self.fills("S12", "EXIT")[0]["price"], "99")
        self.assertNotEqual(self.fills("S12", "EXIT")[0]["price"], "99.5")
        self.assertEqual(self.fills("S13", "EXIT")[0]["price"], "101")
        self.assertNotEqual(self.fills("S13", "EXIT")[0]["price"], "100.5")
        unknown = self.signals["s14"]
        self.assertEqual(unknown["status"], "INVALID_UNKNOWN_MAINTENANCE")
        self.assertIsNone(unknown["liq_price"])
        self.assertIsNone(unknown["realized_pnl"])
        self.assertTrue(unknown["incomplete"])
        self.assertFalse(unknown["is_miss"])
        self.assertEqual(self.signals["s15"]["status"], "MIN_SIZE")
        self.assertEqual(self.signals["s15"]["realized_pnl"], "0")
        self.assertFalse(self.signals["s15"]["rounded_up"])
        dust_order = self.scenario("S16")["orders"][0]
        self.assertEqual(dust_order["filled_qty"], "0")
        self.assertEqual(dust_order["original_qty"], "5")
        self.assertFalse(dust_order["rounded_up"])
        self.assertEqual(self.signals["s16"]["status"], "DUST")
        self.assertEqual(self.signals["s16"]["dust_qty_refused"], "0.4")
        self.assertEqual(self.signals["s17-b"]["status"], "CONCURRENCY")
        self.assertEqual(self.signals["s17-b"]["realized_pnl"], "0")
        self.assertEqual(self.signals["s17-a"]["status"], "CLOSED")

    def test_margin_is_not_maximum_loss(self):
        stop = self.signals["s11"]
        self.assertEqual(stop["initial_margin"], "10")
        self.assertEqual(stop["notional"], "1000")
        self.assertFalse(stop["margin_is_maximum_loss"])
        self.assertTrue(stop["loss_can_exceed_margin"])
        self.assertTrue(stop["realized_loss_exceeds_margin"])
        self.assertEqual(stop["realized_pnl"], "-200")
        self.assertGreater(Decimal("200"), Decimal(stop["initial_margin"]))

    def test_independent_accounting_oracle(self):
        expected = {
            "s0": "0",
            "s1": "38",
            "s2": "0",
            "s3": "0",
            "s4": "0",
            "s5": "0",
            "s6": "0",
            "s7": "0",
            "s8": "20",
            "s9": "39",
            "s10": "-21",
            "s11": "-200",
            "s12": "-10",
            "s13": "-10",
            "s14": None,
            "s15": "0",
            "s16": "0",
            "s17-a": "0",
            "s17-b": "0",
            "s-trail": "0",
            "s-giveback": "0",
            "s-target": "0",
            "s-time": "0",
            "s-thesis": "0",
            "s-boundary": "0",
            "s-pub": "0",
            "srecv-early": "0",
            "srecv-ok": "0",
            "s-offset": "0",
        }
        self.assertEqual(set(self.signals), set(expected))
        numerator = Decimal(0)
        for signal_id, pnl in expected.items():
            self.assertEqual(self.signals[signal_id]["realized_pnl"], pnl)
            if pnl is not None:
                numerator += Decimal(pnl)
        hand = (
            (Decimal(440) - (Decimal(100) * 2 + Decimal(101) * 2))
            + (Decimal(5 * 110) - (Decimal(2 * 100) + Decimal(3 * 110)))
            + (Decimal(139) - Decimal(100))
            + (Decimal(79) - Decimal(100))
            + (Decimal(80) - Decimal(100)) * 10
            + (Decimal(99) - Decimal(100)) * 10
            + (Decimal(100) - Decimal(101)) * 10
        )
        self.assertEqual(hand, Decimal("-144"))
        self.assertEqual(numerator, hand)
        depth = (Decimal("100.5") - Decimal(100)) * 4
        delay = (
            (Decimal(100) - Decimal(79)) * 1
            + (Decimal(90) - Decimal(80)) * 10
            + (Decimal("99.5") - Decimal(99)) * 10
            + (Decimal(101) - Decimal("100.5")) * 10
            + (Decimal(105) - Decimal(100)) * 1
            + (Decimal(110) - Decimal(100)) * 1
            + (Decimal(110) - Decimal(100)) * 1
        )
        self.assertEqual(depth, Decimal(2))
        self.assertEqual(delay, Decimal(156))
        accounting = self.result["accounting"]
        self.assertEqual(Decimal(accounting["numerator"]), numerator)
        self.assertEqual(accounting["denominator"], "29")
        self.assertEqual(Decimal(accounting["net_pnl"]), numerator - Decimal(accounting["trading_fee"]))
        self.assertEqual(Decimal(accounting["net_per_signal"]), numerator / Decimal(29))
        self.assertEqual(Decimal(accounting["synthetic_depth_slippage_memo"]), depth)
        self.assertEqual(Decimal(accounting["synthetic_delay_gap_memo"]), delay)
        wrong = numerator - depth - delay
        self.assertNotEqual(Decimal(accounting["net_pnl"]), wrong)
        self.assertEqual(accounting["aggregate_status"], "INCOMPLETE")
        self.assertEqual(accounting["unresolved_signal_ids"], ["s9", "s14"])
        self.assertEqual(len(accounting["missed_signal_ids"]), 9)
        self.assertEqual(len(self.signals), 29)

    def test_mutations_fail_named_oracles(self):
        checks = [
            (
                "admit_future_books",
                "S0 entry fill remains 100 when future books stay invisible",
                lambda result: self.assertEqual(
                    [row["price"] for row in result["scenarios"]["S0"]["fills"] if row["role"] == "ENTRY"],
                    ["100"],
                ),
            ),
            (
                "instant_cancels",
                "S7 in-flight partial remains 4 when cancel waits for the ack",
                lambda result: self.assertEqual(
                    next(order["filled_qty"] for order in result["scenarios"]["S7"]["orders"] if order["order_id"] == "s7:entry"),
                    "4",
                ),
            ),
            (
                "omit_partials",
                "S7 partial fill quantity remains 4",
                lambda result: self.assertEqual(
                    next(order["filled_qty"] for order in result["scenarios"]["S7"]["orders"] if order["order_id"] == "s7:entry"),
                    "4",
                ),
            ),
            (
                "fill_at_trigger_price",
                "S11 stop fills at the delayed book 80, not the trigger 90",
                lambda result: self.assertEqual(
                    [row["price"] for row in result["scenarios"]["S11"]["fills"] if row["role"] == "EXIT"],
                    ["80"],
                ),
            ),
            (
                "increase_size",
                "S15 below minimum stays MIN_SIZE with no round-up",
                lambda result: self.assertEqual(
                    next(row["status"] for row in result["accounting"]["signals"] if row["signal_id"] == "s15"),
                    "MIN_SIZE",
                ),
            ),
            (
                "drop_misses",
                "missed signals remain in the denominator of 29",
                lambda result: self.assertEqual(result["accounting"]["denominator"], "29"),
            ),
        ]
        receipts = []
        for fault, assertion_id, check in checks:
            mutated = replay(self.fixture, mutation=fault, workers=1)
            try:
                check(mutated)
            except AssertionError as exc:
                receipts.append(
                    {
                        "fault": fault,
                        "failed_assertion": assertion_id,
                        "failed": True,
                        "error": str(exc),
                    }
                )
            else:
                self.fail(f"mutation {fault} was not killed")
            check(self.result)
        self.assertEqual(len(receipts), 6)
        self.assertTrue(all(item["failed"] for item in receipts))
        blob = json.dumps(receipts)
        self.assertNotIn("CANARY-", blob)
        self.mutation_receipts = receipts

    def test_resource_caps_stop_and_this_run_is_inside_them(self):
        self.assertEqual(MAX_WORKERS, 2)
        self.assertEqual(MAX_RSS_BYTES, 512 * 1024 * 1024)
        self.assertEqual(MAX_COMMAND_SECONDS, 120)
        self.assertEqual(MAX_SUITE_SECONDS, 600)
        self.assertEqual(MAX_RECEIPT_BYTES, 8 * 1024 * 1024)
        self.assertEqual(MAX_EVENTS, 2048)
        probes = [
            (ResourceObservation(events=2049), "events"),
            (ResourceObservation(rss_bytes=MAX_RSS_BYTES + 1), "rss"),
            (ResourceObservation(command_seconds=MAX_COMMAND_SECONDS + 1), "command"),
            (ResourceObservation(suite_seconds=MAX_SUITE_SECONDS + 1), "suite"),
            (ResourceObservation(receipt_bytes=MAX_RECEIPT_BYTES + 1), "receipt"),
            (ResourceObservation(workers=3), "workers"),
        ]
        for observation, code in probes:
            with self.assertRaises(CapStop) as caught:
                enforce_observation(observation)
            self.assertEqual(str(caught.exception), f"CAP_STOP:{code}")
        enforce_observation(ResourceObservation(
            workers=2,
            rss_bytes=MAX_RSS_BYTES,
            command_seconds=MAX_COMMAND_SECONDS,
            suite_seconds=MAX_SUITE_SECONDS,
            receipt_bytes=MAX_RECEIPT_BYTES,
            events=MAX_EVENTS,
        ))
        with self.assertRaises(CapStop):
            replay(self.fixture, workers=3)
        tiny = copy.deepcopy(self.fixture)
        tiny["scenarios"] = tiny["scenarios"][:1]
        with self.assertRaises(CapStop) as caught:
            replay(tiny, workers=1, limits=ResourceLimits(max_events=1))
        self.assertEqual(str(caught.exception), "CAP_STOP:events")
        resource = self.result["resource"]
        self.assertTrue(resource["enforced"])
        self.assertEqual(resource["workers"], 1)
        self.assertLessEqual(resource["workers"], MAX_WORKERS)
        self.assertLessEqual(resource["rss_bytes"], MAX_RSS_BYTES)
        self.assertLessEqual(resource["command_seconds"], MAX_COMMAND_SECONDS)
        self.assertLessEqual(resource["events"], MAX_EVENTS)
        self.assertEqual(resource["events"], sum(len(item["events"]) for item in self.fixture["scenarios"]))
        self.assertGreater(resource["rss_bytes"], 0)

    def test_prohibited_access_rejects_before_side_effects(self):
        guard = RuntimeGuard()
        original = socket.socket
        trace = []
        guard.activate()
        try:
            with self.assertRaises(GuardError) as caught:
                socket.socket()
            self.assertEqual(str(caught.exception), "PROHIBITED_NETWORK")
            self.assertNotIn("CANARY", str(caught.exception))
            for method, code in (
                (guard.reject_env, "PROHIBITED_ENV"),
                (guard.reject_vault, "PROHIBITED_VAULT"),
                (guard.reject_real_root, "PROHIBITED_REAL_ROOT"),
                (guard.reject_network, "PROHIBITED_NETWORK"),
            ):
                with self.assertRaises(GuardError) as caught:
                    method("injected", payload="CANARY-SECRET-FIXTURE-ONLY")
                self.assertEqual(str(caught.exception), code)
                self.assertNotIn("CANARY", str(caught.exception))
                self.assertEqual(trace, [])
        finally:
            guard.deactivate()
        self.assertIs(socket.socket, original)

    def test_cli_fixture_only_pin_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "receipt.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "research.event_replay_v1.fixture_cli",
                    "--fixture-only",
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--receipt",
                    str(receipt),
                    "--workers",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("CANARY-", proc.stdout)
            self.assertNotIn("CANARY-", proc.stderr)
            body = receipt.read_text(encoding="utf-8")
            self.assertNotIn("CANARY-", body)
            self.assertLessEqual(receipt.stat().st_size, MAX_RECEIPT_BYTES)
            payload = json.loads(body)
            self.assertEqual(payload["compatibility"], "BLOCKED")
            self.assertEqual(payload["live"], "OFF")
            self.assertIsNone(payload["head"])
            self.assertEqual(payload["authoritative_base"], AUTHORITATIVE_BASE)
            self.assertEqual(payload["result"]["accounting"]["trading_fee"], "0")
            self.assertIsNone(payload["result"]["accounting"]["funding_charges"]["amount"])
            self.assertFalse(payload["research_complete"])
            rejected = subprocess.run(
                [sys.executable, "-m", "research.event_replay_v1.fixture_cli", "--real-input"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("REAL_INPUT_MODE_REJECTED", rejected.stderr)
            tampered = Path(tmp) / "tampered.json"
            tampered.write_text(FIXTURE_PATH.read_text(encoding="utf-8").replace("S0", "S0X", 1), encoding="utf-8")
            mismatch = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "research.event_replay_v1.fixture_cli",
                    "--fixture-only",
                    "--fixture",
                    str(tampered),
                    "--receipt",
                    str(Path(tmp) / "no.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("FIXTURE_PIN_MISMATCH", mismatch.stderr)
            self.assertNotIn("CANARY-", mismatch.stderr)
            self.assertNotIn("CANARY-", mismatch.stdout)

    def test_lock_pins_bytes_imports_and_runtime(self):
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(lock["third_party"], [])
        self.assertEqual(lock["authoritative_base"], AUTHORITATIVE_BASE)
        self.assertIsNone(lock["head"])
        self.assertEqual(lock["entrypoint"], "research/event_replay_v1/fixture_cli.py")
        self.assertEqual(lock["entrypoint_args"], ["--fixture-only"])
        self.assertEqual(lock["runtime"]["workers_cap"], 2)
        self.assertEqual(lock["runtime"]["rss_cap_bytes"], 512 * 1024 * 1024)
        self.assertEqual(lock["runtime"]["command_cap_seconds"], 120)
        self.assertEqual(lock["runtime"]["suite_cap_seconds"], 600)
        self.assertEqual(lock["runtime"]["receipt_cap_bytes"], 8 * 1024 * 1024)
        self.assertEqual(lock["runtime"]["events_cap"], 2048)
        self.assertEqual(lock["compatibility"], "BLOCKED")
        self.assertIsNone(lock["approved_stage_a_implementation_pin"])
        allowed = set(lock["imports_allow"])
        for relative in EXPECTED_FILES:
            if not relative.endswith(".py"):
                continue
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    self.assertIn(node.module.split(".")[0], allowed)
            if relative.endswith("guards.py") or relative.endswith("test_acceptance.py"):
                continue
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import socket", text)
            self.assertNotIn("import requests", text)
            self.assertNotIn("paper_fill", text)
        for relative, meta in lock["files"].items():
            data = (ROOT / relative).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"])
            self.assertEqual(len(data), meta["bytes"])
        self.assertNotIn("research/event_replay_v1/dependencies.lock.json", lock["files"])
        digest = hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
