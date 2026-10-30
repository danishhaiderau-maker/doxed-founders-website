"""HM-approved explicit episode-id quarantine of legacy POLICY_IDENTITY_CONTAMINATION."""
import tempfile
import unittest
from unittest import mock

from research import legacy_policy_quarantine as lpq
from research.research_v3_report import build_safe_policy_genome_v3_report
from research_v3_store import V3EvidenceStore

B = 1.8e9
EP = "episode-legacy-1"
LISTED = {EP: {"policy_ids": ["PATIENT"], "identity_rows_max_ts": B + 1000.0, "lanes": ["L"], "reason": "test"}}


def _seed(store, *, episode=EP, order_ts=B + 500.0, decision_ts=B + 500.0, order_extra=None):
    store.append("opportunity", {"record_id": f"o-{episode}", "episode_id": episode, "signal_ts": B})
    store.append("decision", {
        "record_id": f"d-{episode}", "episode_id": episode, "decision_stage": "LANE_POLICY_VERDICT",
        "policy_id": "PATIENT", "policy_signature": "sig-decision", "policy_epoch_id": "pe-1",
        "event_id": f"ev-{episode}", "decision_ts": decision_ts})
    store.append("order_intent", {
        "record_id": f"i-{episode}", "episode_id": episode, "event_id": f"ev-{episode}", "policy_id": "PATIENT",
        "policy_signature": "sig-order", "policy_epoch_id": "pe-2", "submitted_ts": order_ts, **(order_extra or {})})


def _report(listed=LISTED, **kw):
    with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
        store = V3EvidenceStore(data, epoch_id="epoch-v3")
        _seed(store, **kw)
        with mock.patch.object(lpq, "LEGACY_POLICY_IDENTITY_EPISODES", listed):
            return build_safe_policy_genome_v3_report(data, reports)


class LegacyQuarantineTests(unittest.TestCase):
    def test_listed_episode_with_all_rows_before_bound_is_quarantined_and_excluded_everywhere(self):
        report = _report()
        scope = report["epoch_scope"]
        self.assertNotIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])
        self.assertFalse(scope["policy_signature_divergence"])
        q = scope["quarantined_legacy_episodes"]
        self.assertEqual(q["count"], 1)
        self.assertEqual(q["episodes"][0]["episode_id"], EP)
        self.assertEqual(scope["included_fresh_rows"], 0)
        self.assertEqual(scope["active_opportunity_rows"], 0)
        self.assertEqual(report["collection"]["independent_opportunities"], 0)

    def test_divergent_listed_episode_with_a_post_bound_row_is_still_invalid(self):
        report = _report(order_ts=B + 1500.0)
        self.assertIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])
        q = report["epoch_scope"]["quarantined_legacy_episodes"]
        self.assertEqual(q["count"], 0)
        self.assertEqual(q["rejected_not_quarantined"][0]["reason"], "IDENTITY_ROWS_NEWER_THAN_EVIDENCE_BOUND")
        self.assertEqual(report["epoch_scope"]["included_fresh_rows"], 1)

    def test_episode_not_on_the_list_is_still_invalid(self):
        report = _report(episode="episode-other")
        self.assertIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])
        self.assertEqual(report["epoch_scope"]["quarantined_legacy_episodes"]["count"], 0)

    def test_undated_identity_row_fails_closed_unless_a_dated_sibling_shares_its_event_id(self):
        rows = [{"episode_id": EP, "policy_id": "PATIENT", "policy_signature": "s", "event_id": "e1"}]
        out = lpq.evaluate([EP], rows, LISTED)
        self.assertEqual(out["accepted"], [])
        self.assertEqual(out["rejected"][0]["reason"], "UNDATED_IDENTITY_ROW")
        rows.append({"episode_id": EP, "policy_id": "PATIENT", "policy_signature": "s", "event_id": "e1",
                     "observed_ts": B + 500.0})
        self.assertEqual(lpq.evaluate([EP], rows, LISTED)["accepted"], [EP])

    def test_undated_row_with_a_post_bound_dated_sibling_is_rejected(self):
        rows = [{"episode_id": EP, "policy_id": "PATIENT", "policy_signature": "s", "event_id": "e1"},
                {"episode_id": EP, "policy_id": "PATIENT", "policy_signature": "s", "event_id": "e1",
                 "observed_ts": B + 1500.0}]
        out = lpq.evaluate([EP], rows, LISTED)
        self.assertEqual(out["accepted"], [])
        self.assertEqual(out["rejected"][0]["reason"], "IDENTITY_ROWS_NEWER_THAN_EVIDENCE_BOUND")

    def test_only_creation_time_keys_date_a_row(self):
        row = {"episode_id": EP, "policy_id": "PATIENT", "policy_signature": "s", "signal_ts": B + 1.0,
               "resolution_deadline_ts": B + 9999.0, "mae_ts": B + 5.0, "market_segment_refs": {"end_ts": B + 9999.0}}
        self.assertIsNone(lpq.identity_row_ts(row))  # signal_ts alone is not a creation time
        self.assertEqual(lpq.evaluate([EP], [row], LISTED)["rejected"][0]["reason"], "UNDATED_IDENTITY_ROW")
        row.update({"decision_ts": B + 500.0, "fill_ts": B + 600.0})
        self.assertEqual(lpq.identity_row_ts(row), B + 600.0)

    def test_row_count_must_match_the_recorded_count(self):
        listed = {EP: {**LISTED[EP], "identity_row_count": 2}}
        one = [{"episode_id": EP, "policy_id": "PATIENT", "policy_signature": "s", "observed_ts": B + 1.0}]
        self.assertEqual(lpq.evaluate([EP], one, listed)["rejected"][0]["reason"], "IDENTITY_ROW_COUNT_MISMATCH")
        self.assertEqual(lpq.evaluate([EP], one * 2, listed)["accepted"], [EP])

    def test_the_real_list_is_a_fixed_list_of_concrete_ids(self):
        ids = sorted(lpq.LEGACY_POLICY_IDENTITY_EPISODES)
        self.assertEqual(ids, ["episode-1ff6cd183c4b011958be", "episode-cfacc140c836a55a140c"])
        expect = {"episode-1ff6cd183c4b011958be": (1792281301.0, 20), "episode-cfacc140c836a55a140c": (1792297805.0, 18)}
        for ep, (bound, count) in expect.items():  # each id carries its OWN bound; both pre-date the deploy boot
            spec = lpq.LEGACY_POLICY_IDENTITY_EPISODES[ep]
            self.assertEqual(spec["identity_rows_max_ts"], bound)
            self.assertEqual(spec["identity_row_count"], count)
            self.assertLess(bound, 1792305723.0)  # #713 deploy boot 17:42:03 AEDT
            self.assertEqual(len(spec["policy_ids"]), 2)

    def test_bounds_are_per_id_a_later_episode_is_not_covered_by_an_earlier_bound(self):
        two = {**LISTED, "episode-b": {**LISTED[EP], "identity_rows_max_ts": B + 5000.0}}
        late = {"episode_id": "episode-b", "policy_id": "PATIENT", "policy_signature": "s", "observed_ts": B + 4000.0}
        self.assertEqual(lpq.evaluate(["episode-b"], [late], two)["accepted"], ["episode-b"])
        self.assertEqual(lpq.evaluate(["episode-b"], [late], {"episode-b": LISTED[EP]})["accepted"], [])


    def test_synthetic_listed_rows_are_accepted_with_their_own_bounds_and_counts(self):
        """SYNTHETIC rows only (R22 2026-10-30): real Fly/laptop ledger export deleted; shape matches list bounds/counts."""
        import pathlib
        assert not (pathlib.Path(__file__).parent / "fixtures" / "legacy_policy_quarantine_real_rows.ndjson").exists()
        rows = []
        for ep, spec in lpq.LEGACY_POLICY_IDENTITY_EPISODES.items():
            bound = float(spec["identity_rows_max_ts"])
            count = int(spec["identity_row_count"])
            pids = list(spec["policy_ids"])
            for i in range(count):
                rows.append({
                    "episode_id": ep,
                    "policy_id": pids[i % len(pids)],
                    "policy_signature": f"synthetic-sig-{ep[-6:]}-{i}",
                    "observed_ts": bound - (count - i),  # all <= bound
                    "event_id": f"syn-{ep[-6:]}-{i}",
                    "_fixture": "SYNTHETIC_R22_NO_LIVE_LEDGER",
                })
            rows.append({
                "episode_id": ep, "policy_id": "OTHER", "policy_signature": "x",
                "observed_ts": bound - 1.0, "event_id": f"syn-noise-{ep[-6:]}",
                "_fixture": "SYNTHETIC_R22_NO_LIVE_LEDGER",
            })
        out = lpq.evaluate(list(lpq.LEGACY_POLICY_IDENTITY_EPISODES), rows)
        self.assertEqual(out["rejected"], [])
        self.assertEqual({e["episode_id"]: e["identity_rows"] for e in out["episodes"]},
                         {"episode-1ff6cd183c4b011958be": 20, "episode-cfacc140c836a55a140c": 18})
        for e in out["episodes"]:
            self.assertLessEqual(e["newest_identity_row_ts"], e["evidence_bound_ts"])
        only_first = {k: v for k, v in lpq.LEGACY_POLICY_IDENTITY_EPISODES.items() if k.endswith("11958be")}
        self.assertEqual(lpq.evaluate(["episode-cfacc140c836a55a140c"], rows, only_first)["accepted"], [])


if __name__ == "__main__":
    unittest.main()
