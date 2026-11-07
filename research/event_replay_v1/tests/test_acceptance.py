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
import os
import resource
import signal
import socket
import subprocess
import sys
import time
import unittest
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VAS_BYTES = 268435456
COMMAND_SECONDS = 120
SUITE_SECONDS = 600


def _install_vas() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (VAS_BYTES, VAS_BYTES))


def _parent_main() -> int:
    sys.dont_write_bytecode = True
    _install_vas()
    from research.event_replay_v1.guards import (
        MAX_AGGREGATE_BYTES,
        MAX_RECEIPT_BYTES,
        ByteSource,
        CapStop,
        OutputLedger,
        ProcessGate,
        ChunkPump,
        emit_receipt,
        install_entry_containment,
    )

    receipt = Path("/opt/cursor/artifacts/b992-v5-suite-receipt.json")
    head = None
    base = "40087a65f2157f8c6701ea3670adc5c29a28c87d"
    clean = None
    args = sys.argv[1:]
    index = 0
    while index < len(args):
        if args[index] == "--receipt" and index + 1 < len(args):
            receipt = Path(args[index + 1])
            index += 2
            continue
        if args[index] == "--head" and index + 1 < len(args):
            head = args[index + 1]
            index += 2
            continue
        if args[index] == "--base" and index + 1 < len(args):
            base = args[index + 1]
            index += 2
            continue
        if args[index] == "--clean-tree" and index + 1 < len(args):
            clean = args[index + 1]
            index += 2
            continue
        index += 1
    allowed_root = Path("/opt/cursor/artifacts").resolve()
    if not str(receipt.resolve()).startswith(str(allowed_root) + "/"):
        print("PROHIBITED_REAL_ROOT", file=sys.stderr)
        return 3
    if not receipt.parent.is_dir():
        print("PROHIBITED_REAL_ROOT", file=sys.stderr)
        return 3
    install_entry_containment((str(receipt.resolve()),))
    gate = ProcessGate()
    ledger = OutputLedger()
    lock_bytes = (ROOT / "research/event_replay_v1/dependencies.lock.json").read_bytes()
    external_lock_sha = hashlib.sha256(lock_bytes).hexdigest()
    suite_started = time.monotonic()
    proofs = []

    def spawn(argv: list[str], deadline: float) -> tuple[int, bytes, bytes, dict]:
        gate.acquire()
        started = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            env={},
            preexec_fn=_install_vas,
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.stdin is not None:
            proc.stdin.close()
        stopped = False
        overshoot = 0.0
        out_pump = ChunkPump(proc.stdout.fileno(), ledger) if proc.stdout is not None else None
        err_pump = ChunkPump(proc.stderr.fileno(), ledger) if proc.stderr is not None else None
        try:
            while True:
                if proc.poll() is not None and (out_pump is None or out_pump.eof) and (err_pump is None or err_pump.eof):
                    break
                if (time.monotonic() - started > deadline or time.monotonic() - suite_started > SUITE_SECONDS) and not stopped:
                    kill_at = time.monotonic()
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    stopped = True
                    overshoot = time.monotonic() - kill_at
                if out_pump is not None and not out_pump.eof:
                    out_pump.pull()
                if err_pump is not None and not err_pump.eof:
                    err_pump.pull()
                if stopped and (out_pump is None or out_pump.eof) and (err_pump is None or err_pump.eof):
                    break
                if (out_pump is None or not out_pump.progress) and (err_pump is None or not err_pump.progress):
                    time.sleep(0.01)
            out = out_pump.value() if out_pump is not None else b""
            err = err_pump.value() if err_pump is not None else b""
        finally:
            gate.release()
        return proc.returncode if proc.returncode is not None else -9, out, err, {
            "metric": "command_wall_seconds",
            "mechanism": "parent_monotonic_killpg",
            "deadline": deadline,
            "elapsed": time.monotonic() - started,
            "overshoot": overshoot,
            "stopped": stopped,
        }

    memory_code = (
        "import resource\n"
        "resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))\n"
        "blocks=[]\n"
        "try:\n"
        "    while True:\n"
        "        blocks.append(bytearray(8000000))\n"
        "except MemoryError:\n"
        "    print('MEMORY_STOP')\n"
    )
    code, out, err, wall = spawn([sys.executable, "-I", "-S", "-c", memory_code], 30)
    proofs.append({"fault": "vas_allocation", "returncode": code, "stdout": out.decode("utf-8", "replace")[:200], "wall": wall})
    sleeper = "import time\ntime.sleep(30)\nprint('SLEPT')\n"
    code, out, err, wall = spawn([sys.executable, "-I", "-S", "-c", sleeper], 0.4)
    proofs.append({"fault": "wall_deadline", "returncode": code, "saw_slept": b"SLEPT" in out, "wall": wall})
    bootstrap = (
        "import sys\n"
        "sys.dont_write_bytecode = True\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "sys.argv = [sys.argv[2], '--suite-child']\n"
        "import runpy\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    code, out, err, wall = spawn(
        [sys.executable, "-I", "-S", "-c", bootstrap, str(ROOT), str(Path(__file__).resolve())],
        COMMAND_SECONDS,
    )
    body = {
        "kind": "event_replay_v1.suite_receipt",
        "compatibility": "BLOCKED",
        "authoritative_base": base,
        "head": head,
        "head_source": "EXTERNAL_ARGV_NOT_SELF_HASH",
        "clean_tree": clean,
        "command": "python research/event_replay_v1/tests/test_acceptance.py",
        "interpreter": sys.executable,
        "runtime": sys.version.split()[0],
        "external_lock_sha256": external_lock_sha,
        "suite_wall": wall,
        "cap_stop_proofs": proofs,
        "child_returncode": code,
        "aggregate_bytes": ledger.aggregate_used,
        "aggregate_cap_bytes": MAX_AGGREGATE_BYTES,
        "receipt_cap_bytes": MAX_RECEIPT_BYTES,
        "child_stderr": ByteSource(err),
        "child_stdout": ByteSource(out),
    }
    try:
        with receipt.open("wb") as handle:
            emit_receipt(ledger, handle, body, sort_keys=False)
    except CapStop as exc:
        print(f"CAP_STOP:{exc.code}", file=sys.stderr)
        return 2
    sys.stderr.buffer.write(err)
    return code if code is not None else 1


if __name__ == "__main__" and "--suite-child" not in sys.argv:
    raise SystemExit(_parent_main())

if "--suite-child" in sys.argv:
    sys.dont_write_bytecode = True
    _install_vas()
    from research.event_replay_v1.guards import install_entry_containment

    install_entry_containment(())

from research.event_replay_v1.accounting import FeeDispositionError
from research.event_replay_v1.engine import FixtureError, replay
from research.event_replay_v1.guards import (
    MAX_COMMAND_SECONDS,
    MAX_EVENTS,
    MAX_RECEIPT_BYTES,
    MAX_RSS_BOUND_BYTES,
    MAX_SUITE_SECONDS,
    MAX_WORKERS,
    MAX_AGGREGATE_BYTES,
    VAS_BYTES,
    CapStop,
    GuardError,
    OuterInterception,
    OutputLedger,
    ProcessGate,
    ResourceLimits,
    ResourceObservation,
    enforce_observation,
    install_env_trap,
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
        self.assertIsNone(accounting["net_pnl"])
        self.assertIsNone(accounting["net_per_signal"])
        self.assertEqual(accounting["slippage_only_stress"]["status"], "EMITTED")
        self.assertFalse(accounting["slippage_only_stress"]["charged_in_net"])

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
                        "event_id": "s0-late-trade",
                        "type": "trade",
                        "time_us": 4600,
                        "seq": 23,
                        "price": "1",
                        "qty": "9",
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
        self.assertIsNone(self.scenario("S-THESIS")["exits"][0]["observed_price"])
        hands = (
            ("HARD_STOP", "S11", "80", "10", "s11", "-200"),
            ("LIQUIDATION", "S12", "99", "10", "s12", "-10"),
            ("LIQUIDATION", "S13", "101", "10", "s13", "-10"),
            ("THESIS_CUT", "S-THESIS", "100", "1", "s-thesis", "0"),
            ("LADDER", "S9", "139", "1", "s9", "39"),
            ("BREAKEVEN", "S10", "79", "1", "s10", "-21"),
            ("TRAIL", "S-TRAIL", "100", "1", "s-trail", "0"),
            ("GIVEBACK", "S-GIVEBACK", "100", "1", "s-giveback", "0"),
            ("TARGET", "S-TARGET", "100", "1", "s-target", "0"),
            ("TIME", "S-TIME", "100", "1", "s-time", "0"),
        )
        for name, scenario_id, price, qty, signal_id, pnl in hands:
            oracle = f"oracle:protection:{name}"
            fill = self.fills(scenario_id, "EXIT")[0]
            self.assertEqual(fill["price"], price, oracle)
            self.assertEqual(fill["qty"], qty, oracle)
            self.assertEqual(self.signals[signal_id]["realized_pnl"], pnl, oracle)
        pending = self.scenario("S-PENDING")["exits"][0]
        self.assertEqual(pending["winner"], "HARD_STOP", "oracle:pending-cross")
        self.assertEqual(pending["superseded_from"], "TARGET", "oracle:pending-cross")
        pending_fill = self.fills("S-PENDING", "EXIT")[0]
        self.assertEqual(pending_fill["price"], "70", "oracle:pending-cross")
        self.assertEqual(pending_fill["qty"], "1", "oracle:pending-cross")
        self.assertEqual(self.signals["s-pending"]["realized_pnl"], "-30", "oracle:pending-cross")
        cancel = next(order for order in self.scenario("S7")["orders"] if order["order_id"] == "s7:entry")
        self.assertEqual(cancel["filled_qty"], "4", "oracle:cancel-conserve")
        self.assertEqual(cancel["cancelled_qty"], "6", "oracle:cancel-conserve")
        self.assertEqual(cancel["original_qty"], "10", "oracle:cancel-conserve")
        cancel_fill = self.fills("S7", "ENTRY")[0]
        self.assertEqual(cancel_fill["price"], "50", "oracle:cancel-conserve")
        self.assertEqual(cancel_fill["qty"], "4", "oracle:cancel-conserve")
        a = next(order for order in self.scenario("S-C3")["orders"] if order["order_id"] == "c3-a:entry")
        b = next(order for order in self.scenario("S-C3")["orders"] if order["order_id"] == "c3-b:entry")
        self.assertEqual(a["filled_qty"], "2", "oracle:cancel-conserve")
        self.assertEqual(b["filled_qty"], "1", "oracle:cancel-conserve")
        self.assertEqual(Decimal(a["filled_qty"]) + Decimal(b["filled_qty"]), Decimal(3), "oracle:cancel-conserve")

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
            "c3-a": "0",
            "c3-b": "0",
            "s-pending": "-30",
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
            + (Decimal(70) - Decimal(100))
        )
        self.assertEqual(hand, Decimal("-174"))
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
            + (Decimal(90) - Decimal(70)) * 1
        )
        self.assertEqual(depth, Decimal(2))
        self.assertEqual(delay, Decimal("176"))
        accounting = self.result["accounting"]
        self.assertIsNone(accounting["net_pnl"])
        self.assertIsNone(accounting["net_per_signal"])
        self.assertEqual(accounting["aggregate_status"], "INCOMPLETE")
        self.assertEqual(Decimal(accounting["known_realized_subtotal"]), numerator)
        self.assertGreaterEqual(len(self.signals), 29)
        self.assertEqual(len(accounting["missed_signal_ids"]), 9)
        self.assertIn("s9", accounting["unresolved_signal_ids"])
        self.assertIn("s14", accounting["unresolved_signal_ids"])
        self.assertEqual(accounting["slippage_only_stress"]["charged_in_net"], False)
        self.assertEqual(Decimal(accounting["synthetic_depth_slippage_memo"]), depth)
        self.assertEqual(Decimal(accounting["synthetic_delay_gap_memo"]), delay)

    def test_named_faults_fail_at_named_assertions(self):
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
            (
                "fill_at_trigger_price_trail",
                "TRAIL fill stays at the book price 100, not the trigger",
                lambda result: self.assertEqual(
                    [row["price"] for row in result["scenarios"]["S-TRAIL"]["fills"] if row["role"] == "EXIT"],
                    ["100"],
                ),
            ),
        ]
        import research.event_replay_v1.pricer as pricer_mod
        import research.event_replay_v1.risk as risk_mod

        def run_fault(name, replacement, target, assertion_id, check):
            with patch(target, replacement):
                faulty = replay(self.fixture, workers=1)
            check(self.result)
            with self.assertRaises(AssertionError) as caught:
                check(faulty)
            self.assertIn(assertion_id, str(caught.exception))
            return {"fault": name, "failed_assertion": assertion_id, "failed": True}

        real_select = pricer_mod.select_book
        real_size = risk_mod.size_decision
        real_delay = pricer_mod.cancel_ack_delay_us
        real_alloc = pricer_mod.allocate_resting
        real_walk = pricer_mod.exit_walk
        real_den = pricer_mod.signal_denominator

        def leak(books, time_us, *, admit_future=False):
            return real_select(books, time_us, admit_future=True)

        def zero_delay(_spec):
            return 0

        def omit(*args, **kwargs):
            allocated = real_alloc(*args, **kwargs)
            allocated["fill_qty"] = Decimal(0)
            allocated["trade_remaining"] = args[4]
            return allocated

        def at_trigger(levels, qty, trigger):
            price = trigger if trigger is not None else levels[0][0]
            return {
                "parts": [{"price": str(price), "qty": str(qty)}],
                "filled": qty,
                "residual": Decimal(0),
                "notional": price * qty,
                "vwap": price,
                "touch": price,
            }

        def round_up(qty, min_qty, *, increase_size=False):
            return real_size(qty, min_qty, increase_size=True)

        def drop(signals):
            return real_den([row for row in signals if not row["is_miss"]])

        def oracle_future(result):
            self.assertEqual(
                [row["price"] for row in result["scenarios"]["S0"]["fills"] if row["role"] == "ENTRY"],
                ["100"],
                "oracle:future-book",
            )

        def oracle_partial(result):
            self.assertEqual(
                next(order["filled_qty"] for order in result["scenarios"]["S7"]["orders"] if order["order_id"] == "s7:entry"),
                "4",
                "oracle:partial-before-ack",
            )

        def oracle_stop(result):
            self.assertEqual(
                [row["price"] for row in result["scenarios"]["S11"]["fills"] if row["role"] == "EXIT"],
                ["80"],
                "oracle:stop-book-price",
            )

        def oracle_size(result):
            self.assertEqual(
                next(row["status"] for row in result["accounting"]["signals"] if row["signal_id"] == "s15"),
                "MIN_SIZE",
                "oracle:no-round-up",
            )

        def oracle_den(result):
            self.assertEqual(result["accounting"]["denominator"], str(len(self.signals)), "oracle:miss-denominator")

        def oracle_trail(result):
            self.assertEqual(
                [row["price"] for row in result["scenarios"]["S-TRAIL"]["fills"] if row["role"] == "EXIT"],
                ["100"],
                "oracle:protection:TRAIL",
            )

        receipts = [
            run_fault("admit_future_books", leak, "research.event_replay_v1.engine.select_book", "oracle:future-book", oracle_future),
            run_fault("instant_cancels", zero_delay, "research.event_replay_v1.engine.cancel_ack_delay_us", "oracle:partial-before-ack", oracle_partial),
            run_fault("omit_partials", omit, "research.event_replay_v1.engine.allocate_resting", "oracle:partial-before-ack", oracle_partial),
            run_fault("fill_at_trigger_price", at_trigger, "research.event_replay_v1.engine.exit_walk", "oracle:stop-book-price", oracle_stop),
            run_fault("increase_size", round_up, "research.event_replay_v1.engine.size_decision", "oracle:no-round-up", oracle_size),
            run_fault("drop_misses", drop, "research.event_replay_v1.pricer.signal_denominator", "oracle:miss-denominator", oracle_den),
            run_fault("fill_at_trigger_price_trail", at_trigger, "research.event_replay_v1.engine.exit_walk", "oracle:protection:TRAIL", oracle_trail),
        ]
        self.assertEqual([item["fault"] for item in receipts], [item[0] for item in checks])
        blob = json.dumps(receipts)
        self.assertNotIn("CANARY-", blob)

    def test_resource_caps_stop_and_this_run_is_inside_them(self):
        self.assertEqual(MAX_WORKERS, 2)
        self.assertEqual(MAX_RSS_BOUND_BYTES, 512 * 1024 * 1024)
        self.assertEqual(MAX_COMMAND_SECONDS, 120)
        self.assertEqual(MAX_SUITE_SECONDS, 600)
        self.assertEqual(MAX_RECEIPT_BYTES, 8 * 1024 * 1024)
        self.assertEqual(MAX_EVENTS, 2048)
        probes = [
            (ResourceObservation(events=2049), "events"),
            (ResourceObservation(rss_bytes=MAX_RSS_BOUND_BYTES + 1), "rss"),
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
            rss_bytes=MAX_RSS_BOUND_BYTES,
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
        self.assertNotIn("enforced", resource)
        self.assertTrue(resource["checks"])
        self.assertEqual(resource["workers"], 1)
        self.assertLessEqual(resource["workers"], MAX_WORKERS)
        self.assertLessEqual(resource["rss_bytes"], MAX_RSS_BOUND_BYTES)
        self.assertLessEqual(resource["command_seconds"], MAX_COMMAND_SECONDS)
        self.assertLessEqual(resource["events"], MAX_EVENTS)
        self.assertEqual(resource["events"], sum(len(item["events"]) for item in self.fixture["scenarios"]))
        self.assertGreater(resource["rss_bytes"], 0)

    def test_prohibited_access_rejects_before_side_effects(self):
        outer = OuterInterception()
        original = socket.socket
        outer.install()
        try:
            with self.assertRaises(GuardError) as caught:
                socket.socket()
            self.assertEqual(str(caught.exception), "PROHIBITED_NETWORK")
            self.assertNotIn("CANARY", str(caught.exception))
            with self.assertRaises(GuardError):
                socket.getaddrinfo("127.0.0.1", 9)
            self.assertEqual(outer.refusals[-1], "PROHIBITED_NETWORK")
        finally:
            socket.socket = original
            socket.create_connection = outer._saved["create_connection"]
            socket.getaddrinfo = outer._saved["getaddrinfo"]
        saved_env = os.environ
        saved_getenv = os.getenv
        try:
            install_env_trap()
            with self.assertRaises(GuardError) as caught:
                os.environ.get("SYNTHETIC_ENV_KEY")
            self.assertEqual(str(caught.exception), "PROHIBITED_ENV")
            self.assertNotIn("CANARY", str(caught.exception))
        finally:
            os.environ = saved_env
            os.getenv = saved_getenv
        ledger = OutputLedger(aggregate_cap=32, receipt_cap=16)
        ledger.reserve_pipe(8, "pipe")
        ledger.reserve_receipt(8)
        with self.assertRaises(CapStop):
            ledger.reserve_receipt(16)
        gate = ProcessGate()
        gate.acquire()
        with self.assertRaises(CapStop) as caught:
            gate.acquire()
        self.assertEqual(str(caught.exception), "CAP_STOP:workers")
        gate.release()

    def test_cli_fixture_only_pin_and_receipt(self):
        from research.event_replay_v1.fixture_cli import run_inprocess

        payload = run_inprocess(["--fixture-only", "--fixture", str(FIXTURE_PATH), "--workers", "1"])
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["compatibility"], "BLOCKED")
        self.assertEqual(payload["result"]["accounting"]["trading_fee"], "0")
        self.assertIsNone(payload["result"]["accounting"]["funding_charges"]["amount"])
        self.assertIsNone(payload["result"]["accounting"]["net_pnl"])
        blob = json.dumps(payload)
        self.assertNotIn("CANARY-", blob)
        with self.assertRaises(RuntimeError) as caught:
            run_inprocess(["--real-input"])
        self.assertIn("REAL_INPUT", str(caught.exception))
        self.assertEqual(VAS_BYTES, 268435456)
        self.assertEqual(MAX_AGGREGATE_BYTES, 16 * 1024 * 1024)

    def test_lock_pins_bytes_imports_and_runtime(self):
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(lock["third_party"], [])
        self.assertEqual(lock["authoritative_base"], AUTHORITATIVE_BASE)
        self.assertIsNone(lock["head"])
        self.assertEqual(lock["entrypoint"], "research/event_replay_v1/fixture_cli.py")
        self.assertEqual(lock["entrypoint_args"], ["--fixture-only"])
        self.assertEqual(lock["runtime"]["workers_cap"], 2)
        self.assertEqual(lock["runtime"]["rss_bound_bytes"], 512 * 1024 * 1024)
        self.assertEqual(lock["runtime"]["vas_bytes_per_process"], 268435456)
        self.assertEqual(lock["runtime"]["aggregate_cap_bytes"], 16 * 1024 * 1024)
        self.assertIsNone(lock["runtime"]["cpu_seconds_cap"])
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

    def test_suite_admit_before_read(self):
        from research.event_replay_v1.guards import CapStop, OutputLedger, read_chunked, read_framed

        class Stream:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload
                self.pos = 0
                self.reads: list[int] = []

            def read(self, nbytes: int) -> bytes:
                self.reads.append(nbytes)
                chunk = self.payload[self.pos:self.pos + nbytes]
                self.pos += len(chunk)
                return chunk

        control = Stream(b"hello")
        ledger = OutputLedger(aggregate_cap=64, receipt_cap=32)
        before = ledger.aggregate_used
        got = read_chunked(control, ledger, chunk=8)
        self.assertEqual(got, b"hello", "oracle:suite-admit-before-read")
        self.assertEqual(ledger.aggregate_used - before, control.pos, "oracle:suite-admit-before-read")
        self.assertEqual(control.reads[0], 8, "oracle:suite-admit-before-read")
        framed_body = b"ok"
        framed = Stream(len(framed_body).to_bytes(4, "big") + framed_body)
        framed_ledger = OutputLedger(aggregate_cap=64, receipt_cap=32)
        before = framed_ledger.aggregate_used
        frames = read_framed(framed, framed_ledger)
        self.assertEqual(frames, [framed_body], "oracle:suite-admit-before-read")
        self.assertEqual(framed_ledger.aggregate_used - before, framed.pos, "oracle:suite-admit-before-read")
        fault = Stream((1000).to_bytes(4, "big") + b"X" * 1000)
        fault_ledger = OutputLedger(aggregate_cap=32, receipt_cap=16)
        with self.assertRaises(CapStop) as caught:
            read_framed(fault, fault_ledger)
        self.assertEqual(str(caught.exception), "CAP_STOP:aggregate", "oracle:suite-admit-before-read")
        self.assertEqual(fault.reads, [4], "oracle:suite-admit-before-read")
        self.assertEqual(fault.pos, 4, "oracle:suite-admit-before-read")

    def test_receipt_reserve_before_build(self):
        from research.event_replay_v1.guards import ByteSource, CapStop, OutputLedger, ReceiptWriter

        class Watch:
            def __init__(self, ledger: OutputLedger) -> None:
                self.ledger = ledger
                self.buf = bytearray()

            def write(self, data: bytes) -> None:
                before = self.ledger.receipt_used
                self.buf.extend(data)
                self.assert_unchanged = before == self.ledger.receipt_used

        # 6 * 2 source bytes is 12, which does not fit in the room left after the
        # opening quote. The exact ASCII length does, so the charge is the exact length.
        ledger = OutputLedger(aggregate_cap=64, receipt_cap=4)
        watch = Watch(ledger)
        writer = ReceiptWriter(ledger, watch)
        writer.emit(ByteSource(b"AB"))
        self.assertTrue(watch.assert_unchanged, "oracle:receipt-prewrite-not-a-second-charge")
        self.assertEqual(bytes(watch.buf), b'"AB"', "oracle:receipt-reserve-before-build")
        self.assertEqual(ledger.receipt_used, 4, "oracle:receipt-reserve-before-build")
        one = OutputLedger(aggregate_cap=8, receipt_cap=1)
        one_watch = Watch(one)
        one_writer = ReceiptWriter(one, one_watch)
        one_writer.write_byte_content(b"A")
        self.assertEqual(bytes(one_watch.buf), b"A", "oracle:receipt-reserve-before-build")
        self.assertEqual(one.receipt_used, 1, "oracle:receipt-reserve-before-build")
        fault = OutputLedger(aggregate_cap=8, receipt_cap=5)
        fault_watch = Watch(fault)
        fault_writer = ReceiptWriter(fault, fault_watch)
        fault_writer.write_exact(b'"')
        before_scratch = len(fault_writer.scratch_allocations)
        with self.assertRaises(CapStop) as caught:
            fault_writer.write_byte_content(b"\x01")
        self.assertEqual(str(caught.exception), "CAP_STOP:receipt", "oracle:receipt-reserve-before-build")
        self.assertEqual(len(fault_writer.scratch_allocations), before_scratch, "oracle:receipt-reserve-before-build")
        self.assertNotIn(b"\\u0001", bytes(fault_watch.buf), "oracle:receipt-reserve-before-build")
        emoji = OutputLedger(aggregate_cap=64, receipt_cap=14)
        emoji_watch = Watch(emoji)
        ReceiptWriter(emoji, emoji_watch).emit("\U0001F600")
        self.assertEqual(emoji.receipt_used, 14, "oracle:receipt-reserve-before-build")
        self.assertIn(b"\\ud83d\\ude00", bytes(emoji_watch.buf), "oracle:receipt-reserve-before-build")

    def test_outer_refuses_when_inner_guard_off(self):
        from research.event_replay_v1.guards import ACTIVE_OUTER, INNER_GUARD

        self.assertIsNotNone(ACTIVE_OUTER, "oracle:outer-socket-after-inner-off")
        self.assertTrue(ACTIVE_OUTER.installed, "oracle:outer-socket-after-inner-off")
        saved = INNER_GUARD.active
        try:
            INNER_GUARD.active = False
            with self.assertRaises(GuardError) as caught:
                socket.socket()
            self.assertEqual(str(caught.exception), "PROHIBITED_NETWORK", "oracle:outer-socket-after-inner-off")
            with self.assertRaises(GuardError):
                socket.getaddrinfo("127.0.0.1", 9)
            self.assertIn("PROHIBITED_NETWORK", ACTIVE_OUTER.refusals, "oracle:outer-socket-after-inner-off")
            with self.assertRaises(GuardError) as caught:
                open("/tmp/b992-synthetic-outer-write", "w", encoding="utf-8")
            self.assertEqual(str(caught.exception), "PROHIBITED_WRITE", "oracle:outer-write-after-inner-off")
            self.assertIn("PROHIBITED_WRITE", ACTIVE_OUTER.refusals, "oracle:outer-write-after-inner-off")
            self.assertFalse(Path("/tmp/b992-synthetic-outer-write").exists(), "oracle:outer-write-after-inner-off")
        finally:
            INNER_GUARD.active = saved
        INNER_GUARD.active = True
        with self.assertRaises(GuardError) as caught:
            socket.socket()
        self.assertEqual(str(caught.exception), "PROHIBITED_NETWORK", "oracle:outer-socket-after-inner-off")
        with self.assertRaises(GuardError) as caught:
            open("/tmp/b992-synthetic-outer-write-control", "w", encoding="utf-8")
        self.assertEqual(str(caught.exception), "PROHIBITED_WRITE", "oracle:outer-write-after-inner-off")


if __name__ == "__main__":
    sys.argv = [sys.argv[0]]
    program = unittest.main(verbosity=2, exit=False)
    from research.event_replay_v1.engine import verify_loaded_pins

    loaded, lock_sha = verify_loaded_pins()
    print("LOADED_PINS " + json.dumps({"count": len(loaded), "external_lock_sha256": lock_sha, "loaded": loaded}))
    raise SystemExit(0 if program.result.wasSuccessful() else 1)
