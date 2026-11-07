"""Handwritten acceptance oracle and mutation kills for the Stage A fixture.

The expected strategy ids below are a literal. They are not produced by
production enumeration helpers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import resource
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.causal_grammar_v1 import asof
from research.causal_grammar_v1 import enumerate as fixture_enumerate
from research.causal_grammar_v1 import features as features_mod
from research.causal_grammar_v1 import fixture_cli
from research.causal_grammar_v1 import grammar
from research.causal_grammar_v1 import guards
from research.causal_grammar_v1 import normalized_adapter as adapter
from research.causal_grammar_v1.canonical import canonicalize_members
from research.causal_grammar_v1.contracts_v1 import (
    AUTHORIZED_BASE,
    NORMALIZED_REQUIRED,
    PAYLOAD_FIELDS,
    POLICY_KEYS,
    ContractError,
)

PKG = Path(__file__).resolve().parents[1]
SUITE_STARTED = time.perf_counter()
MUTATIONS: list[dict] = []
CANARY = "CANARY-SECRET-FIXTURE-9f3a"

# Handwritten id-set oracle. Do not derive this from enumerate_fixture.
HANDWRITTEN_IDS = (
    "S:A",
    "S:B",
    "S:C",
    "U:A+B",
    "U:A+C",
    "U:B+C",
    "U:A+B+C",
    "O:A>B",
    "O:B>A",
    "O:A>C",
    "O:C>A",
    "O:B>C",
    "O:C>B",
)

HANDWRITTEN_PAYLOAD_FIELDS = (
    "member_id",
    "record_index",
    "series_id",
    "source_timestamp_ms",
    "publication_time_ms",
    "bar_close_time_ms",
    "receive_time_ms",
    "receive_monotonic_ns",
    "connection_epoch",
    "release_status",
    "revision_of",
    "value",
    "unit",
    "book_valid_from_ms",
    "book_valid_to_ms",
)

_DEFAULT = object()


def oracle_canonical(obj: object) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def expect_killed(fault_id: str, fault: str, assertion_name: str, fn) -> None:
    try:
        fn()
    except AssertionError:
        MUTATIONS.append(
            {
                "receipt_schema": "causal_grammar.mutation_kill.v1",
                "fault_id": fault_id,
                "deliberate_fault": fault,
                "failed_assertion": assertion_name,
                "killed": True,
                "exception_type": "AssertionError",
            }
        )
        return
    raise AssertionError("MUTATION_SURVIVED:" + fault_id)


def row(
    member_id: str,
    record_index: int,
    series_id: str,
    value: int,
    when_ms: int,
    mono: int,
    *,
    release: str = "first",
    revision_of: str | None = None,
    source: object = _DEFAULT,
    pub: object = _DEFAULT,
    bar: object = _DEFAULT,
    recv: object = _DEFAULT,
    book_from: object = _DEFAULT,
    book_to: int | None = 100000,
) -> dict:
    def pick(explicit: object) -> object:
        return when_ms if explicit is _DEFAULT else explicit

    return {
        "member_id": member_id,
        "record_index": record_index,
        "series_id": series_id,
        "source_timestamp_ms": pick(source),
        "publication_time_ms": pick(pub),
        "bar_close_time_ms": pick(bar),
        "receive_time_ms": pick(recv),
        "receive_monotonic_ns": mono,
        "connection_epoch": "fx-epoch-1",
        "release_status": release,
        "revision_of": revision_of,
        "value": value,
        "unit": "fixture_unit",
        "book_valid_from_ms": when_ms if book_from is _DEFAULT else book_from,
        "book_valid_to_ms": book_to,
    }


def pinned_rows() -> list[dict]:
    return [
        row("m-a1", 0, "A", 0, 1000, 10),
        row("m-b1", 1, "B", 0, 1200, 20),
        row("m-c1", 2, "C", 1, 1500, 30),
        row("m-a2", 3, "A", 0, 1600, 40),
        row("m-b2", 4, "B", 0, 1700, 50),
        row("m-c2", 5, "C", 1, 1800, 60),
    ]


def build_manifest(rows: list[dict], manifest_id: str = "committed-events-v1") -> dict:
    segment_id = "fx-seg-1"
    parts: list[bytes] = []
    members: list[dict] = []
    offset = 0
    for item in rows:
        core = {key: item[key] for key in HANDWRITTEN_PAYLOAD_FIELDS}
        payload = oracle_canonical(core)
        start = offset
        end = offset + len(payload)
        offset = end
        parts.append(payload)
        member = dict(core)
        member["committed"] = True
        member["linkage"] = f"{segment_id}:{item['member_id']}:{item['record_index']}"
        member["byte_start"] = start
        member["byte_end"] = end
        member["member_digest"] = hashlib.sha256(payload).hexdigest()
        members.append(member)
    prefix = b"".join(parts)
    tail = b"UNCOMMITTED"
    segment_bytes = prefix + tail
    members.append(
        {
            "member_id": "m-tail",
            "record_index": -1,
            "series_id": "A",
            "source_timestamp_ms": None,
            "publication_time_ms": None,
            "bar_close_time_ms": None,
            "receive_time_ms": None,
            "receive_monotonic_ns": 0,
            "connection_epoch": "fx-epoch-1",
            "release_status": "first",
            "revision_of": None,
            "value": 0,
            "unit": "fixture_unit",
            "book_valid_from_ms": 0,
            "book_valid_to_ms": 1,
            "committed": False,
            "linkage": f"{segment_id}:m-tail:tail",
            "byte_start": len(prefix),
            "byte_end": len(segment_bytes),
            "member_digest": hashlib.sha256(tail).hexdigest(),
        }
    )
    return {
        "manifest_schema": "causal_grammar.manufactured_input.v1",
        "fixture_only": True,
        "compatibility": "NOT_ESTABLISHED",
        "label": "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED",
        "manifest_id": manifest_id,
        "producer_spec_references": {
            "auditor_coverage_report": "3272c6425b59",
            "collector": ["1439354678ef", "7bee784c7e13"],
            "implementation_pins": None,
            "compatibility_status": "BLOCKED",
        },
        "segment": {
            "segment_id": segment_id,
            "segment_bytes_b64": base64.b64encode(segment_bytes).decode("ascii"),
            "segment_digest": hashlib.sha256(segment_bytes).hexdigest(),
            "prefix_extent": len(prefix),
            "prefix_digest": hashlib.sha256(prefix).hexdigest(),
            "members": members,
        },
    }


def clone(obj: dict) -> dict:
    return json.loads(oracle_canonical(obj).decode("utf-8"))


def load_spec() -> dict:
    return json.loads((PKG / "fixtures" / "tiny-spec.json").read_text(encoding="utf-8"))


def compiled() -> list[dict]:
    return fixture_enumerate.enumerate_fixture(load_spec())


def normalized(rows: list[dict]) -> dict:
    return adapter.normalize_manifest(build_manifest(rows))


def signals_for(rows: list[dict]) -> tuple[list[dict], dict]:
    produced = normalized(rows)
    return grammar.evaluate_signals(compiled(), produced["records"]), produced


def ordered_fires(signals: list[dict], strategy_id: str | None = None) -> list[dict]:
    found = []
    for signal in signals:
        if signal["signal"] != "ORDERED_FIRE":
            continue
        if strategy_id is not None and signal["strategy_id"] != strategy_id:
            continue
        found.append(signal)
    return found


def signal_key(signal: dict) -> tuple:
    evidence = signal["evidence"]
    return (
        signal["strategy_id"],
        signal["decision_ms"],
        signal["signal"],
        signal["pure_interaction"],
        signal["marginal_outcome"],
        evidence["linkage"],
        evidence["antecedent_linkage"],
        evidence["consequent_linkage"],
        evidence["delay_ms"],
        tuple(evidence["linkages"]),
    )


def link(member: str, index: int) -> str:
    return f"fx-seg-1:{member}:{index}"


def expected_pinned_signals() -> list[tuple]:
    a1, b1, c1 = link("m-a1", 0), link("m-b1", 1), link("m-c1", 2)
    a2, b2, c2 = link("m-a2", 3), link("m-b2", 4), link("m-c2", 5)

    def single(strategy_id: str, decision: int, code: str, outcome: str, linkage: str) -> tuple:
        return (strategy_id, decision, code, None, outcome, linkage, None, None, None, ())

    def ordered(strategy_id: str, decision: int, antecedent: str, consequent: str, delay: int) -> tuple:
        return (strategy_id, decision, "ORDERED_FIRE", None, None, None, antecedent, consequent, delay, ())

    def conj(strategy_id: str, decision: int, pure: bool, linkages: list[str]) -> tuple:
        return (strategy_id, decision, "CONJUNCTION", pure, None, None, None, None, None, tuple(linkages))

    return [
        single("S:A", 1000, "FLAT", "unsuccessful", a1),
        single("S:A", 1600, "FLAT", "unsuccessful", a2),
        single("S:B", 1200, "FLAT", "unsuccessful", b1),
        single("S:B", 1700, "FLAT", "unsuccessful", b2),
        single("S:C", 1500, "LONG", "successful", c1),
        single("S:C", 1800, "LONG", "successful", c2),
        ordered("O:A>B", 1200, a1, b1, 200),
        ordered("O:A>B", 1700, a2, b2, 100),
        ordered("O:A>C", 1500, a1, c1, 500),
        ordered("O:A>C", 1800, a2, c2, 200),
        ordered("O:B>A", 1600, b1, a2, 400),
        ordered("O:B>C", 1500, b1, c1, 300),
        ordered("O:B>C", 1800, b2, c2, 100),
        ordered("O:C>A", 1600, c1, a2, 100),
        ordered("O:C>B", 1700, c1, b2, 200),
        conj("U:A+B", 1200, True, [a1, b1]),
        conj("U:A+C", 1500, False, [a1, c1]),
        conj("U:A+B+C", 1500, False, [a1, b1, c1]),
        conj("U:B+C", 1500, False, [b1, c1]),
    ]


def view_through(rows: list[dict], cutoff_ms: int = 1500) -> dict:
    produced = normalized(rows)
    spec = load_spec()
    feature_rows = features_mod.features_at(produced["records"], spec["feature"])
    signal_rows = grammar.evaluate_signals(compiled(), produced["records"])
    return {
        "records": [
            record
            for record in produced["records"]
            if record["availability_ms"] is not None and record["availability_ms"] <= cutoff_ms
        ],
        "features": [item for item in feature_rows if item["cutoff_ms"] <= cutoff_ms],
        "signals": [item for item in signal_rows if item["decision_ms"] <= cutoff_ms],
        "rejections": produced["rejections"],
    }


def perturbed_rows() -> list[dict]:
    rows = pinned_rows()
    rows[3]["value"] = 4
    rows[4]["value"] = 5
    rows[5]["value"] = 6
    rows.append(
        row(
            "m-a1r",
            6,
            "A",
            50,
            9000,
            90,
            release="revision",
            revision_of=link("m-a1", 0),
            source=1000,
            pub=9000,
            bar=9000,
            recv=9000,
            book_from=9000,
        )
    )
    rows.append(row("m-c3", 7, "C", 9, 5000, 70))
    return rows


class StageAAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.maxDiff = None

    def test_handwritten_oracle_lists_exactly_13(self) -> None:
        self.assertEqual(len(HANDWRITTEN_IDS), 13)
        self.assertEqual(len(set(HANDWRITTEN_IDS)), 13)
        self.assertEqual(HANDWRITTEN_PAYLOAD_FIELDS, PAYLOAD_FIELDS)

    def test_pinned_spec_compiles_handwritten_ids(self) -> None:
        definitions = compiled()
        self.assertEqual([item["strategy_id"] for item in definitions], list(HANDWRITTEN_IDS))
        for definition in definitions:
            self.assertEqual(set(definition["policy"]), set(POLICY_KEYS))
        ordered = {item["strategy_id"]: item["members"] for item in definitions if item["ordered"]}
        self.assertEqual(ordered["O:A>B"], ["A", "B"])
        self.assertEqual(ordered["O:B>A"], ["B", "A"])
        self.assertEqual(ordered["O:A>B"][0] + ">" + ordered["O:B>A"][0], "A>B")
        pair = next(item for item in definitions if item["strategy_id"] == "O:A>B")
        self.assertEqual(pair["policy"]["lag_ms"], 100)
        self.assertIs(pair["policy"]["lag_inclusive"], True)
        self.assertEqual(pair["policy"]["expiry_ms"], 1000)
        self.assertIs(pair["policy"]["expiry_inclusive"], False)

    def test_commutative_sort_does_not_sort_ordered_pairs(self) -> None:
        self.assertEqual(canonicalize_members("unordered_pair", ["B", "A"]), ["A", "B"])
        self.assertEqual(canonicalize_members("ordered_pair", ["B", "A"]), ["B", "A"])

    def test_duplicate_unordered_pair_fails_visibly(self) -> None:
        spec = clone(load_spec())
        spec["families"]["singles"] = []
        spec["families"]["unordered_pairs"] = [["B", "A"], ["A", "B"]]
        spec["families"]["unordered_triples"] = []
        spec["families"]["ordered_pairs"] = []
        with self.assertRaises(ContractError) as caught:
            fixture_enumerate.enumerate_fixture(spec)
        self.assertEqual(caught.exception.code, "DUPLICATE_STRATEGY")

    def test_pinned_file_matches_handwritten_rows(self) -> None:
        loaded = json.loads((PKG / "fixtures" / "committed-events.json").read_text(encoding="utf-8"))
        self.assertEqual(oracle_canonical(loaded), oracle_canonical(build_manifest(pinned_rows())))
        raw = base64.b64decode(loaded["segment"]["segment_bytes_b64"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), loaded["segment"]["segment_digest"])
        prefix = raw[: loaded["segment"]["prefix_extent"]]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), loaded["segment"]["prefix_digest"])
        self.assertNotEqual(loaded["segment"]["segment_digest"], loaded["segment"]["prefix_digest"])

    def test_pinned_signals_match_handwritten_trace(self) -> None:
        signals, produced = signals_for(pinned_rows())
        self.assertEqual(len(produced["records"]), 6)
        self.assertEqual(sorted(signal_key(item) for item in signals), sorted(expected_pinned_signals()))

    def test_directions_are_distinct(self) -> None:
        forward, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 200, 2)])
        backward, _ = signals_for([row("m-b1", 0, "B", 0, 0, 1), row("m-a1", 1, "A", 0, 200, 2)])
        self.assertEqual({item["strategy_id"] for item in ordered_fires(forward)}, {"O:A>B"})
        self.assertEqual({item["strategy_id"] for item in ordered_fires(backward)}, {"O:B>A"})

    def test_lag_expiry_tie_duplicate_reset_and_retrigger(self) -> None:
        fired, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 100, 2)])
        self.assertEqual(len(ordered_fires(fired, "O:A>B")), 1)
        early, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 99, 2)])
        self.assertEqual(ordered_fires(early, "O:A>B"), [])
        inside, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 999, 2)])
        self.assertEqual(len(ordered_fires(inside, "O:A>B")), 1)
        expired, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 1000, 2)])
        self.assertEqual(ordered_fires(expired, "O:A>B"), [])
        tie_a, _ = signals_for([row("m-a1", 0, "A", 0, 500, 1), row("m-b1", 1, "B", 0, 500, 2)])
        tie_b, _ = signals_for([row("m-b1", 0, "B", 0, 500, 1), row("m-a1", 1, "A", 0, 500, 2)])
        self.assertEqual(ordered_fires(tie_a), [])
        self.assertEqual(ordered_fires(tie_b), [])
        held, produced = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 200, 2)])
        duplicate = next(record for record in produced["records"] if record["member_id"] == "m-b1")
        doubled = grammar.evaluate_signals(compiled(), produced["records"] + [dict(duplicate)])
        self.assertEqual(len(ordered_fires(doubled, "O:A>B")), 1)
        retrigger, _ = signals_for(
            [
                row("m-a1", 0, "A", 0, 0, 1),
                row("m-b1", 1, "B", 0, 200, 2),
                row("m-b2", 2, "B", 0, 400, 3),
                row("m-a2", 3, "A", 0, 1000, 4),
                row("m-b3", 4, "B", 0, 1200, 5),
            ]
        )
        self.assertEqual([item["decision_ms"] for item in ordered_fires(retrigger, "O:A>B")], [200, 1200])
        reset, _ = signals_for(
            [
                row("m-a1", 0, "A", 0, 0, 1),
                row("m-b1", 1, "B", 0, 1000, 2),
                row("m-b2", 2, "B", 0, 1100, 3),
                row("m-a2", 3, "A", 0, 2000, 4),
                row("m-b3", 4, "B", 0, 2200, 5),
            ]
        )
        self.assertEqual([item["decision_ms"] for item in ordered_fires(reset, "O:A>B")], [2200])
        later, _ = signals_for(
            [
                row("m-a1", 0, "A", 0, 0, 1),
                row("m-b1", 1, "B", 0, 50, 2),
                row("m-b2", 2, "B", 0, 150, 3),
            ]
        )
        self.assertEqual([item["decision_ms"] for item in ordered_fires(later, "O:A>B")], [150])

    def test_negative_single_is_short(self) -> None:
        signals, _ = signals_for([row("m-a1", 0, "A", -2, 1000, 1)])
        single = next(item for item in signals if item["strategy_id"] == "S:A")
        self.assertEqual(single["signal"], "SHORT")
        self.assertEqual(single["marginal_outcome"], "successful")

    def test_pure_interaction_survives_unsuccessful_marginals(self) -> None:
        definitions = compiled()
        self.assertIn("U:A+B", [item["strategy_id"] for item in definitions])
        signals, _ = signals_for(pinned_rows())
        singles = [item for item in signals if item["strategy_id"] in {"S:A", "S:B"}]
        self.assertTrue(singles)
        self.assertTrue(all(item["marginal_outcome"] == "unsuccessful" for item in singles))
        interaction = next(item for item in signals if item["strategy_id"] == "U:A+B")
        self.assertEqual(interaction["signal"], "CONJUNCTION")
        self.assertIs(interaction["pure_interaction"], True)

    def test_post_cutoff_perturbation_is_byte_identical_through_t(self) -> None:
        self.assertEqual(
            oracle_canonical(view_through(pinned_rows())),
            oracle_canonical(view_through(perturbed_rows())),
        )
        changed = pinned_rows()
        changed[0]["value"] = 3
        self.assertNotEqual(
            oracle_canonical(view_through(pinned_rows())),
            oracle_canonical(view_through(changed)),
        )

    def test_features_use_strict_prior_points_only(self) -> None:
        produced = normalized(pinned_rows())
        feature_rows = features_mod.features_at(produced["records"], load_spec()["feature"])
        at_2000 = {
            item["series_id"]: item
            for item in feature_rows
            if item["cutoff_ms"] == 2000
        }
        self.assertEqual(at_2000["A"]["status"], "DEFINED")
        self.assertEqual(at_2000["A"]["value"], 0)
        self.assertEqual(at_2000["C"]["status"], "DEFINED")
        self.assertEqual(at_2000["C"]["value"], 1)
        at_1500 = [item for item in feature_rows if item["cutoff_ms"] == 1500]
        self.assertTrue(all(item["status"] == "UNKNOWN" and item["value"] is None for item in at_1500))

    def test_delayed_publication_bar_close_revision_and_null_source(self) -> None:
        record = normalized(
            [row("m-a1", 0, "A", 1, 1000, 1, source=1000, pub=5000, bar=1000, recv=1000)]
        )["records"][0]
        self.assertFalse(asof.is_eligible(record, 2000))
        self.assertIsNone(asof.asof_latest([record], "A", 2000))
        bar = normalized([row("m-a1", 0, "A", 1, 1000, 1, source=1000, pub=1000, bar=5000, recv=1000)])[
            "records"
        ][0]
        self.assertFalse(asof.is_eligible(bar, 2000))
        missing = normalized([row("m-a1", 0, "A", 1, 1000, 1, source=None)])["records"][0]
        self.assertIsNone(missing["timestamps"]["source_ms"])
        self.assertEqual(missing["timestamps"]["receive_ms"], 1000)
        self.assertEqual(missing["availability"], "UNKNOWN")
        self.assertFalse(missing["usable"])
        self.assertFalse(asof.is_eligible(missing, 1000))
        first = row("m-a1", 0, "A", 1, 1000, 1)
        revised = row(
            "m-a1r",
            1,
            "A",
            9,
            1000,
            2,
            release="revision",
            revision_of=link("m-a1", 0),
            source=1000,
            pub=5000,
            bar=1000,
            recv=1000,
        )
        records = normalized([first, revised])["records"]
        early = asof.visible_at(records, 2000)
        late = asof.visible_at(records, 6000)
        self.assertEqual([item["value"] for item in early], [1])
        self.assertEqual([item["value"] for item in late], [9])

    def test_receive_order_is_preserved(self) -> None:
        records = normalized(
            [
                row("m-a1", 0, "A", 1, 1000, 1, source=5000, pub=1000, bar=1000, recv=1000),
                row("m-b1", 1, "B", 1, 2000, 2, source=1000, pub=2000, bar=2000, recv=2000),
            ]
        )["records"]
        self.assertEqual([item["member_id"] for item in records], ["m-a1", "m-b1"])
        self.assertGreater(records[0]["timestamps"]["source_ms"], records[1]["timestamps"]["source_ms"])
        self.assertLess(records[0]["timestamps"]["receive_ms"], records[1]["timestamps"]["receive_ms"])

    def test_linkage_rejections(self) -> None:
        manifest = build_manifest(pinned_rows())
        raw = base64.b64decode(manifest["segment"]["segment_bytes_b64"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["segment"]["segment_digest"])
        bad_member = clone(manifest)
        bad_member["segment"]["members"][0]["member_digest"] = "0" * 64
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(bad_member)
        self.assertEqual(caught.exception.code, "MEMBER_DIGEST_MISMATCH")
        bad_prefix = clone(manifest)
        bad_prefix["segment"]["prefix_digest"] = "ab" * 32
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(bad_prefix)
        self.assertEqual(caught.exception.code, "PREFIX_DIGEST_MISMATCH")
        truncated = clone(manifest)
        member = truncated["segment"]["members"][0]
        self.assertGreater(member["byte_end"] - member["byte_start"], 1)
        member["byte_end"] = member["byte_start"] + 1
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(truncated)
        self.assertEqual(caught.exception.code, "TRUNCATED_MEMBER")
        reindexed = clone(manifest)
        reindexed["segment"]["members"][0]["record_index"] = 7
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(reindexed)
        self.assertIn(caught.exception.code, {"RECORD_INDEX_SEQUENCE", "LINKAGE_MISMATCH"})
        produced = adapter.normalize_manifest(manifest)
        self.assertTrue(any(item["code"] == "UNCOMMITTED_TAIL" for item in produced["rejections"]))
        self.assertTrue(all(not item["linkage"].endswith(":tail") for item in produced["records"]))

    def test_book_gap_is_unknown_not_filled(self) -> None:
        produced = normalized([row("m-a1", 0, "A", 7, 1000, 1, book_to=1500)])
        feature_rows = features_mod.features_at(produced["records"], load_spec()["feature"])
        gap = next(item for item in feature_rows if item["series_id"] == "A" and item["cutoff_ms"] == 2000)
        self.assertEqual(gap["status"], "UNKNOWN")
        self.assertIsNone(gap["value"])

    def test_fail_closed_inputs(self) -> None:
        missing = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        del missing["segment"]["members"][0]["source_timestamp_ms"]
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(missing)
        self.assertEqual(caught.exception.code, "MISSING_FIELD:source_timestamp_ms")
        unknown = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        unknown["manifest_schema"] = "hm.raw_event.v9"
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(unknown)
        self.assertEqual(caught.exception.code, "UNKNOWN_VERSION")
        nonfinite = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        nonfinite["segment"]["members"][0]["value"] = float("nan")
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(nonfinite)
        self.assertEqual(caught.exception.code, "NONFINITE:value")
        with self.assertRaises(ContractError) as caught:
            features_mod.append_series("A", {"series_id": "B"})
        self.assertEqual(caught.exception.code, "CROSS_SERIES_STATE")
        spec = clone(load_spec())
        spec["families"]["singles"] = ["A", "D"]
        with self.assertRaises(ContractError) as caught:
            fixture_enumerate.enumerate_fixture(spec)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_DOMAIN")
        spec = clone(load_spec())
        spec["families"]["ordered_triples"] = [["A", "B", "C"]]
        with self.assertRaises(ContractError) as caught:
            fixture_enumerate.enumerate_fixture(spec)
        self.assertEqual(caught.exception.code, "UNKNOWN_FIELD:ordered_triples")
        claimed = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        claimed["compatibility"] = "PASS"
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(claimed)
        self.assertEqual(caught.exception.code, "COMPATIBILITY_NOT_ESTABLISHED")

    def test_canary_does_not_leak(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        manifest["segment"]["members"][0]["untrusted_blob"] = CANARY
        with self.assertRaises(ContractError) as caught:
            adapter.normalize_manifest(manifest)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertNotIn("CANARY-SECRET-", str(caught.exception))
        with self.assertRaises(ContractError) as caught:
            guards.scan_text("prefix " + CANARY)
        self.assertEqual(caught.exception.code, "PAYLOAD_LEAK")

    def test_prohibited_access_is_refused_before_io(self) -> None:
        with self.assertRaises(guards.ProhibitedAccess) as caught:
            guards.reject_prohibited("network")
        self.assertEqual(caught.exception.code, "PROHIBITED:network")
        with self.assertRaises(guards.ProhibitedAccess) as caught:
            guards.guarded_read_text(Path("/etc/passwd"), [PKG])
        self.assertEqual(caught.exception.code, "PROHIBITED:real-root")
        with self.assertRaises(guards.ProhibitedAccess) as caught:
            guards.guarded_read_text(Path("/vault/secret"), [PKG])
        self.assertEqual(caught.exception.code, "PROHIBITED:vault")

    def test_caps_fail_closed_without_truncation(self) -> None:
        spec = clone(load_spec())
        spec["families"]["singles"] = ["A"] * 257
        spec["families"]["unordered_pairs"] = []
        spec["families"]["unordered_triples"] = []
        spec["families"]["ordered_pairs"] = []
        with self.assertRaises(guards.CapExhausted) as caught:
            fixture_enumerate.enumerate_fixture(spec)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_DEFINITIONS")
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["members"] = [{} for _ in range(2049)]
        with self.assertRaises(guards.CapExhausted) as caught:
            adapter.normalize_manifest(manifest)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_EVENTS")
        with self.assertRaises(guards.CapExhausted) as caught:
            guards.assert_workers(3)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_WORKERS")
        with self.assertRaises(guards.CapExhausted) as caught:
            guards.assert_elapsed(121)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_COMMAND_SECONDS")
        with self.assertRaises(guards.CapExhausted) as caught:
            guards.assert_elapsed(601, suite=True)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_SUITE_SECONDS")
        with self.assertRaises(guards.CapExhausted) as caught:
            guards.assert_receipt_size((8 * 1024 * 1024) + 1)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_RECEIPT_BYTES")
        with self.assertRaises(guards.CapExhausted) as caught:
            guards.assert_rss((512 * 1024 * 1024) + 1)
        self.assertEqual(caught.exception.code, "INCOMPLETE:CAP_RSS")

    def test_schema_and_import_pins(self) -> None:
        schema = json.loads((PKG / "contracts-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(schema["$defs"]["normalized_record"]["required"]), NORMALIZED_REQUIRED)
        self.assertEqual(schema["$defs"]["compatibility"]["properties"]["compatibility_status"]["const"], "BLOCKED")
        sources = guards.load_package_sources(PKG)
        detected = guards.audit_sources(sources, test_names={"tests/test_acceptance.py"})
        lock = json.loads((PKG / "dependencies.lock.json").read_text(encoding="utf-8"))
        allowed = set(lock["stdlib_imports"]) | set(lock["internal_imports"]) | set(lock["test_stdlib_imports"])
        self.assertEqual(lock["third_party"], [])
        self.assertFalse(lock["network"])
        self.assertFalse(lock["env_access"])
        for name, modules in detected.items():
            self.assertTrue(set(modules) <= allowed, name)
            text = sources[name]
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    for banned in ("auditor", "collector", "dashboard"):
                        self.assertNotIn(banned, stripped)

    def test_cli_receipt_pins_and_blocks_compatibility(self) -> None:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_path = Path(temp_dir) / "receipt.json"
            code = fixture_cli.main(
                [
                    "--fixture-only",
                    "--spec",
                    str(PKG / "fixtures" / "tiny-spec.json"),
                    "--events",
                    str(PKG / "fixtures" / "committed-events.json"),
                    "--receipt",
                    str(receipt_path),
                    "--pin-base",
                    AUTHORIZED_BASE,
                    "--pin-head",
                    AUTHORIZED_BASE,
                    "--workers",
                    "1",
                ]
            )
            elapsed = time.perf_counter() - started
            self.assertEqual(code, 0)
            self.assertLess(elapsed, 120)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            text = oracle_canonical(receipt).decode("utf-8")
            self.assertNotIn('"compatibility_status":"PASS"', text)
            self.assertNotIn('"compatibility":"PASS"', text)
            self.assertEqual(receipt["pin_base"], AUTHORIZED_BASE)
            self.assertEqual(receipt["coverage"]["strategy_ids"], list(HANDWRITTEN_IDS))
            self.assertEqual(receipt["coverage"]["synthetic_strategy_count"], 13)
            self.assertIsNone(receipt["coverage"]["research_n"])
            self.assertIsNone(receipt["coverage"]["dates"])
            self.assertIsNone(receipt["coverage"]["max_hold"])
            self.assertIsNone(receipt["coverage"]["cost_threshold"])
            self.assertEqual(receipt["coverage"]["temporal_real_n"], "NOT_IMPLEMENTED")
            self.assertEqual(receipt["coverage"]["temporal_real_tested"], "NOT_TESTED")
            self.assertIs(receipt["coverage"]["outside_posted_n"], True)
            self.assertEqual(receipt["coverage"]["accepted_findings"], "NONE")
            self.assertEqual(receipt["coverage"]["profitability"], "NO_EVIDENCE")
            self.assertEqual(receipt["coverage"]["mission"], "NOT_DONE")
            self.assertEqual(receipt["compatibility"]["compatibility_status"], "BLOCKED")
            self.assertEqual(receipt["compatibility"]["auditor_coverage_report_spec_ref"], "3272c6425b59")
            self.assertEqual(receipt["compatibility"]["collector_spec_refs"], ["1439354678ef", "7bee784c7e13"])
            self.assertEqual(receipt["resources"]["cpu_workers"], 1)
            self.assertLess(receipt["resources"]["peak_rss_bytes"], 512 * 1024 * 1024)
            self.assertLess(receipt["resources"]["receipt_bytes"], 8 * 1024 * 1024)
            self.assertEqual(receipt["resources"]["event_count"], 7)
            self.assertEqual(receipt["resources"]["cap_status"], "WITHIN_CAPS")
            for relative, digest in receipt["substantive_bytes_sha256"].items():
                payload = (PKG / relative).read_bytes()
                self.assertEqual(digest, hashlib.sha256(payload).hexdigest(), relative)
            refused = fixture_cli.main(
                [
                    "--spec",
                    str(PKG / "fixtures" / "tiny-spec.json"),
                    "--events",
                    str(PKG / "fixtures" / "committed-events.json"),
                    "--receipt",
                    str(Path(temp_dir) / "other.json"),
                    "--pin-base",
                    AUTHORIZED_BASE,
                    "--pin-head",
                    AUTHORIZED_BASE,
                ]
            )
            self.assertEqual(refused, 2)
            inside = fixture_cli.main(
                [
                    "--fixture-only",
                    "--spec",
                    str(PKG / "fixtures" / "tiny-spec.json"),
                    "--events",
                    str(PKG / "fixtures" / "committed-events.json"),
                    "--receipt",
                    str(PKG / "fixtures" / "blocked-receipt.json"),
                    "--pin-base",
                    AUTHORIZED_BASE,
                    "--pin-head",
                    AUTHORIZED_BASE,
                ]
            )
            self.assertEqual(inside, 3)
            self.assertFalse((PKG / "fixtures" / "blocked-receipt.json").exists())

    def test_m01_drop_ordered_pair(self) -> None:
        mutant = [item for item in HANDWRITTEN_IDS if item != "O:A>B"]

        def assertion() -> None:
            self.assertEqual(mutant, list(HANDWRITTEN_IDS))

        expect_killed("M01_DROP_ORDERED_PAIR", "removed ordered pair O:A>B", "handwritten_oracle_id_set_equality", assertion)

    def test_m02_reverse_ordered_pair(self) -> None:
        mutant = ["O:B>A" if item == "O:A>B" else item for item in HANDWRITTEN_IDS]

        def assertion() -> None:
            self.assertEqual(mutant, list(HANDWRITTEN_IDS))

        expect_killed("M02_REVERSE_ORDERED_PAIR", "reversed O:A>B into O:B>A", "handwritten_oracle_id_set_equality", assertion)

    def test_m03_sort_ordered_pair(self) -> None:
        mutant = []
        for item in HANDWRITTEN_IDS:
            if item.startswith("O:"):
                left, right = item[2:].split(">")
                ordered = sorted([left, right])
                mutant.append("O:" + ordered[0] + ">" + ordered[1])
            else:
                mutant.append(item)

        def assertion() -> None:
            self.assertEqual(mutant, list(HANDWRITTEN_IDS))

        expect_killed("M03_SORT_ORDERED_PAIR", "sorted ordered-pair members", "handwritten_oracle_id_set_equality", assertion)

    def test_m04_direction_collapse(self) -> None:
        def collapsed(signals: list[dict]) -> set[tuple[str, str]]:
            found = set()
            for signal in ordered_fires(signals):
                left, right = signal["strategy_id"][2:].split(">")
                pair = tuple(sorted([left, right]))
                found.add((pair[0], pair[1]))
            return found

        def assertion() -> None:
            forward, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 200, 2)])
            backward, _ = signals_for([row("m-b1", 0, "B", 0, 0, 1), row("m-a1", 1, "A", 0, 200, 2)])
            self.assertNotEqual(collapsed(forward), collapsed(backward))

        expect_killed(
            "M04_DIRECTION_COLLAPSE",
            "compared ordered pairs after sorting away direction",
            "A_before_B_distinct_from_B_before_A",
            assertion,
        )

    def test_m05_lag_endpoint_exclusive(self) -> None:
        real = grammar.classify_delay

        def exclusive(delay: int, policy: dict) -> str:
            patched = dict(policy)
            patched["lag_inclusive"] = False
            return real(delay, patched)

        grammar.classify_delay = exclusive
        try:
            def assertion() -> None:
                signals, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 100, 2)])
                self.assertEqual(len(ordered_fires(signals, "O:A>B")), 1)

            expect_killed("M05_LAG_ENDPOINT_EXCLUSIVE", "lag endpoint treated as exclusive", "lag_100_fires", assertion)
        finally:
            grammar.classify_delay = real

    def test_m06_expiry_inclusive(self) -> None:
        real = grammar.classify_delay

        def inclusive(delay: int, policy: dict) -> str:
            patched = dict(policy)
            patched["expiry_inclusive"] = True
            return real(delay, patched)

        grammar.classify_delay = inclusive
        try:
            def assertion() -> None:
                signals, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 1000, 2)])
                self.assertEqual(ordered_fires(signals, "O:A>B"), [])

            expect_killed("M06_EXPIRY_INCLUSIVE", "expiry endpoint treated as inclusive", "expiry_1000_does_not_fire", assertion)
        finally:
            grammar.classify_delay = real

    def test_m07_tie_fires(self) -> None:
        real = grammar.classify_delay

        def ties_fire(delay: int, policy: dict) -> str:
            if delay == 0:
                return "fire"
            return real(delay, policy)

        grammar.classify_delay = ties_fire
        try:
            def assertion() -> None:
                signals, _ = signals_for([row("m-a1", 0, "A", 0, 500, 1), row("m-b1", 1, "B", 0, 500, 2)])
                self.assertEqual(ordered_fires(signals), [])

            expect_killed("M07_TIE_FIRES", "simultaneous availability fires an ordered pair", "tie_does_not_fire", assertion)
        finally:
            grammar.classify_delay = real

    def test_m08_duplicate_retriggers(self) -> None:
        real = grammar.duplicate_seen
        grammar.duplicate_seen = lambda seen, linkage: False
        try:
            def assertion() -> None:
                _signals, produced = signals_for([row("m-a1", 0, "A", 1, 1000, 1)])
                duplicate = next(record for record in produced["records"] if record["member_id"] == "m-a1")
                signals = grammar.evaluate_signals(compiled(), [duplicate, dict(duplicate)])
                singles = [item for item in signals if item["strategy_id"] == "S:A"]
                self.assertEqual(len(singles), 1)

            expect_killed("M08_DUPLICATE_RETRIGGERS", "exact duplicate linkage processed again", "duplicate_linkage_one_signal", assertion)
        finally:
            grammar.duplicate_seen = real

    def test_m09_retrigger_without_reset(self) -> None:
        real = grammar.consume_pending

        def keep_on_fire(pending: dict | None, kind: str) -> dict | None:
            if kind == "expire":
                return None
            return pending

        grammar.consume_pending = keep_on_fire
        try:
            def assertion() -> None:
                signals, _ = signals_for(
                    [
                        row("m-a1", 0, "A", 0, 0, 1),
                        row("m-b1", 1, "B", 0, 200, 2),
                        row("m-b2", 2, "B", 0, 400, 3),
                    ]
                )
                self.assertEqual([item["decision_ms"] for item in ordered_fires(signals, "O:A>B")], [200])

            expect_killed("M09_RETRIGGER_WITHOUT_RESET", "ordered fire does not consume the antecedent", "second_consequent_does_not_fire", assertion)
        finally:
            grammar.consume_pending = real

    def test_m10_expiry_without_reset(self) -> None:
        real = grammar.consume_pending

        def keep_on_expire(pending: dict | None, kind: str) -> dict | None:
            if kind == "fire":
                return None
            return pending

        grammar.consume_pending = keep_on_expire
        try:
            def assertion() -> None:
                signals, _ = signals_for(
                    [
                        row("m-a1", 0, "A", 0, 0, 1),
                        row("m-b1", 1, "B", 0, 1000, 2),
                        row("m-b2", 2, "B", 0, 1100, 3),
                        row("m-a2", 3, "A", 0, 2000, 4),
                        row("m-b3", 4, "B", 0, 2200, 5),
                    ]
                )
                self.assertEqual([item["decision_ms"] for item in ordered_fires(signals, "O:A>B")], [2200])

            expect_killed("M10_EXPIRY_WITHOUT_RESET", "expiry does not reset the pending antecedent", "new_antecedent_can_fire_after_expiry", assertion)
        finally:
            grammar.consume_pending = real

    def test_m11_post_cutoff_included(self) -> None:
        base = view_through(pinned_rows())

        def assertion() -> None:
            mutated = dict(base)
            mutated["records"] = normalized(perturbed_rows())["records"]
            self.assertEqual(oracle_canonical(mutated), oracle_canonical(base))

        expect_killed("M11_POST_CUTOFF_VIEW", "view through t includes post-cutoff records", "byte_identity_through_cutoff", assertion)

    def test_m12_source_time_only(self) -> None:
        record = normalized([row("m-a1", 0, "A", 1, 1000, 1, source=1000, pub=5000, bar=1000, recv=1000)])["records"][0]
        real = asof.causal_times_ok

        def source_only(item: dict, cutoff_ms: int) -> bool:
            value = item["timestamps"]["source_ms"]
            return value is not None and value <= cutoff_ms

        asof.causal_times_ok = source_only
        try:
            def assertion() -> None:
                self.assertFalse(asof.is_eligible(record, 2000))

            expect_killed("M12_SOURCE_TIME_ONLY", "eligibility uses source time only", "delayed_publication_not_eligible", assertion)
        finally:
            asof.causal_times_ok = real

    def test_m13_future_calibration(self) -> None:
        rows = [
            row("m-c1", 0, "C", 1, 1000, 1),
            row("m-c2", 1, "C", 1, 1200, 2),
            row("m-c3", 2, "C", 9, 5000, 3),
        ]
        records = normalized(rows)["records"]
        real = features_mod.include_in_prior
        features_mod.include_in_prior = lambda record, cutoff_ms: True
        try:
            def assertion() -> None:
                feature = features_mod.prior_mean(records, "C", 2000, load_spec()["feature"], bound_series="C")
                self.assertEqual(feature["value"], 1)

            expect_killed("M13_FUTURE_CALIBRATION", "prior mean includes observations available after cutoff", "prior_mean_excludes_future", assertion)
        finally:
            features_mod.include_in_prior = real

    def test_m14_nullable_substitution(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1, source=None)])
        real = adapter.source_timestamp_ms

        def substitute(member: dict) -> object:
            if member["source_timestamp_ms"] is None:
                return member["receive_time_ms"]
            return member["source_timestamp_ms"]

        adapter.source_timestamp_ms = substitute
        try:
            def assertion() -> None:
                record = adapter.normalize_manifest(manifest)["records"][0]
                self.assertIsNone(record["timestamps"]["source_ms"])
                self.assertEqual(record["availability"], "UNKNOWN")

            expect_killed("M14_NULLABLE_SUBSTITUTION", "missing exchange timestamp replaced with receive time", "source_timestamp_remains_null", assertion)
        finally:
            adapter.source_timestamp_ms = real

    def test_m15_revision_before_publication(self) -> None:
        records = normalized(
            [
                row("m-a1", 0, "A", 1, 1000, 1),
                row(
                    "m-a1r",
                    1,
                    "A",
                    9,
                    1000,
                    2,
                    release="revision",
                    revision_of=link("m-a1", 0),
                    source=1000,
                    pub=5000,
                    bar=1000,
                    recv=1000,
                ),
            ]
        )["records"]
        real = asof.causal_times_ok

        def source_only(item: dict, cutoff_ms: int) -> bool:
            value = item["timestamps"]["source_ms"]
            return value is not None and value <= cutoff_ms

        asof.causal_times_ok = source_only
        try:
            def assertion() -> None:
                visible = asof.visible_at(records, 2000)
                self.assertEqual([item["value"] for item in visible], [1])

            expect_killed("M15_REVISION_BEFORE_PUBLICATION", "revision visible from source time before publication", "revision_hidden_until_publication", assertion)
        finally:
            asof.causal_times_ok = real

    def test_m16_bar_close_ignored(self) -> None:
        record = normalized([row("m-a1", 0, "A", 1, 1000, 1, source=1000, pub=1000, bar=5000, recv=1000)])["records"][0]
        real = asof.causal_times_ok

        def no_bar(item: dict, cutoff_ms: int) -> bool:
            for key in ("source_ms", "publication_ms", "receive_ms"):
                value = item["timestamps"][key]
                if value is None or value > cutoff_ms:
                    return False
            return True

        asof.causal_times_ok = no_bar
        try:
            def assertion() -> None:
                self.assertFalse(asof.is_eligible(record, 2000))

            expect_killed("M16_BAR_CLOSE_IGNORED", "bar-close time omitted from eligibility", "late_bar_close_not_eligible", assertion)
        finally:
            asof.causal_times_ok = real

    def test_m17_member_digest_skipped(self) -> None:
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["members"][0]["member_digest"] = "0" * 64
        real = adapter.assert_member_digest
        adapter.assert_member_digest = lambda actual, declared: None
        try:
            def assertion() -> None:
                try:
                    adapter.normalize_manifest(manifest)
                except ContractError as exc:
                    self.assertEqual(exc.code, "MEMBER_DIGEST_MISMATCH")
                    return
                raise AssertionError("MEMBER_LINKAGE_IGNORED")

            expect_killed("M17_MEMBER_DIGEST_SKIPPED", "member digest check removed while segment digest still matches", "corrupted_member_rejected", assertion)
        finally:
            adapter.assert_member_digest = real

    def test_m18_truncation_accepted(self) -> None:
        manifest = build_manifest(pinned_rows())
        member = manifest["segment"]["members"][0]
        member["byte_end"] = member["byte_start"] + 1
        real = adapter.verify_committed_prefix

        def segment_only(segment: dict, committed: list[dict]) -> list[dict]:
            raw = base64.b64decode(segment["segment_bytes_b64"])
            if hashlib.sha256(raw).hexdigest() != segment["segment_digest"]:
                raise ContractError("SEGMENT_DIGEST_MISMATCH")
            return sorted(committed, key=lambda item: item["record_index"])

        adapter.verify_committed_prefix = segment_only
        try:
            def assertion() -> None:
                try:
                    adapter.normalize_manifest(manifest)
                except ContractError as exc:
                    self.assertEqual(exc.code, "TRUNCATED_MEMBER")
                    return
                raise AssertionError("TRUNCATION_ACCEPTED")

            expect_killed("M18_TRUNCATION_ACCEPTED", "verifier trusts the whole-segment digest and skips member slices", "truncated_member_rejected", assertion)
        finally:
            adapter.verify_committed_prefix = real

    def test_m19_uncommitted_tail_usable(self) -> None:
        produced = adapter.normalize_manifest(build_manifest(pinned_rows()))

        def assertion() -> None:
            mutated = list(produced["records"])
            mutated.append({"linkage": "fx-seg-1:m-tail:tail", "usable": True})
            self.assertTrue(all(not item["linkage"].endswith(":tail") for item in mutated))

        expect_killed("M19_UNCOMMITTED_TAIL_USABLE", "uncommitted tail appended to usable records", "tail_absent_from_usable_records", assertion)

    def test_m20_record_index_accepted(self) -> None:
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["members"][0]["record_index"] = 7
        real = adapter.verify_committed_prefix

        def segment_only(segment: dict, committed: list[dict]) -> list[dict]:
            raw = base64.b64decode(segment["segment_bytes_b64"])
            if hashlib.sha256(raw).hexdigest() != segment["segment_digest"]:
                raise ContractError("SEGMENT_DIGEST_MISMATCH")
            return list(committed)

        adapter.verify_committed_prefix = segment_only
        try:
            def assertion() -> None:
                try:
                    adapter.normalize_manifest(manifest)
                except ContractError as exc:
                    self.assertIn(exc.code, {"RECORD_INDEX_SEQUENCE", "LINKAGE_MISMATCH"})
                    return
                raise AssertionError("RECORD_INDEX_ACCEPTED")

            expect_killed("M20_RECORD_INDEX_ACCEPTED", "record-index change accepted after a segment-digest-only check", "record_index_change_rejected", assertion)
        finally:
            adapter.verify_committed_prefix = real

    def test_m21_forward_fill(self) -> None:
        records = normalized([row("m-a1", 0, "A", 7, 1000, 1, book_to=1500)])["records"]
        real = features_mod.fill_gap
        features_mod.fill_gap = lambda last_value: last_value
        try:
            def assertion() -> None:
                feature = features_mod.prior_mean(records, "A", 2000, load_spec()["feature"], bound_series="A")
                self.assertEqual(feature["status"], "UNKNOWN")
                self.assertIsNone(feature["value"])

            expect_killed("M21_FORWARD_FILL", "expired book value carried across a validity gap", "gap_feature_unknown", assertion)
        finally:
            features_mod.fill_gap = real

    def test_m22_prune_pure_interaction(self) -> None:
        definitions = compiled()
        signals, _ = signals_for(pinned_rows())

        def prune() -> list[str]:
            unsuccessful = set()
            for signal in signals:
                if signal["marginal_outcome"] == "unsuccessful":
                    unsuccessful.add(signal["strategy_id"].split(":")[1])
            kept = []
            for definition in definitions:
                if definition["kind"] != "single" and any(member in unsuccessful for member in definition["members"]):
                    continue
                kept.append(definition["strategy_id"])
            return kept

        def assertion() -> None:
            self.assertEqual(prune(), list(HANDWRITTEN_IDS))

        expect_killed("M22_PRUNE_PURE_INTERACTION", "conjunctions containing unsuccessful marginals were dropped", "pure_interaction_remains_in_family", assertion)

    def test_m23_missing_field_defaulted(self) -> None:
        def faulty(manifest: dict) -> dict:
            del manifest
            return {"records": [{"usable": True, "timestamps": {"source_ms": 0}}]}

        def assertion() -> None:
            try:
                faulty(build_manifest([row("m-a1", 0, "A", 1, 1000, 1)]))
            except ContractError as exc:
                self.assertEqual(exc.code, "MISSING_FIELD:source_timestamp_ms")
                return
            raise AssertionError("MISSING_DEFAULTED")

        expect_killed("M23_MISSING_FIELD_DEFAULTED", "missing source timestamp defaulted to a usable record", "missing_field_fails_closed", assertion)

    def test_m24_unknown_version_accepted(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        manifest["manifest_schema"] = "hm.raw_event.v9"
        real = adapter._validate_envelope

        def skip_schema(item: dict) -> None:
            saved = item["manifest_schema"]
            item["manifest_schema"] = "causal_grammar.manufactured_input.v1"
            try:
                real(item)
            finally:
                item["manifest_schema"] = saved

        adapter._validate_envelope = skip_schema
        try:
            def assertion() -> None:
                try:
                    adapter.normalize_manifest(manifest)
                except ContractError as exc:
                    self.assertEqual(exc.code, "UNKNOWN_VERSION")
                    return
                raise AssertionError("UNKNOWN_VERSION_ACCEPTED")

            expect_killed("M24_UNKNOWN_VERSION_ACCEPTED", "unknown manifest schema rewritten to the fixture schema", "unknown_version_fails_closed", assertion)
        finally:
            adapter._validate_envelope = real

    def test_m25_nonfinite_coerced(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        manifest["segment"]["members"][0]["value"] = float("nan")
        real = adapter.assert_finite_value
        adapter.assert_finite_value = lambda value: None
        try:
            def assertion() -> None:
                try:
                    adapter.normalize_manifest(manifest)
                except ContractError as exc:
                    self.assertEqual(exc.code, "NONFINITE:value")
                    return
                raise AssertionError("ACCEPTED_NONFINITE")

            expect_killed("M25_NONFINITE_COERCED", "nonfinite value check removed", "nonfinite_value_fails_closed", assertion)
        finally:
            adapter.assert_finite_value = real

    def test_m26_cross_series_allowed(self) -> None:
        real = features_mod.append_series
        features_mod.append_series = lambda bound, record: None
        try:
            def assertion() -> None:
                try:
                    features_mod.append_series("A", {"series_id": "B"})
                except ContractError as exc:
                    self.assertEqual(exc.code, "CROSS_SERIES_STATE")
                    return
                raise AssertionError("CROSS_SERIES_ALLOWED")

            expect_killed("M26_CROSS_SERIES_ALLOWED", "series boundary check removed", "cross_series_state_fails_closed", assertion)
        finally:
            features_mod.append_series = real

    def test_m27_unsupported_domain_dropped(self) -> None:
        spec = clone(load_spec())
        spec["families"]["singles"] = ["A", "D"]

        def faulty(item: dict) -> list[dict]:
            edited = clone(item)
            edited["families"]["singles"] = [member for member in edited["families"]["singles"] if member in {"A", "B", "C"}]
            return fixture_enumerate.enumerate_fixture(edited)

        def assertion() -> None:
            try:
                faulty(spec)
            except ContractError:
                return
            raise AssertionError("SILENT_SMALLER_FAMILY")

        expect_killed("M27_UNSUPPORTED_DOMAIN_DROPPED", "unsupported member D dropped and a smaller family returned", "unsupported_domain_fails_visibly", assertion)

    def test_m28_canary_leak(self) -> None:
        real = guards.scan_text
        guards.scan_text = lambda text: None
        try:
            def assertion() -> None:
                guards.scan_text(CANARY)
                raise AssertionError("LEAK_NOT_SCANNED")

            expect_killed("M28_CANARY_LEAK", "leakage scan disabled", "canary_scan_fails_closed", assertion)
        finally:
            guards.scan_text = real

    def test_m29_network_not_blocked(self) -> None:
        real = guards.reject_prohibited
        guards.reject_prohibited = lambda kind: None
        try:
            def assertion() -> None:
                guards.reject_prohibited("network")
                raise AssertionError("NETWORK_NOT_BLOCKED")

            expect_killed("M29_NETWORK_NOT_BLOCKED", "network attempt allowed", "network_attempt_blocked", assertion)
        finally:
            guards.reject_prohibited = real

    def test_m30_compatibility_pass(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        real = adapter.blocked_compatibility

        def claim_pass(refs: dict) -> dict:
            claimed = real(refs)
            claimed["compatibility"] = "PASS"
            claimed["compatibility_status"] = "PASS"
            return claimed

        adapter.blocked_compatibility = claim_pass
        try:
            def assertion() -> None:
                record = adapter.normalize_manifest(manifest)["records"][0]
                self.assertEqual(record["provenance"]["compatibility_status"], "BLOCKED")
                self.assertNotEqual(record["provenance"]["compatibility"], "PASS")

            expect_killed("M30_COMPATIBILITY_PASS", "provenance compatibility forced to PASS", "compatibility_remains_blocked", assertion)
        finally:
            adapter.blocked_compatibility = real

    def test_m31_prefix_digest_ignored(self) -> None:
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["prefix_digest"] = "ab" * 32
        real = adapter.verify_committed_prefix

        def segment_only(segment: dict, committed: list[dict]) -> list[dict]:
            raw = base64.b64decode(segment["segment_bytes_b64"])
            if hashlib.sha256(raw).hexdigest() != segment["segment_digest"]:
                raise ContractError("SEGMENT_DIGEST_MISMATCH")
            return sorted(committed, key=lambda item: item["record_index"])

        adapter.verify_committed_prefix = segment_only
        try:
            def assertion() -> None:
                try:
                    adapter.normalize_manifest(manifest)
                except ContractError as exc:
                    self.assertEqual(exc.code, "PREFIX_DIGEST_MISMATCH")
                    return
                raise AssertionError("PREFIX_LINKAGE_IGNORED")

            expect_killed("M31_PREFIX_DIGEST_IGNORED", "prefix digest ignored when the whole-segment digest matches", "corrupted_prefix_rejected", assertion)
        finally:
            adapter.verify_committed_prefix = real

    def test_m32_interpolation(self) -> None:
        records = normalized(
            [
                row("m-a1", 0, "A", 0, 1000, 1, book_to=1200),
                row("m-a2", 1, "A", 10, 3000, 2, book_from=3000),
            ]
        )["records"]
        real = features_mod.resolve_gap

        def interpolate(left_value: object, right_value: object, left_ms: int, right_ms: int, cutoff_ms: int) -> float:
            del left_ms, right_ms, cutoff_ms
            return (float(left_value) + float(right_value)) / 2

        features_mod.resolve_gap = interpolate
        try:
            def assertion() -> None:
                feature = features_mod.prior_mean(records, "A", 2000, load_spec()["feature"], bound_series="A")
                self.assertEqual(feature["status"], "UNKNOWN")
                self.assertIsNone(feature["value"])

            expect_killed("M32_INTERPOLATION", "gap between observations interpolated", "gap_not_interpolated", assertion)
        finally:
            features_mod.resolve_gap = real

    def test_z_mutation_resource_and_suite_caps(self) -> None:
        required = {
            "M01_DROP_ORDERED_PAIR",
            "M02_REVERSE_ORDERED_PAIR",
            "M03_SORT_ORDERED_PAIR",
            "M04_DIRECTION_COLLAPSE",
            "M05_LAG_ENDPOINT_EXCLUSIVE",
            "M06_EXPIRY_INCLUSIVE",
            "M07_TIE_FIRES",
            "M08_DUPLICATE_RETRIGGERS",
            "M09_RETRIGGER_WITHOUT_RESET",
            "M10_EXPIRY_WITHOUT_RESET",
            "M11_POST_CUTOFF_VIEW",
            "M12_SOURCE_TIME_ONLY",
            "M13_FUTURE_CALIBRATION",
            "M14_NULLABLE_SUBSTITUTION",
            "M15_REVISION_BEFORE_PUBLICATION",
            "M16_BAR_CLOSE_IGNORED",
            "M17_MEMBER_DIGEST_SKIPPED",
            "M18_TRUNCATION_ACCEPTED",
            "M19_UNCOMMITTED_TAIL_USABLE",
            "M20_RECORD_INDEX_ACCEPTED",
            "M21_FORWARD_FILL",
            "M22_PRUNE_PURE_INTERACTION",
            "M23_MISSING_FIELD_DEFAULTED",
            "M24_UNKNOWN_VERSION_ACCEPTED",
            "M25_NONFINITE_COERCED",
            "M26_CROSS_SERIES_ALLOWED",
            "M27_UNSUPPORTED_DOMAIN_DROPPED",
            "M28_CANARY_LEAK",
            "M29_NETWORK_NOT_BLOCKED",
            "M30_COMPATIBILITY_PASS",
            "M31_PREFIX_DIGEST_IGNORED",
            "M32_INTERPOLATION",
        }
        killed = {item["fault_id"] for item in MUTATIONS if item["killed"]}
        self.assertTrue(required <= killed)
        self.assertTrue(all(item["exception_type"] == "AssertionError" for item in MUTATIONS))
        self.assertLess(time.perf_counter() - SUITE_STARTED, 600)
        self.assertLess(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024, 512 * 1024 * 1024)


if __name__ == "__main__":
    outcome = unittest.main(argv=[sys.argv[0]], exit=False).result
    print("MUTATION_KILLS", sum(1 for item in MUTATIONS if item["killed"]))
    print("SUITE_ELAPSED_SECONDS", f"{time.perf_counter() - SUITE_STARTED:.6f}")
    print("PEAK_RSS_BYTES", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
    sys.exit(0 if outcome.wasSuccessful() else 1)
