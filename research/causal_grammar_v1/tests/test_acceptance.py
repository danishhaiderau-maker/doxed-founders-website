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
MATRIX: list[dict] = []
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


class _SymbolPatch:
    def __init__(self, owner: object, name: str, fn: object) -> None:
        self.owner = owner
        self.name = name
        self.fn = fn
        self.saved = None

    def on(self) -> None:
        self.saved = getattr(self.owner, self.name)
        setattr(self.owner, self.name, self.fn)

    def off(self) -> None:
        setattr(self.owner, self.name, self.saved)

    def restored(self) -> bool:
        return getattr(self.owner, self.name) is self.saved


class _GlobalPatch:
    def __init__(self, name: str, fn: object) -> None:
        self.name = name
        self.fn = fn
        self.saved = None

    def on(self) -> None:
        self.saved = globals()[self.name]
        globals()[self.name] = self.fn

    def off(self) -> None:
        globals()[self.name] = self.saved

    def restored(self) -> bool:
        return globals()[self.name] is self.saved


class _ComboPatch:
    def __init__(self, patches: list[object]) -> None:
        self.patches = patches

    def on(self) -> None:
        for patch in self.patches:
            patch.on()

    def off(self) -> None:
        for patch in reversed(self.patches):
            patch.off()

    def restored(self) -> bool:
        return all(patch.restored() for patch in self.patches)


def run_matrix(
    *,
    fault_id: str,
    symbol: str,
    call_path: str,
    assertion_id: str,
    sentinel: str,
    note: str,
    base,
    control_patch,
    control,
    intact,
    weak_patch,
    weak,
) -> None:
    row = {
        "fault_id": fault_id,
        "symbol": symbol,
        "call_path": call_path,
        "assertion_id": assertion_id,
        "sentinel": sentinel,
        "note": note,
        "base": "ERROR",
        "control": "ERROR",
        "intact": "ERROR",
        "weak": "ERROR",
        "restored": False,
    }
    try:
        base()
        row["base"] = "HELD"
        control_patch.on()
        try:
            control()
            row["control"] = "HELD"
        finally:
            control_patch.off()
        intact()
        row["intact"] = "HELD"
        weak_patch.on()
        try:
            try:
                weak()
            except AssertionError:
                row["weak"] = "ORACLE_VIOLATED"
            except Exception as exc:
                row["weak"] = "ERROR:" + type(exc).__name__ + ":" + str(exc)
        finally:
            weak_patch.off()
            row["restored"] = weak_patch.restored()
    except Exception as exc:
        if row["base"] != "HELD":
            row["base"] = "ERROR:" + type(exc).__name__ + ":" + str(exc)
        elif row["control"] != "HELD":
            row["control"] = "ERROR:" + type(exc).__name__ + ":" + str(exc)
        elif row["intact"] != "HELD":
            row["intact"] = "ERROR:" + type(exc).__name__ + ":" + str(exc)
        MATRIX.append(row)
        raise
    MATRIX.append(row)
    if row["weak"] != "ORACLE_VIOLATED" or not row["restored"]:
        raise AssertionError(fault_id + ":" + row["weak"] + ":restored=" + str(row["restored"]))


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


def _capture_main(args: list[str]) -> tuple[int, str]:
    held = sys.stderr
    chunks: list[str] = []

    class _Err:
        def write(self, text: str) -> None:
            chunks.append(text)

        def flush(self) -> None:
            return None

    sys.stderr = _Err()
    try:
        code = fixture_cli.main(args)
    finally:
        sys.stderr = held
    return code, "".join(chunks)


def _cli_args(receipt: Path, workers: str = "1") -> list[str]:
    return [
        "--fixture-only",
        "--spec",
        str(PKG / "fixtures" / "tiny-spec.json"),
        "--events",
        str(PKG / "fixtures" / "committed-events.json"),
        "--receipt",
        str(receipt),
        "--pin-base",
        AUTHORIZED_BASE,
        "--pin-head",
        AUTHORIZED_BASE,
        "--workers",
        workers,
    ]


def _ordered_only(pairs: list[list[str]]) -> dict:
    spec = clone(load_spec())
    spec["families"]["singles"] = ["A"]
    spec["families"]["unordered_pairs"] = []
    spec["families"]["unordered_triples"] = []
    spec["families"]["ordered_pairs"] = pairs
    return spec


class _SlowClock:
    def __init__(self) -> None:
        self.started = 0.0

    def elapsed(self) -> float:
        return 121.0

    def assert_open(self) -> None:
        guards.assert_elapsed(self.elapsed())


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
            self.assertIs(receipt["git_head_self_pin"], False)
            self.assertIs(receipt["resources"]["ceiling_proof"], False)
            self.assertIn("not_zero_overshoot", receipt["resources"]["enforcement"])
            self.assertIn("under_limit_run_is_not_ceiling_proof", receipt["resources"]["enforcement"])
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

    def test_prior_mean_uses_latest_revision_and_keeps_history(self) -> None:
        feature_spec = load_spec()["feature"]
        chain = [
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
                pub=1500,
                bar=1500,
                recv=1500,
            ),
        ]
        chain_records = normalized(chain)["records"]
        self.assertEqual({item["linkage"] for item in chain_records}, {link("m-a1", 0), link("m-a1r", 1)})
        chain_only = features_mod.prior_mean(chain_records, "A", 2000, feature_spec, bound_series="A")
        self.assertEqual(chain_only["status"], "UNKNOWN")
        self.assertIsNone(chain_only["value"])
        eligible = [
            item
            for item in chain_records
            if features_mod.include_in_prior(item, 2000)
        ]
        self.assertEqual(len(eligible), 2)
        self.assertEqual(len(features_mod.collapse_latest_eligible(eligible)), 1)
        signals = grammar.evaluate_signals(compiled(), chain_records)
        self.assertEqual(len([item for item in signals if item["strategy_id"] == "S:A"]), 2)
        mixed = chain + [row("m-a2", 2, "A", 3, 1600, 3)]
        mixed_records = normalized(mixed)["records"]
        mixed_feature = features_mod.prior_mean(mixed_records, "A", 2000, feature_spec, bound_series="A")
        self.assertEqual(mixed_feature["status"], "DEFINED")
        self.assertEqual(mixed_feature["value"], 6)
        blocked = [
            row("m-a1", 0, "A", 1, 1000, 1),
            row("m-a2", 1, "A", 3, 1100, 2),
            row(
                "m-a1r",
                2,
                "A",
                100,
                1000,
                4,
                release="revision",
                revision_of=link("m-a1", 0),
                source=1000,
                pub=1500,
                bar=1500,
                recv=1500,
                book_from=9000,
            ),
        ]
        blocked_feature = features_mod.prior_mean(
            normalized(blocked)["records"], "A", 2000, feature_spec, bound_series="A"
        )
        self.assertEqual(blocked_feature["value"], 2)

    def test_revision_does_not_change_earlier_feature_bytes(self) -> None:
        feature_spec = load_spec()["feature"]
        early = [row("m-a1", 0, "A", 1, 1000, 1), row("m-a2", 1, "A", 3, 1100, 2)]
        late = early + [
            row(
                "m-a1r",
                2,
                "A",
                9,
                1000,
                3,
                release="revision",
                revision_of=link("m-a1", 0),
                source=1000,
                pub=5000,
                bar=5000,
                recv=5000,
                book_from=5000,
            )
        ]
        left = features_mod.prior_mean(normalized(early)["records"], "A", 1200, feature_spec, bound_series="A")
        right_records = normalized(late)["records"]
        right = features_mod.prior_mean(right_records, "A", 1200, feature_spec, bound_series="A")
        self.assertEqual(oracle_canonical(left), oracle_canonical(right))
        self.assertIn(link("m-a1r", 2), {item["linkage"] for item in right_records})

    def test_schema_structure_accepts_emitted_record(self) -> None:
        schema = json.loads((PKG / "contracts-v1.schema.json").read_text(encoding="utf-8"))
        guards.schema_record_consistent(schema)
        record = normalized([row("m-a1", 0, "A", 1, 1000, 1)])["records"][0]
        guards.assert_record_matches_schema_structure(record, schema)
        broken = json.loads(json.dumps(schema))
        del broken["$defs"]["normalized_record"]["properties"]["linkage"]
        self.assertEqual(tuple(broken["$defs"]["normalized_record"]["required"]), NORMALIZED_REQUIRED)
        with self.assertRaises(ContractError) as caught:
            guards.schema_record_consistent(broken)
        self.assertEqual(caught.exception.code, "SCHEMA_INCONSISTENT")
        extra = dict(record)
        extra["unknown_field"] = 1
        with self.assertRaises(ContractError) as caught:
            guards.assert_record_matches_schema_structure(extra, schema)
        self.assertEqual(caught.exception.code, "SCHEMA_INCONSISTENT")

    def test_dynamic_import_audit_and_outer_layer(self) -> None:
        planted_call = "__import__('socket')\n"
        planted_module = "importlib.import_module('socket')\n"
        planted_import = "import socket\n"
        for source in (planted_call, planted_module, planted_import):
            with self.assertRaises(guards.ProhibitedAccess) as caught:
                guards.audit_sources({"planted.py": source}, test_names=set())
            self.assertEqual(caught.exception.code, "PROHIBITED:import")
            with self.assertRaises(guards.ProhibitedAccess) as outer:
                guards.outer_containment_scan({"planted.py": source})
            self.assertEqual(outer.exception.code, "PROHIBITED:outer-import")

    def _ids(self, spec: dict | None = None) -> list[str]:
        return [item["strategy_id"] for item in fixture_enumerate.enumerate_fixture(spec or load_spec())]

    def test_m01_drop_ordered_pair(self) -> None:
        real = fixture_enumerate.strategy_id_for

        def keep(kind: str, members: list[str]) -> str:
            return real(kind, members)

        def drop(kind: str, members: list[str]) -> str:
            strategy_id = real(kind, members)
            if strategy_id == "O:A>B":
                return "O:MISSING"
            return strategy_id

        def oracle() -> None:
            ids = self._ids()
            assert ids == list(HANDWRITTEN_IDS)
            assert "O:A>B" in ids

        run_matrix(
            fault_id="M01_DROP_ORDERED_PAIR",
            symbol="enumerate.strategy_id_for",
            call_path="enumerate_fixture",
            assertion_id="handwritten_ids_include_O:A>B",
            sentinel="O:A>B",
            note="production id builder omits O:A>B",
            base=oracle,
            control_patch=_SymbolPatch(fixture_enumerate, "strategy_id_for", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(fixture_enumerate, "strategy_id_for", drop),
            weak=oracle,
        )

    def test_m02_reverse_ordered_pair(self) -> None:
        real = fixture_enumerate.strategy_id_for
        spec = _ordered_only([["A", "B"]])

        def keep(kind: str, members: list[str]) -> str:
            return real(kind, members)

        def reverse(kind: str, members: list[str]) -> str:
            if kind == "ordered_pair" and list(members) == ["A", "B"]:
                return real(kind, ["B", "A"])
            return real(kind, members)

        def oracle() -> None:
            ids = self._ids(spec)
            assert "O:A>B" in ids and "O:B>A" not in ids

        run_matrix(
            fault_id="M02_REVERSE_ORDERED_PAIR",
            symbol="enumerate.strategy_id_for",
            call_path="enumerate_fixture",
            assertion_id="ordered_A_to_B_keeps_direction",
            sentinel="O:A>B",
            note="single ordered pair reversed by the id builder",
            base=oracle,
            control_patch=_SymbolPatch(fixture_enumerate, "strategy_id_for", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(fixture_enumerate, "strategy_id_for", reverse),
            weak=oracle,
        )

    def test_m03_sort_ordered_pair(self) -> None:
        real = fixture_enumerate.canonicalize_members
        spec = _ordered_only([["B", "A"]])

        def keep(kind: str, members: list[str]) -> list[str]:
            return real(kind, members)

        def sort_ordered(kind: str, members: list[str]) -> list[str]:
            if kind == "ordered_pair":
                return sorted(members)
            return real(kind, members)

        def oracle() -> None:
            definitions = fixture_enumerate.enumerate_fixture(spec)
            ordered = next(item for item in definitions if item["ordered"])
            assert ordered["strategy_id"] == "O:B>A"
            assert ordered["members"] == ["B", "A"]

        run_matrix(
            fault_id="M03_SORT_ORDERED_PAIR",
            symbol="enumerate.canonicalize_members",
            call_path="enumerate_fixture",
            assertion_id="ordered_B_to_A_not_sorted",
            sentinel="O:B>A",
            note="canonicalize_members sorts an ordered pair",
            base=oracle,
            control_patch=_SymbolPatch(fixture_enumerate, "canonicalize_members", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(fixture_enumerate, "canonicalize_members", sort_ordered),
            weak=oracle,
        )

    def test_m04_direction_collapse(self) -> None:
        real = grammar.evaluate_ordered

        def keep(defn: dict, records: list[dict]) -> list[dict]:
            return real(defn, records)

        def collapse(defn: dict, records: list[dict]) -> list[dict]:
            pair = sorted(defn["members"])
            label = "O:" + pair[0] + ">" + pair[1]
            forward = dict(defn)
            forward["members"] = [pair[0], pair[1]]
            forward["strategy_id"] = label
            backward = dict(defn)
            backward["members"] = [pair[1], pair[0]]
            backward["strategy_id"] = label
            return real(forward, records) + real(backward, records)

        def oracle() -> None:
            forward, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 200, 2)])
            backward, _ = signals_for([row("m-b1", 0, "B", 0, 0, 1), row("m-a1", 1, "A", 0, 200, 2)])
            left = {item["strategy_id"] for item in ordered_fires(forward)}
            right = {item["strategy_id"] for item in ordered_fires(backward)}
            assert left == {"O:A>B"}
            assert right == {"O:B>A"}

        run_matrix(
            fault_id="M04_DIRECTION_COLLAPSE",
            symbol="grammar.evaluate_ordered",
            call_path="evaluate_signals",
            assertion_id="A_before_B_distinct_from_B_before_A",
            sentinel="O:A>B_vs_O:B>A",
            note="control keeps distinct fires; weak collapse makes the sets equal",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "evaluate_ordered", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "evaluate_ordered", collapse),
            weak=oracle,
        )

    def test_m05_lag_endpoint_exclusive(self) -> None:
        real = grammar.classify_delay

        def keep(delay: int, policy: dict) -> str:
            return real(delay, policy)

        def exclusive(delay: int, policy: dict) -> str:
            patched = dict(policy)
            patched["lag_inclusive"] = False
            return real(delay, patched)

        def oracle() -> None:
            signals, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 100, 2)])
            assert len(ordered_fires(signals, "O:A>B")) == 1

        run_matrix(
            fault_id="M05_LAG_ENDPOINT_EXCLUSIVE",
            symbol="grammar.classify_delay",
            call_path="evaluate_ordered",
            assertion_id="lag_100_fires",
            sentinel="ORDERED_FIRE",
            note="inclusive lag boundary",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "classify_delay", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "classify_delay", exclusive),
            weak=oracle,
        )

    def test_m06_expiry_inclusive(self) -> None:
        real = grammar.classify_delay

        def keep(delay: int, policy: dict) -> str:
            return real(delay, policy)

        def inclusive(delay: int, policy: dict) -> str:
            patched = dict(policy)
            patched["expiry_inclusive"] = True
            return real(delay, patched)

        def oracle() -> None:
            signals, _ = signals_for([row("m-a1", 0, "A", 0, 0, 1), row("m-b1", 1, "B", 0, 1000, 2)])
            assert ordered_fires(signals, "O:A>B") == []

        run_matrix(
            fault_id="M06_EXPIRY_INCLUSIVE",
            symbol="grammar.classify_delay",
            call_path="evaluate_ordered",
            assertion_id="expiry_1000_does_not_fire",
            sentinel="no_fire",
            note="exclusive expiry boundary",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "classify_delay", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "classify_delay", inclusive),
            weak=oracle,
        )

    def test_m07_tie_fires(self) -> None:
        real = grammar.classify_delay

        def keep(delay: int, policy: dict) -> str:
            return real(delay, policy)

        def ties_fire(delay: int, policy: dict) -> str:
            if delay == 0:
                return "fire"
            return real(delay, policy)

        def oracle() -> None:
            signals, _ = signals_for([row("m-a1", 0, "A", 0, 500, 1), row("m-b1", 1, "B", 0, 500, 2)])
            assert ordered_fires(signals) == []

        run_matrix(
            fault_id="M07_TIE_FIRES",
            symbol="grammar.classify_delay",
            call_path="evaluate_ordered",
            assertion_id="tie_does_not_fire",
            sentinel="no_fire",
            note="delay 0 is a tie",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "classify_delay", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "classify_delay", ties_fire),
            weak=oracle,
        )

    def test_m08_duplicate_retriggers(self) -> None:
        real = grammar.duplicate_seen

        def keep(seen: set[str], linkage: str) -> bool:
            return real(seen, linkage)

        def allow(seen: set[str], linkage: str) -> bool:
            del seen, linkage
            return False

        def oracle() -> None:
            _signals, produced = signals_for([row("m-a1", 0, "A", 1, 1000, 1)])
            duplicate = next(record for record in produced["records"] if record["member_id"] == "m-a1")
            signals = grammar.evaluate_signals(compiled(), [duplicate, dict(duplicate)])
            assert len([item for item in signals if item["strategy_id"] == "S:A"]) == 1

        run_matrix(
            fault_id="M08_DUPLICATE_RETRIGGERS",
            symbol="grammar.duplicate_seen",
            call_path="evaluate_single",
            assertion_id="duplicate_linkage_one_signal",
            sentinel="one_S:A",
            note="repeated linkage",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "duplicate_seen", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "duplicate_seen", allow),
            weak=oracle,
        )

    def test_m09_retrigger_without_reset(self) -> None:
        real = grammar.consume_pending

        def keep(pending: dict | None, kind: str) -> dict | None:
            return real(pending, kind)

        def keep_on_fire(pending: dict | None, kind: str) -> dict | None:
            if kind == "expire":
                return None
            return pending

        def oracle() -> None:
            signals, _ = signals_for(
                [
                    row("m-a1", 0, "A", 0, 0, 1),
                    row("m-b1", 1, "B", 0, 200, 2),
                    row("m-b2", 2, "B", 0, 400, 3),
                ]
            )
            assert [item["decision_ms"] for item in ordered_fires(signals, "O:A>B")] == [200]

        run_matrix(
            fault_id="M09_RETRIGGER_WITHOUT_RESET",
            symbol="grammar.consume_pending",
            call_path="evaluate_ordered",
            assertion_id="second_consequent_does_not_fire",
            sentinel="200",
            note="fire must consume the antecedent",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "consume_pending", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "consume_pending", keep_on_fire),
            weak=oracle,
        )

    def test_m10_expiry_without_reset(self) -> None:
        real = grammar.consume_pending

        def keep(pending: dict | None, kind: str) -> dict | None:
            return real(pending, kind)

        def keep_on_expire(pending: dict | None, kind: str) -> dict | None:
            if kind == "fire":
                return None
            return pending

        def oracle() -> None:
            signals, _ = signals_for(
                [
                    row("m-a1", 0, "A", 0, 0, 1),
                    row("m-b1", 1, "B", 0, 1000, 2),
                    row("m-b2", 2, "B", 0, 1100, 3),
                    row("m-a2", 3, "A", 0, 2000, 4),
                    row("m-b3", 4, "B", 0, 2200, 5),
                ]
            )
            assert [item["decision_ms"] for item in ordered_fires(signals, "O:A>B")] == [2200]

        run_matrix(
            fault_id="M10_EXPIRY_WITHOUT_RESET",
            symbol="grammar.consume_pending",
            call_path="evaluate_ordered",
            assertion_id="new_antecedent_can_fire_after_expiry",
            sentinel="2200",
            note="expiry must clear the pending antecedent",
            base=oracle,
            control_patch=_SymbolPatch(grammar, "consume_pending", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(grammar, "consume_pending", keep_on_expire),
            weak=oracle,
        )

    def test_m11_post_cutoff_included(self) -> None:
        saved = view_through

        def keep(rows: list[dict], cutoff_ms: int = 1500) -> dict:
            return saved(rows, cutoff_ms)

        def unfiltered(rows: list[dict], cutoff_ms: int = 1500) -> dict:
            del cutoff_ms
            produced = normalized(rows)
            spec = load_spec()
            return {
                "records": produced["records"],
                "features": features_mod.features_at(produced["records"], spec["feature"]),
                "signals": grammar.evaluate_signals(compiled(), produced["records"]),
                "rejections": produced["rejections"],
            }

        def oracle() -> None:
            view = view_through(pinned_rows())
            assert all(item["member_id"] != "m-a2" for item in view["records"])
            assert oracle_canonical(view_through(pinned_rows())) == oracle_canonical(view_through(perturbed_rows()))

        run_matrix(
            fault_id="M11_POST_CUTOFF_VIEW",
            symbol="view_through",
            call_path="normalize_manifest+features_at+evaluate_signals",
            assertion_id="byte_identity_through_cutoff",
            sentinel="m-a2_absent",
            note="filter removed; production bytes still come from the real normalizer",
            base=oracle,
            control_patch=_GlobalPatch("view_through", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_GlobalPatch("view_through", unfiltered),
            weak=oracle,
        )

    def test_m12_source_time_only(self) -> None:
        record = normalized([row("m-a1", 0, "A", 1, 1000, 1, source=1000, pub=5000, bar=1000, recv=1000)])["records"][0]
        real = asof.causal_times_ok

        def keep(item: dict, cutoff_ms: int) -> bool:
            return real(item, cutoff_ms)

        def source_only(item: dict, cutoff_ms: int) -> bool:
            value = item["timestamps"]["source_ms"]
            return value is not None and value <= cutoff_ms

        def oracle() -> None:
            assert asof.is_eligible(record, 2000) is False

        run_matrix(
            fault_id="M12_SOURCE_TIME_ONLY",
            symbol="asof.causal_times_ok",
            call_path="is_eligible",
            assertion_id="delayed_publication_not_eligible",
            sentinel="False",
            note="publication 5000 blocks cutoff 2000",
            base=oracle,
            control_patch=_SymbolPatch(asof, "causal_times_ok", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(asof, "causal_times_ok", source_only),
            weak=oracle,
        )

    def test_m13_future_calibration(self) -> None:
        records = normalized(
            [
                row("m-c1", 0, "C", 1, 1000, 1),
                row("m-c2", 1, "C", 1, 1200, 2),
                row("m-c3", 2, "C", 9, 5000, 3),
            ]
        )["records"]
        real = features_mod.include_in_prior
        feature_spec = load_spec()["feature"]

        def keep(record: dict, cutoff_ms: int) -> bool:
            return real(record, cutoff_ms)

        def everything(record: dict, cutoff_ms: int) -> bool:
            del record, cutoff_ms
            return True

        def oracle() -> None:
            feature = features_mod.prior_mean(records, "C", 2000, feature_spec, bound_series="C")
            assert feature["value"] == 1

        run_matrix(
            fault_id="M13_FUTURE_CALIBRATION",
            symbol="features.include_in_prior",
            call_path="prior_mean",
            assertion_id="prior_mean_excludes_future",
            sentinel="1",
            note="post-cutoff value 9",
            base=oracle,
            control_patch=_SymbolPatch(features_mod, "include_in_prior", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(features_mod, "include_in_prior", everything),
            weak=oracle,
        )

    def test_m14_nullable_substitution(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1, source=None)])
        real = adapter.source_timestamp_ms

        def keep(member: dict) -> object:
            return real(member)

        def substitute(member: dict) -> object:
            if member["source_timestamp_ms"] is None:
                return member["receive_time_ms"]
            return member["source_timestamp_ms"]

        def oracle() -> None:
            record = adapter.normalize_manifest(manifest)["records"][0]
            assert record["timestamps"]["source_ms"] is None
            assert record["availability"] == "UNKNOWN"

        run_matrix(
            fault_id="M14_NULLABLE_SUBSTITUTION",
            symbol="normalized_adapter.source_timestamp_ms",
            call_path="to_record",
            assertion_id="source_timestamp_remains_null",
            sentinel="UNKNOWN",
            note="receive time must not fill a null exchange timestamp",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "source_timestamp_ms", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "source_timestamp_ms", substitute),
            weak=oracle,
        )

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

        def keep(item: dict, cutoff_ms: int) -> bool:
            return real(item, cutoff_ms)

        def source_only(item: dict, cutoff_ms: int) -> bool:
            value = item["timestamps"]["source_ms"]
            return value is not None and value <= cutoff_ms

        def oracle() -> None:
            visible = asof.visible_at(records, 2000)
            assert [item["value"] for item in visible] == [1]

        run_matrix(
            fault_id="M15_REVISION_BEFORE_PUBLICATION",
            symbol="asof.causal_times_ok",
            call_path="visible_at",
            assertion_id="revision_hidden_until_publication",
            sentinel="1",
            note="revision publication is 5000",
            base=oracle,
            control_patch=_SymbolPatch(asof, "causal_times_ok", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(asof, "causal_times_ok", source_only),
            weak=oracle,
        )

    def test_m16_bar_close_ignored(self) -> None:
        record = normalized([row("m-a1", 0, "A", 1, 1000, 1, source=1000, pub=1000, bar=5000, recv=1000)])["records"][0]
        real = asof.causal_times_ok

        def keep(item: dict, cutoff_ms: int) -> bool:
            return real(item, cutoff_ms)

        def no_bar(item: dict, cutoff_ms: int) -> bool:
            for key in ("source_ms", "publication_ms", "receive_ms"):
                value = item["timestamps"][key]
                if value is None or value > cutoff_ms:
                    return False
            return True

        def oracle() -> None:
            assert asof.is_eligible(record, 2000) is False

        run_matrix(
            fault_id="M16_BAR_CLOSE_IGNORED",
            symbol="asof.causal_times_ok",
            call_path="is_eligible",
            assertion_id="late_bar_close_not_eligible",
            sentinel="False",
            note="bar close 5000",
            base=oracle,
            control_patch=_SymbolPatch(asof, "causal_times_ok", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(asof, "causal_times_ok", no_bar),
            weak=oracle,
        )

    def test_m17_member_digest_skipped(self) -> None:
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["members"][0]["member_digest"] = "0" * 64
        real_digest = adapter.assert_member_digest

        def oracle() -> None:
            try:
                adapter.normalize_manifest(manifest)
            except ContractError as exc:
                assert exc.code == "MEMBER_DIGEST_MISMATCH"
                return
            raise AssertionError("MEMBER_LINKAGE_IGNORED")

        run_matrix(
            fault_id="M17_MEMBER_DIGEST_SKIPPED",
            symbol="normalized_adapter.assert_member_digest",
            call_path="verify_committed_prefix",
            assertion_id="corrupted_member_rejected",
            sentinel="MEMBER_DIGEST_MISMATCH",
            note="segment digest still matches",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "assert_member_digest", real_digest),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "assert_member_digest", lambda actual, declared: None),
            weak=oracle,
        )

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

        def oracle() -> None:
            try:
                adapter.normalize_manifest(manifest)
            except ContractError as exc:
                assert exc.code == "TRUNCATED_MEMBER"
                return
            raise AssertionError("TRUNCATION_ACCEPTED")

        run_matrix(
            fault_id="M18_TRUNCATION_ACCEPTED",
            symbol="normalized_adapter.verify_committed_prefix",
            call_path="normalize_manifest",
            assertion_id="truncated_member_rejected",
            sentinel="TRUNCATED_MEMBER",
            note="segment-digest-only verifier",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "verify_committed_prefix", real),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "verify_committed_prefix", segment_only),
            weak=oracle,
        )

    def test_m19_uncommitted_tail_usable(self) -> None:
        real = adapter.normalize_manifest

        def keep(manifest: dict) -> dict:
            return real(manifest)

        def include_tail(manifest: dict) -> dict:
            result = real(manifest)
            records = list(result["records"])
            for member in manifest["segment"]["members"]:
                if member.get("committed") is False:
                    records.append({"linkage": member["linkage"], "usable": True, "member_id": member.get("member_id")})
            return {"records": records, "rejections": list(result["rejections"]), "trace": list(result["trace"])}

        def oracle() -> None:
            produced = adapter.normalize_manifest(build_manifest(pinned_rows()))
            assert all(not str(item.get("linkage", "")).endswith(":tail") for item in produced["records"])
            assert any(item["code"] == "UNCOMMITTED_TAIL" for item in produced["rejections"])

        run_matrix(
            fault_id="M19_UNCOMMITTED_TAIL_USABLE",
            symbol="normalized_adapter.normalize_manifest",
            call_path="normalize_manifest",
            assertion_id="tail_absent_from_usable_records",
            sentinel="UNCOMMITTED_TAIL",
            note="inline committed check is frozen; the symbol replacement reintroduces the tail",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "normalize_manifest", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "normalize_manifest", include_tail),
            weak=oracle,
        )

    def test_m20_record_index_accepted(self) -> None:
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["members"][0]["record_index"] = 7
        real = adapter.verify_committed_prefix

        def segment_only(segment: dict, committed: list[dict]) -> list[dict]:
            raw = base64.b64decode(segment["segment_bytes_b64"])
            if hashlib.sha256(raw).hexdigest() != segment["segment_digest"]:
                raise ContractError("SEGMENT_DIGEST_MISMATCH")
            return list(committed)

        def oracle() -> None:
            try:
                adapter.normalize_manifest(manifest)
            except ContractError as exc:
                assert exc.code in {"RECORD_INDEX_SEQUENCE", "LINKAGE_MISMATCH"}
                return
            raise AssertionError("RECORD_INDEX_ACCEPTED")

        run_matrix(
            fault_id="M20_RECORD_INDEX_ACCEPTED",
            symbol="normalized_adapter.verify_committed_prefix",
            call_path="normalize_manifest",
            assertion_id="record_index_change_rejected",
            sentinel="RECORD_INDEX_SEQUENCE",
            note="segment-digest-only verifier",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "verify_committed_prefix", real),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "verify_committed_prefix", segment_only),
            weak=oracle,
        )

    def test_m21_forward_fill(self) -> None:
        records = normalized([row("m-a1", 0, "A", 7, 1000, 1, book_to=1500)])["records"]
        feature_spec = load_spec()["feature"]

        def carry(last_value: object) -> object:
            return last_value

        def oracle() -> None:
            feature = features_mod.prior_mean(records, "A", 2000, feature_spec, bound_series="A")
            assert feature["status"] == "UNKNOWN"
            assert feature["value"] is None

        run_matrix(
            fault_id="M21_FORWARD_FILL",
            symbol="features.fill_gap",
            call_path="prior_mean",
            assertion_id="gap_feature_unknown",
            sentinel="UNKNOWN",
            note="book ends at 1500",
            base=oracle,
            control_patch=_SymbolPatch(features_mod, "fill_gap", lambda last_value: None),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(features_mod, "fill_gap", carry),
            weak=oracle,
        )

    def test_m22_prune_pure_interaction(self) -> None:
        real = fixture_enumerate.strategy_id_for

        def keep(kind: str, members: list[str]) -> str:
            return real(kind, members)

        def drop(kind: str, members: list[str]) -> str:
            strategy_id = real(kind, members)
            if strategy_id == "U:A+B":
                return "U:DROPPED"
            return strategy_id

        def oracle() -> None:
            ids = self._ids()
            assert "U:A+B" in ids
            assert ids == list(HANDWRITTEN_IDS)

        run_matrix(
            fault_id="M22_PRUNE_PURE_INTERACTION",
            symbol="enumerate.strategy_id_for",
            call_path="enumerate_fixture",
            assertion_id="pure_interaction_remains_in_family",
            sentinel="U:A+B",
            note="id builder renames the pure interaction out of the family",
            base=oracle,
            control_patch=_SymbolPatch(fixture_enumerate, "strategy_id_for", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(fixture_enumerate, "strategy_id_for", drop),
            weak=oracle,
        )

    def test_m23_missing_field_defaulted(self) -> None:
        real = adapter.require_keys

        def keep(obj: dict, keys: object, label: str) -> None:
            return real(obj, keys, label)

        def fill_source(obj: dict, keys: object, label: str) -> None:
            if isinstance(obj, dict) and "member_id" in obj and "source_timestamp_ms" not in obj:
                obj["source_timestamp_ms"] = 0
            return real(obj, keys, label)

        def oracle() -> None:
            missing = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
            del missing["segment"]["members"][0]["source_timestamp_ms"]
            try:
                adapter.normalize_manifest(missing)
            except ContractError as exc:
                assert exc.code == "MISSING_FIELD:source_timestamp_ms"
                return
            raise AssertionError("MISSING_DEFAULTED")

        def clean() -> None:
            adapter.normalize_manifest(build_manifest([row("m-a1", 0, "A", 1, 1000, 1)]))

        run_matrix(
            fault_id="M23_MISSING_FIELD_DEFAULTED",
            symbol="normalized_adapter.require_keys",
            call_path="normalize_manifest",
            assertion_id="missing_field_fails_closed",
            sentinel="MISSING_FIELD:source_timestamp_ms",
            note="control uses a complete manifest; weak fills the missing timestamp",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "require_keys", fill_source),
            control=clean,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "require_keys", fill_source),
            weak=oracle,
        )

    def test_m24_unknown_version_accepted(self) -> None:
        real = adapter._validate_envelope

        def keep(item: dict) -> None:
            return real(item)

        def skip_schema(item: dict) -> None:
            saved = item["manifest_schema"]
            item["manifest_schema"] = "causal_grammar.manufactured_input.v1"
            try:
                real(item)
            finally:
                item["manifest_schema"] = saved

        def oracle() -> None:
            manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
            manifest["manifest_schema"] = "hm.raw_event.v9"
            try:
                adapter.normalize_manifest(manifest)
            except ContractError as exc:
                assert exc.code == "UNKNOWN_VERSION"
                return
            raise AssertionError("UNKNOWN_VERSION_ACCEPTED")

        run_matrix(
            fault_id="M24_UNKNOWN_VERSION_ACCEPTED",
            symbol="normalized_adapter._validate_envelope",
            call_path="normalize_manifest",
            assertion_id="unknown_version_fails_closed",
            sentinel="UNKNOWN_VERSION",
            note="unknown schema rewritten to the fixture schema",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "_validate_envelope", keep),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "_validate_envelope", skip_schema),
            weak=oracle,
        )

    def test_m25_nonfinite_coerced(self) -> None:
        def manifest() -> dict:
            built = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
            built["segment"]["members"][0]["value"] = float("nan")
            return built

        def oracle() -> None:
            try:
                adapter.normalize_manifest(manifest())
            except ContractError as exc:
                assert exc.code == "NONFINITE:value"
                return
            raise AssertionError("ACCEPTED_NONFINITE")

        def clean() -> None:
            adapter.normalize_manifest(build_manifest([row("m-a1", 0, "A", 1, 1000, 1)]))

        run_matrix(
            fault_id="M25_NONFINITE_COERCED",
            symbol="normalized_adapter.assert_finite_value",
            call_path="normalize_manifest",
            assertion_id="nonfinite_value_fails_closed",
            sentinel="NONFINITE:value",
            note="frozen canonical_bytes still raises CANONICAL_REJECT after the finite check is removed",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "assert_finite_value", lambda value: None),
            control=clean,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "assert_finite_value", lambda value: None),
            weak=oracle,
        )

    def test_m26_cross_series_allowed(self) -> None:
        records = normalized([row("m-b1", 0, "B", 4, 1000, 1), row("m-b2", 1, "B", 6, 1200, 2)])["records"]
        feature_spec = load_spec()["feature"]

        def matched() -> None:
            feature = features_mod.prior_mean(records, "B", 2000, feature_spec, bound_series="B")
            assert feature["value"] == 5

        def crossed() -> None:
            try:
                features_mod.prior_mean(records, "B", 2000, feature_spec, bound_series="A")
            except ContractError as exc:
                assert exc.code == "CROSS_SERIES_STATE"
                return
            raise AssertionError("CROSS_SERIES_ALLOWED")

        run_matrix(
            fault_id="M26_CROSS_SERIES_ALLOWED",
            symbol="features.assert_series_boundary",
            call_path="prior_mean",
            assertion_id="cross_series_state_fails_closed",
            sentinel="CROSS_SERIES_STATE",
            note="prior_mean integration, not only append_series",
            base=crossed,
            control_patch=_SymbolPatch(features_mod, "assert_series_boundary", lambda bound, series: None),
            control=matched,
            intact=crossed,
            weak_patch=_SymbolPatch(features_mod, "assert_series_boundary", lambda bound, series: None),
            weak=crossed,
        )

    def test_m27_unsupported_domain_dropped(self) -> None:
        real = fixture_enumerate._check_member

        def keep(member: object) -> str:
            return real(member)

        def allow(member: object) -> str:
            if not isinstance(member, str):
                raise ContractError("UNSUPPORTED_DOMAIN")
            return member

        def bad() -> None:
            spec = clone(load_spec())
            spec["families"]["singles"] = ["A", "D"]
            try:
                fixture_enumerate.enumerate_fixture(spec)
            except ContractError as exc:
                assert exc.code == "UNSUPPORTED_DOMAIN"
                return
            raise AssertionError("SILENT_UNSUPPORTED")

        def good() -> None:
            assert self._ids() == list(HANDWRITTEN_IDS)

        run_matrix(
            fault_id="M27_UNSUPPORTED_DOMAIN_DROPPED",
            symbol="enumerate._check_member",
            call_path="enumerate_fixture",
            assertion_id="unsupported_domain_fails_visibly",
            sentinel="UNSUPPORTED_DOMAIN",
            note="member D is accepted instead of rejected",
            base=bad,
            control_patch=_SymbolPatch(fixture_enumerate, "_check_member", keep),
            control=good,
            intact=bad,
            weak_patch=_SymbolPatch(fixture_enumerate, "_check_member", allow),
            weak=bad,
        )

    def test_m28_canary_leak(self) -> None:
        rows = [row("m-a1", 0, "A", 1, 1000, 1)]
        rows[0]["connection_epoch"] = CANARY
        manifest = build_manifest(rows)

        def leaked_text() -> str:
            produced = adapter.normalize_manifest(manifest)
            return oracle_canonical(produced).decode("utf-8")

        def oracle() -> None:
            try:
                text = leaked_text()
            except ContractError as exc:
                assert exc.code == "PAYLOAD_LEAK"
                assert CANARY not in str(exc)
                return
            raise AssertionError("CANARY_ACCEPTED")

        def weak() -> None:
            text = leaked_text()
            outer_code = ""
            try:
                guards.outer_scan_text(text)
            except guards.ProhibitedAccess as exc:
                outer_code = exc.code
            if outer_code != "PROHIBITED:outer-leak":
                raise RuntimeError("OUTER_MISSED")
            assert CANARY not in text

        def clean() -> None:
            adapter.normalize_manifest(build_manifest([row("m-a1", 0, "A", 1, 1000, 1)]))

        run_matrix(
            fault_id="M28_CANARY_LEAK",
            symbol="guards.scan_text",
            call_path="normalize_manifest->scan_obj",
            assertion_id="canary_absent_from_output",
            sentinel="PAYLOAD_LEAK",
            note="canary is in the allowed connection_epoch field; outer scan stays on",
            base=oracle,
            control_patch=_SymbolPatch(guards, "scan_text", lambda text: None),
            control=clean,
            intact=oracle,
            weak_patch=_SymbolPatch(guards, "scan_text", lambda text: None),
            weak=weak,
        )

    def test_m29_network_not_blocked(self) -> None:
        planted = "__import__('socket')\n"

        def clean() -> None:
            guards.audit_sources({"clean.py": "x = 1\n"}, test_names=set())

        def planted_oracle() -> None:
            try:
                guards.audit_sources({"planted.py": planted}, test_names=set())
            except guards.ProhibitedAccess as exc:
                assert exc.code == "PROHIBITED:import"
                return
            raise AssertionError("DYNAMIC_IMPORT_ACCEPTED")

        def weak() -> None:
            outer_code = ""
            try:
                guards.outer_containment_scan({"planted.py": planted})
            except guards.ProhibitedAccess as exc:
                outer_code = exc.code
            if outer_code != "PROHIBITED:outer-import":
                raise RuntimeError("OUTER_MISSED")
            planted_oracle()

        run_matrix(
            fault_id="M29_NETWORK_NOT_BLOCKED",
            symbol="guards.reject_prohibited",
            call_path="audit_sources",
            assertion_id="dynamic_import_rejected",
            sentinel="PROHIBITED:import",
            note="source string only; outer_containment_scan is not patched",
            base=planted_oracle,
            control_patch=_SymbolPatch(guards, "reject_prohibited", lambda kind: None),
            control=clean,
            intact=planted_oracle,
            weak_patch=_SymbolPatch(guards, "reject_prohibited", lambda kind: None),
            weak=weak,
        )

    def test_m30_compatibility_pass(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        real = adapter.blocked_compatibility

        def claim_pass(refs: dict) -> dict:
            claimed = real(refs)
            claimed["compatibility"] = "PASS"
            claimed["compatibility_status"] = "PASS"
            return claimed

        def oracle() -> None:
            record = adapter.normalize_manifest(manifest)["records"][0]
            assert record["provenance"]["compatibility_status"] == "BLOCKED"
            assert record["provenance"]["compatibility"] != "PASS"

        run_matrix(
            fault_id="M30_COMPATIBILITY_PASS",
            symbol="normalized_adapter.blocked_compatibility",
            call_path="to_record",
            assertion_id="compatibility_remains_blocked",
            sentinel="BLOCKED",
            note="provenance forced to PASS",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "blocked_compatibility", real),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "blocked_compatibility", claim_pass),
            weak=oracle,
        )

    def test_m31_prefix_digest_ignored(self) -> None:
        manifest = build_manifest(pinned_rows())
        manifest["segment"]["prefix_digest"] = "ab" * 32
        real = adapter.verify_committed_prefix

        def segment_only(segment: dict, committed: list[dict]) -> list[dict]:
            raw = base64.b64decode(segment["segment_bytes_b64"])
            if hashlib.sha256(raw).hexdigest() != segment["segment_digest"]:
                raise ContractError("SEGMENT_DIGEST_MISMATCH")
            return sorted(committed, key=lambda item: item["record_index"])

        def oracle() -> None:
            try:
                adapter.normalize_manifest(manifest)
            except ContractError as exc:
                assert exc.code == "PREFIX_DIGEST_MISMATCH"
                return
            raise AssertionError("PREFIX_LINKAGE_IGNORED")

        run_matrix(
            fault_id="M31_PREFIX_DIGEST_IGNORED",
            symbol="normalized_adapter.verify_committed_prefix",
            call_path="normalize_manifest",
            assertion_id="corrupted_prefix_rejected",
            sentinel="PREFIX_DIGEST_MISMATCH",
            note="segment digest still matches",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "verify_committed_prefix", real),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "verify_committed_prefix", segment_only),
            weak=oracle,
        )

    def test_m32_interpolation(self) -> None:
        records = normalized(
            [
                row("m-a1", 0, "A", 0, 1000, 1, book_to=1200),
                row("m-a2", 1, "A", 10, 3000, 2, book_from=3000),
            ]
        )["records"]
        feature_spec = load_spec()["feature"]

        def interpolate(left_value: object, right_value: object, left_ms: int, right_ms: int, cutoff_ms: int) -> float:
            del left_ms, right_ms, cutoff_ms
            return (float(left_value) + float(right_value)) / 2.0

        def oracle() -> None:
            feature = features_mod.prior_mean(records, "A", 2000, feature_spec, bound_series="A")
            assert feature["status"] == "UNKNOWN"
            assert feature["value"] is None

        run_matrix(
            fault_id="M32_INTERPOLATION",
            symbol="features.resolve_gap",
            call_path="prior_mean",
            assertion_id="gap_not_interpolated",
            sentinel="UNKNOWN",
            note="gap between 1200 and 3000",
            base=oracle,
            control_patch=_SymbolPatch(features_mod, "resolve_gap", lambda *args: None),
            control=oracle,
            intact=oracle,
            weak_patch=_SymbolPatch(features_mod, "resolve_gap", interpolate),
            weak=oracle,
        )

    def test_cap_rss_checkpoint(self) -> None:
        limit = (512 * 1024 * 1024) + 1

        def success() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "receipt.json"
                code, _err = _capture_main(_cli_args(path))
                assert code == 0 and path.exists()

        def over() -> None:
            saved = guards.peak_rss_bytes
            guards.peak_rss_bytes = lambda: limit
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = Path(temp_dir) / "receipt.json"
                    code, err = _capture_main(_cli_args(path))
                    assert code == 4 and "INCOMPLETE:CAP_RSS" in err and not path.exists()
            finally:
                guards.peak_rss_bytes = saved

        run_matrix(
            fault_id="CAP_RSS",
            symbol="guards.assert_rss",
            call_path="fixture_cli.run_fixture->preemptive_checkpoint",
            assertion_id="over_rss_writes_no_receipt",
            sentinel="INCOMPLETE:CAP_RSS",
            note="synthetic meter; not a physical 512MiB allocation; not ceiling proof",
            base=success,
            control_patch=_SymbolPatch(guards, "assert_rss", lambda value: None),
            control=success,
            intact=over,
            weak_patch=_SymbolPatch(guards, "assert_rss", lambda value: None),
            weak=over,
        )

    def test_cap_command_checkpoint(self) -> None:
        saved_clock = fixture_cli.CommandClock

        def success() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "receipt.json"
                code, _err = _capture_main(_cli_args(path))
                assert code == 0 and path.exists()

        def over() -> None:
            fixture_cli.CommandClock = _SlowClock
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = Path(temp_dir) / "receipt.json"
                    code, err = _capture_main(_cli_args(path))
                    assert code == 4 and "INCOMPLETE:CAP_COMMAND_SECONDS" in err and not path.exists()
            finally:
                fixture_cli.CommandClock = saved_clock

        run_matrix(
            fault_id="CAP_COMMAND",
            symbol="guards.assert_elapsed",
            call_path="fixture_cli.run_fixture->preemptive_checkpoint",
            assertion_id="over_command_writes_no_receipt",
            sentinel="INCOMPLETE:CAP_COMMAND_SECONDS",
            note="synthetic 121s clock; wall clock was not 121s; not ceiling proof",
            base=success,
            control_patch=_SymbolPatch(guards, "assert_elapsed", lambda *args, **kwargs: None),
            control=success,
            intact=over,
            weak_patch=_ComboPatch(
                [
                    _SymbolPatch(fixture_cli, "CommandClock", _SlowClock),
                    _SymbolPatch(guards, "assert_elapsed", lambda *args, **kwargs: None),
                ]
            ),
            weak=over,
        )

    def test_cap_suite_checkpoint(self) -> None:
        def ok() -> None:
            guards.preemptive_checkpoint(None)

        def over() -> None:
            saved = guards.process_elapsed
            guards.process_elapsed = lambda: 601.0
            try:
                try:
                    guards.preemptive_checkpoint(None)
                except guards.CapExhausted as exc:
                    assert exc.code == "INCOMPLETE:CAP_SUITE_SECONDS"
                    return
                raise AssertionError("SUITE_CAP_MISSED")
            finally:
                guards.process_elapsed = saved

        run_matrix(
            fault_id="CAP_SUITE",
            symbol="guards.assert_elapsed",
            call_path="preemptive_checkpoint",
            assertion_id="suite_over_600_halts",
            sentinel="INCOMPLETE:CAP_SUITE_SECONDS",
            note="synthetic process elapsed; not a 600s wall clock",
            base=ok,
            control_patch=_SymbolPatch(guards, "assert_elapsed", lambda *args, **kwargs: None),
            control=ok,
            intact=over,
            weak_patch=_ComboPatch(
                [
                    _SymbolPatch(guards, "process_elapsed", lambda: 601.0),
                    _SymbolPatch(guards, "assert_elapsed", lambda *args, **kwargs: None),
                ]
            ),
            weak=over,
        )

    def test_cap_workers_checkpoint(self) -> None:
        def success() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "receipt.json"
                code, _err = _capture_main(_cli_args(path, "1"))
                assert code == 0 and path.exists()

        def over() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "receipt.json"
                code, err = _capture_main(_cli_args(path, "3"))
                assert code == 4 and "INCOMPLETE:CAP_WORKERS" in err and not path.exists()

        run_matrix(
            fault_id="CAP_WORKERS",
            symbol="guards.assert_workers",
            call_path="fixture_cli.run_fixture",
            assertion_id="three_workers_writes_no_receipt",
            sentinel="INCOMPLETE:CAP_WORKERS",
            note="integer gate only; no extra processes are spawned",
            base=success,
            control_patch=_SymbolPatch(guards, "assert_workers", lambda workers: None),
            control=success,
            intact=over,
            weak_patch=_SymbolPatch(guards, "assert_workers", lambda workers: None),
            weak=over,
        )

    def test_cap_events_checkpoint(self) -> None:
        manifest = build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])
        manifest["segment"]["members"] = [{} for _ in range(2049)]

        def oracle() -> None:
            try:
                adapter.normalize_manifest(manifest)
            except ContractError as exc:
                assert exc.code == "INCOMPLETE:CAP_EVENTS"
                return
            raise AssertionError("EVENTS_TRUNCATED_OR_ACCEPTED")

        run_matrix(
            fault_id="CAP_EVENTS",
            symbol="normalized_adapter.assert_event_count",
            call_path="normalize_manifest",
            assertion_id="2049_events_halt_before_members",
            sentinel="INCOMPLETE:CAP_EVENTS",
            note="count gate; members are empty dicts and are not truncated to 2048",
            base=oracle,
            control_patch=_SymbolPatch(adapter, "assert_event_count", lambda count: None),
            control=lambda: adapter.normalize_manifest(build_manifest([row("m-a1", 0, "A", 1, 1000, 1)])),
            intact=oracle,
            weak_patch=_SymbolPatch(adapter, "assert_event_count", lambda count: None),
            weak=oracle,
        )

    def test_cap_definitions_checkpoint(self) -> None:
        spec = clone(load_spec())
        spec["families"]["singles"] = ["A"] * 257
        spec["families"]["unordered_pairs"] = []
        spec["families"]["unordered_triples"] = []
        spec["families"]["ordered_pairs"] = []

        def oracle() -> None:
            try:
                fixture_enumerate.enumerate_fixture(spec)
            except ContractError as exc:
                assert exc.code == "INCOMPLETE:CAP_DEFINITIONS"
                return
            raise AssertionError("DEFINITIONS_TRUNCATED_OR_ACCEPTED")

        run_matrix(
            fault_id="CAP_DEFINITIONS",
            symbol="enumerate.assert_definition_count",
            call_path="enumerate_fixture",
            assertion_id="257_definitions_halt",
            sentinel="INCOMPLETE:CAP_DEFINITIONS",
            note="count gate before emit",
            base=oracle,
            control_patch=_SymbolPatch(fixture_enumerate, "assert_definition_count", lambda count: None),
            control=lambda: self._ids(),
            intact=oracle,
            weak_patch=_SymbolPatch(fixture_enumerate, "assert_definition_count", lambda count: None),
            weak=oracle,
        )

    def test_cap_receipt_bytes(self) -> None:
        payload = "x" * ((8 * 1024 * 1024) + 1)

        def write_over() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "too-big.json"
                try:
                    guards.guarded_write_text(path, payload, ROOT)
                except guards.CapExhausted as exc:
                    assert exc.code == "INCOMPLETE:CAP_RECEIPT_BYTES"
                    assert not path.exists()
                    return
                raise AssertionError("OVERSIZE_RECEIPT_WRITTEN")

        def write_small() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "small.json"
                guards.guarded_write_text(path, "{}", ROOT)
                assert path.exists()

        run_matrix(
            fault_id="CAP_RECEIPT",
            symbol="guards.assert_receipt_size",
            call_path="guarded_write_text",
            assertion_id="over_8mib_writes_no_file",
            sentinel="INCOMPLETE:CAP_RECEIPT_BYTES",
            note="real 8388609-byte payload outside the checkout",
            base=write_over,
            control_patch=_SymbolPatch(guards, "assert_receipt_size", lambda size: None),
            control=write_small,
            intact=write_over,
            weak_patch=_SymbolPatch(guards, "assert_receipt_size", lambda size: None),
            weak=write_over,
        )

    def test_z_fault_matrix_complete(self) -> None:
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
            "CAP_RSS",
            "CAP_COMMAND",
            "CAP_SUITE",
            "CAP_WORKERS",
            "CAP_EVENTS",
            "CAP_DEFINITIONS",
            "CAP_RECEIPT",
        }
        seen = {item["fault_id"] for item in MATRIX}
        self.assertTrue(required <= seen)
        for item in MATRIX:
            self.assertEqual(item["base"], "HELD", item["fault_id"])
            self.assertEqual(item["control"], "HELD", item["fault_id"])
            self.assertEqual(item["intact"], "HELD", item["fault_id"])
            self.assertEqual(item["weak"], "ORACLE_VIOLATED", item["fault_id"])
            self.assertIs(item["restored"], True, item["fault_id"])
        guards.preemptive_checkpoint(None)
        self.assertLess(time.perf_counter() - SUITE_STARTED, 600)
        self.assertLess(guards.peak_rss_bytes(), 512 * 1024 * 1024)


if __name__ == "__main__":
    outcome = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2).result
    destination = Path("/opt/cursor/artifacts/a990-fault-matrix.json")
    destination.write_text(json.dumps({"rows": MATRIX}, indent=2) + "\n", encoding="utf-8")
    print("MATRIX_ROWS", len(MATRIX))
    print("MATRIX_PATH", destination)
    print("SUITE_ELAPSED_SECONDS", f"{time.perf_counter() - SUITE_STARTED:.6f}")
    print("PEAK_RSS_BYTES", guards.peak_rss_bytes())
    sys.exit(0 if outcome.wasSuccessful() else 1)
