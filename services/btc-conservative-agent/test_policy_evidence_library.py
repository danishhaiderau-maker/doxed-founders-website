import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research.canonical_data_store import append_manifest
from research.policy_evidence_library import (
    PolicyEvidenceLibrary, build_library_manifest, normalize_result,
)
from research.policy_evidence_schema import generation_identity


MANIFEST = {"entry_hash":"manifest", "dataset_epoch":"epoch", "source_revision":"source", "tile_config_signature":"tiles"}


def row(episode, classification, *, cohort="same", world="CONSERVATIVE_BBO_DEPTH_TAPE", offset=0.10, **extra):
    payload = {
        "opportunity_id": "opportunity-" + episode, "episode_id": episode,
        "decision_id": "decision-" + episode, "policy_signature": "policy-" + episode,
        "evidence_world": world, "comparison_cohort_key": cohort,
        "classification": classification, "supported": classification != "UNKNOWN",
        "entry_offset_pct": offset, "family": "ATR_TRAIL", "side": "LONG", "split": "OOS",
    }
    if classification in {"FULL_FILL", "PARTIAL_FILL"}:
        payload["filled_qty"] = 0.001
    payload.update(extra)
    return payload


class PolicyEvidenceLibraryTests(unittest.TestCase):
    @staticmethod
    def _canonical_root(tmp, revision="abcdef123456"):
        root = Path(tmp) / "canonical-research-data"
        root.mkdir()
        append_manifest(root, {
            "dataset_epoch":"epoch", "source_revision":revision,
            "tile_config_signature":"tiles", "collection_started_at":"start",
            "collection_observed_at":"observed", "row_count":0, "opportunity_count":0,
            "dataset_checksum":"checksum", "analyzer_status":"PENDING",
            "analyzer_completed_at":None, "analyzer_schema_version":"v62",
        })
        return root

    def test_classifications_and_point_ten_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PolicyEvidenceLibrary(str(Path(tmp) / "canonical-research-data"), MANIFEST, analyzer_revision="a")
            lib.ingest([row("1", "FULL_FILL"), row("2", "PARTIAL_FILL"), row("3", "NO_FILL"), row("4", "UNKNOWN")])
            result = lib.query({"evidence_world":"CONSERVATIVE_BBO_DEPTH_TAPE", "entry_offset_pct":"0.10"})
            self.assertEqual(result["row_count"], 4)
            self.assertEqual({x["classification"] for x in result["rows"]}, {"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})
            self.assertEqual(result, lib.query({"entry_offset_pct":0.1, "evidence_world":"CONSERVATIVE_BBO_DEPTH_TAPE"}))

    def test_unsupported_is_unknown_and_never_no_fill(self):
        identity = generation_identity(MANIFEST, analyzer_revision="a")
        normalized = normalize_result(row("1", "UNSUPPORTED", supported=False), identity)
        self.assertEqual(normalized["classification"], "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_CANNOT_BE_NO_FILL"):
            normalize_result(row("2", "NO_FILL", supported=False), identity)

    def test_positive_quantity_is_required_for_fills(self):
        identity = generation_identity(MANIFEST, analyzer_revision="a")
        with self.assertRaisesRegex(ValueError, "POSITIVE_FILL_QUANTITY"):
            normalize_result(row("1", "FULL_FILL", filled_qty=0), identity)

    def test_worlds_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PolicyEvidenceLibrary(str(Path(tmp) / "canonical-research-data"), MANIFEST, analyzer_revision="a")
            lib.ingest([row("1", "FULL_FILL"), row("2", "FULL_FILL", world="IDEAL_TOUCH_DIAGNOSTIC")])
            conservative = lib.query({"evidence_world":"CONSERVATIVE_BBO_DEPTH_TAPE"})
            ideal = lib.query({"evidence_world":"IDEAL_TOUCH_DIAGNOSTIC"})
            self.assertEqual(conservative["row_count"], 1)
            self.assertEqual(ideal["row_count"], 1)

    def test_mixed_comparison_cohorts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PolicyEvidenceLibrary(str(Path(tmp) / "canonical-research-data"), MANIFEST, analyzer_revision="a")
            lib.ingest([row("1", "NO_FILL", cohort="a"), row("2", "NO_FILL", cohort="b")])
            with self.assertRaisesRegex(ValueError, "MIXED_COMPARISON_COHORTS"):
                lib.query({"evidence_world":"CONSERVATIVE_BBO_DEPTH_TAPE"})

    def test_distinct_opportunities_decisions_and_cohorts_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PolicyEvidenceLibrary(str(Path(tmp) / "canonical-research-data"), MANIFEST, analyzer_revision="a")
            base = row("1", "NO_FILL")
            variants = [
                base,
                {**base, "opportunity_id": "opportunity-other"},
                {**base, "decision_id": "decision-other"},
                {**base, "comparison_cohort_key": "other-cohort"},
            ]
            self.assertEqual(lib.ingest(variants), 4)
            self.assertEqual(lib.cache.select({
                "evidence_world": "CONSERVATIVE_BBO_DEPTH_TAPE", "limit": 100,
            }).__len__(), 4)

    def test_ingest_invalidates_cached_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PolicyEvidenceLibrary(str(Path(tmp) / "canonical-research-data"), MANIFEST, analyzer_revision="a")
            lib.ingest([row("1", "NO_FILL")])
            first = lib.query({"evidence_world": "CONSERVATIVE_BBO_DEPTH_TAPE"})
            self.assertEqual(first["row_count"], 1)
            lib.ingest([row("2", "NO_FILL")])
            second = lib.query({"evidence_world": "CONSERVATIVE_BBO_DEPTH_TAPE"})
            self.assertEqual(second["row_count"], 2)

    def test_v3_event_id_is_preserved_as_decision_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PolicyEvidenceLibrary(str(Path(tmp) / "canonical-research-data"), MANIFEST, analyzer_revision="a")
            event_row = row("1", "NO_FILL")
            event_row["event_id"] = event_row.pop("decision_id")
            lib.ingest([event_row])
            stored = lib.query({"evidence_world": "CONSERVATIVE_BBO_DEPTH_TAPE"})["rows"][0]
            self.assertEqual(stored["decision_id"], event_row["event_id"])

    def test_library_manifest_is_atomic_and_current_identity_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._canonical_root(tmp)
            destination = Path(tmp) / "policy_evidence_library_manifest.json"
            destination.write_text('{"old":true}', encoding="utf-8")
            payload = build_library_manifest(
                root, analyzer_revision="abcdef1234567890", destination=destination,
            )
            published = __import__("json").loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(published["generation"], payload["generation"])
            self.assertEqual(payload["cache_status"], "NOT_BUILT")
            self.assertFalse(payload["evaluation_triggered"])
            self.assertFalse(list(destination.parent.glob(".policy_evidence_library_manifest.json.*.tmp")))

    def test_stale_analyzer_identity_is_rejected_and_old_file_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._canonical_root(tmp)
            destination = Path(tmp) / "policy_evidence_library_manifest.json"
            destination.write_text('{"old":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "REVISION_MISMATCH"):
                build_library_manifest(root, analyzer_revision="different", destination=destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), '{"old":true}')

    def test_atomic_replace_failure_preserves_prior_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._canonical_root(tmp)
            destination = Path(tmp) / "policy_evidence_library_manifest.json"
            destination.write_text('{"old":true}', encoding="utf-8")
            with mock.patch("research.policy_evidence_library.os.replace", side_effect=OSError("fail")):
                with self.assertRaises(OSError):
                    build_library_manifest(root, analyzer_revision="abcdef123456", destination=destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), '{"old":true}')


if __name__ == "__main__":
    unittest.main()
