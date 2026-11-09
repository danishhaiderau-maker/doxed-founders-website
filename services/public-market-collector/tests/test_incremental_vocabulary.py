"""HM B1: incremental semantics must not share state across vocabularies."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_market_collector import market
from public_market_collector.vocabulary import CURRENT_VOCAB, LEGACY_VOCAB, ReviewedVocabMapping, VocabularyError

MISSING = object()


def record(version=MISSING, *, channel="trade", trade_id=123, seq=1):
    payloads = {
        "trade": [7, "te", [trade_id, 1000, 1, 6000]],
        "book_snapshot": [7, [[6000, 1, 1], [6100, 1, -1]]],
        "book_delta": [7, [6000, 1, 2]],
        "gap": {"cause": "disconnect"},
        "heartbeat": [7, "hb"],
        "info": {"event": "info", "version": 2},
    }
    row = {"channel": channel, "payload": payloads[channel], "raw_id": f"fixture-{channel}-{trade_id}",
           "venue_seq": seq, "stream": "raw-trades" if channel == "trade" else "raw-book"}
    if version is not MISSING:
        row["record_vocab_v"] = version
    return row


def mapping():
    """Synthetic converter and review reference only; no production approval."""
    def convert(row):
        row["record_vocab_v"] = CURRENT_VOCAB
        return row
    return ReviewedVocabMapping("b" * 64, convert)


def rich_state(version=CURRENT_VOCAB):
    state = market.FeedState(prec="P0", seq_scope="connection")
    market.step(state, record(version, channel="book_snapshot", seq=1), seq_layout=None, inplace=True)
    market.step(state, record(version, trade_id=123, seq=2), seq_layout=None, inplace=True)
    return state


class IncrementalVocabularyTests(unittest.TestCase):
    def test_hm_exact_reproducer_now_refuses_second_record(self):
        # Verbatim HM fixture, copy path by default.
        state = market.FeedState(prec="P0", seq_scope=None)
        old = {"channel": "trade", "payload": [7, "te", [123, 1000, 1, 6000]], "raw_id": "old"}
        new = {**old, "raw_id": "new", "record_vocab_v": "pmc_vocab_v2"}
        result = market.step(state, old, seq_layout=None)
        state = result.state
        self.assertEqual([(n["record_vocab_v"], n["counted"], n["duplicate"]) for n in result.norms],
                         [("pmc_vocab_v1", True, False)])
        before = deepcopy(state)
        with self.assertRaises(VocabularyError):
            market.step(state, new, seq_layout=None)
        self.assertEqual(state, before)
        self.assertEqual(state.seen_trade_ids, {123})

    def test_first_record_binds_implied_v1_explicit_v1_or_v2_both_paths(self):
        for version, expected in ((MISSING, LEGACY_VOCAB), (LEGACY_VOCAB, LEGACY_VOCAB), (CURRENT_VOCAB, CURRENT_VOCAB)):
            for inplace in (False, True):
                with self.subTest(version=expected, implied=version is MISSING, inplace=inplace):
                    state = market.FeedState(prec="P0", seq_scope=None)
                    output = market.step(state, record(version), seq_layout=None, inplace=inplace)
                    self.assertEqual(output.state.record_vocab_v, expected)
                    self.assertEqual(output.norms[0]["record_vocab_v"], expected)
                    self.assertTrue(output.norms[0]["counted"])
                    self.assertEqual(state.record_vocab_v, expected if inplace else None)
                    self.assertEqual(output.state is state, inplace)

    def test_info_first_also_binds_vocabulary_before_any_data(self):
        for inplace in (False, True):
            result = market.step(market.FeedState(prec="P0", seq_scope=None), record(channel="info"),
                                 seq_layout=None, inplace=inplace)
            self.assertEqual(result.state.record_vocab_v, LEGACY_VOCAB)
            with self.assertRaises(VocabularyError):
                market.step(result.state, record(CURRENT_VOCAB), seq_layout=None, inplace=inplace)

    def test_both_directions_refuse_on_copy_path(self):
        self._directions(inplace=False)

    def test_both_directions_refuse_on_inplace_path(self):
        self._directions(inplace=True)

    def _directions(self, *, inplace):
        for first, second in ((MISSING, CURRENT_VOCAB), (LEGACY_VOCAB, CURRENT_VOCAB),
                              (CURRENT_VOCAB, MISSING), (CURRENT_VOCAB, LEGACY_VOCAB)):
            with self.subTest(first=str(first), second=str(second), inplace=inplace):
                state = market.step(market.FeedState(prec="P0", seq_scope=None), record(first),
                                    seq_layout=None, inplace=inplace).state
                before = deepcopy(state)
                with self.assertRaises(VocabularyError):
                    market.step(state, record(second), seq_layout=None, inplace=inplace)
                self.assertEqual(state, before)

    def test_mismatch_refuses_before_copying_state(self):
        state = rich_state(LEGACY_VOCAB)
        with patch.object(market, "_copy_state", side_effect=AssertionError("copy before guard")) as copier:
            with self.assertRaises(VocabularyError):
                market.step(state, record(CURRENT_VOCAB), seq_layout=None)
            copier.assert_not_called()

    def test_refusal_preserves_all_state_values_and_container_identities(self):
        for inplace in (False, True):
            for old, new in ((LEGACY_VOCAB, CURRENT_VOCAB), (CURRENT_VOCAB, LEGACY_VOCAB)):
                for channel in ("trade", "book_snapshot", "book_delta", "gap", "heartbeat", "info"):
                    with self.subTest(inplace=inplace, old=old, new=new, channel=channel):
                        state = rich_state(old)
                        before = deepcopy(state)
                        objects = (state.book, state.book.bids, state.book.asks, state.book.orders,
                                   state.last_chan_seq, state.seen_trade_ids, state.seen_trade_order)
                        with self.assertRaises(VocabularyError):
                            market.step(state, record(new, channel=channel, trade_id=999, seq=99),
                                        seq_layout=None, inplace=inplace)
                        self.assertEqual(state, before)
                        after = (state.book, state.book.bids, state.book.asks, state.book.orders,
                                 state.last_chan_seq, state.seen_trade_ids, state.seen_trade_order)
                        self.assertTrue(all(a is b for a, b in zip(objects, after)))

    def test_unknown_version_refuses_before_mutation_on_new_and_bound_states(self):
        for inplace in (False, True):
            for bound in (False, True):
                for unknown in (None, "", "pmc_vocab_v3", [], False):
                    with self.subTest(inplace=inplace, bound=bound, unknown=unknown):
                        state = rich_state() if bound else market.FeedState(prec="P0", seq_scope="connection")
                        before = deepcopy(state)
                        with self.assertRaises(VocabularyError):
                            market.step(state, record(unknown, trade_id=999, seq=99),
                                        seq_layout=None, inplace=inplace)
                        self.assertEqual(state, before)

    def test_unknown_version_refuses_before_copying(self):
        state = market.FeedState(prec="P0", seq_scope=None)
        with patch.object(market, "_copy_state", side_effect=AssertionError("copy before validation")) as copier:
            with self.assertRaises(VocabularyError):
                market.step(state, record("pmc_vocab_v3"), seq_layout=None)
            copier.assert_not_called()
        self.assertIsNone(state.record_vocab_v)

    def test_unknown_state_binding_is_not_silently_replaced(self):
        for inplace in (False, True):
            state = rich_state()
            state.record_vocab_v = "pmc_vocab_v3"
            before = deepcopy(state)
            with self.assertRaises(VocabularyError):
                market.step(state, record(CURRENT_VOCAB), seq_layout=None, inplace=inplace)
            self.assertEqual(state, before)

    def test_copy_state_preserves_binding_and_many_copy_steps_cannot_erase_it(self):
        for version in (MISSING, LEGACY_VOCAB, CURRENT_VOCAB):
            expected = LEGACY_VOCAB if version is MISSING else version
            state = rich_state(version)
            cloned = market._copy_state(state)
            self.assertEqual(cloned.record_vocab_v, expected)
            self.assertEqual(state, cloned)
            for n in range(3, 8):
                state = market.step(state, record(version, trade_id=1000+n, seq=n), seq_layout=None).state
                self.assertEqual(state.record_vocab_v, expected)
            other = CURRENT_VOCAB if expected == LEGACY_VOCAB else MISSING
            with self.assertRaises(VocabularyError):
                market.step(state, record(other, trade_id=9999, seq=8), seq_layout=None)

    def test_same_vocab_still_deduplicates_without_modifying_copy_source(self):
        for version in (MISSING, LEGACY_VOCAB, CURRENT_VOCAB):
            for inplace in (False, True):
                state = market.step(market.FeedState(prec="P0", seq_scope=None), record(version),
                                    seq_layout=None, inplace=inplace).state
                before = deepcopy(state)
                result = market.step(state, record(version), seq_layout=None, inplace=inplace)
                self.assertFalse(result.norms[0]["counted"])
                self.assertTrue(result.norms[0]["duplicate"])
                if not inplace:
                    self.assertEqual(state, before)

    def test_explicit_mapping_from_first_record_allows_reviewed_mixed_stream(self):
        for inplace in (False, True):
            for versions in ((MISSING, CURRENT_VOCAB), (CURRENT_VOCAB, LEGACY_VOCAB)):
                state = market.FeedState(prec="P0", seq_scope=None)
                inputs = [record(version) for version in versions]
                before = deepcopy(inputs)
                outputs = []
                for row in inputs:
                    result = market.step(state, row, seq_layout=None, inplace=inplace, reviewed_mapping=mapping())
                    state = result.state
                    outputs.extend(result.norms)
                    self.assertEqual(state.record_vocab_v, CURRENT_VOCAB)
                self.assertEqual(inputs, before)
                self.assertEqual([n["record_vocab_v"] for n in outputs], [CURRENT_VOCAB, CURRENT_VOCAB])
                self.assertEqual([n["counted"] for n in outputs], [True, False])
                self.assertTrue(outputs[1]["duplicate"])

    def test_mapping_can_convert_incoming_v1_into_an_existing_v2_state(self):
        for inplace in (False, True):
            state = market.step(market.FeedState(prec="P0", seq_scope=None), record(CURRENT_VOCAB),
                                seq_layout=None, inplace=inplace).state
            output = market.step(state, record(MISSING), seq_layout=None, inplace=inplace, reviewed_mapping=mapping())
            self.assertEqual(output.state.record_vocab_v, CURRENT_VOCAB)
            self.assertTrue(output.norms[0]["duplicate"])

    def test_late_mapping_cannot_relabel_history_of_already_populated_v1_state(self):
        for inplace in (False, True):
            state = rich_state(LEGACY_VOCAB)
            before = deepcopy(state)
            with self.assertRaises(VocabularyError):
                market.step(state, record(CURRENT_VOCAB), seq_layout=None, inplace=inplace, reviewed_mapping=mapping())
            self.assertEqual(state, before)

    def test_invalid_mapping_or_conversion_refuses_without_state_changes(self):
        for inplace in (False, True):
            for invalid in (False, True, {}, lambda row: row):
                state = market.FeedState(prec="P0", seq_scope=None)
                before = deepcopy(state)
                with self.assertRaises(VocabularyError):
                    market.step(state, record(CURRENT_VOCAB), seq_layout=None, inplace=inplace, reviewed_mapping=invalid)
                self.assertEqual(state, before)
            for converter in (lambda row: row, lambda row: None,
                              lambda row: {**row, "record_vocab_v": "pmc_vocab_v3"}):
                state = market.FeedState(prec="P0", seq_scope=None)
                before = deepcopy(state)
                with self.assertRaises(VocabularyError):
                    market.step(state, record(MISSING), seq_layout=None, inplace=inplace,
                                reviewed_mapping=ReviewedVocabMapping("b" * 64, converter))
                self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
