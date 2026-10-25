"""RK 25 Oct: every new order_intent row carries submitted_ts and observed_ts.

Spies on V3EvidenceStore.append while the existing bridge suite runs, so all
three bridge write sites (paper submit, terminal schedule, v22 mirror) are
exercised by their real fixtures.
"""
import math
import os
import unittest
from unittest import mock

import research_v3_bridge as bridge
import research_v3_store
import test_research_v3_bridge


def test_helper_bases():
    assert bridge._order_intent_ts(10, 12) == {
        "submitted_ts": 10.0, "observed_ts": 12.0, "order_intent_ts_basis": "SOURCE"}
    assert bridge._order_intent_ts(None, 12)["order_intent_ts_basis"] == "SUBMITTED_FROM_OBSERVED"
    assert bridge._order_intent_ts(10, "")["observed_ts"] == 10.0
    out = bridge._order_intent_ts(None, "bad")
    assert out["order_intent_ts_basis"] == "WRITE_TIME_FALLBACK"
    assert out["submitted_ts"] == out["observed_ts"] > 0


def test_every_order_intent_row_from_bridge_suite_is_stamped():
    captured = []
    real_append = research_v3_store.V3EvidenceStore.append

    def spy(self, ledger, row, **kwargs):
        if ledger == "order_intent":
            captured.append(dict(row))
        return real_append(self, ledger, row, **kwargs)

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_research_v3_bridge)
    with mock.patch.object(research_v3_store.V3EvidenceStore, "append", spy):
        with open(os.devnull, "w") as sink:
            unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    assert captured, "bridge suite wrote no order_intent rows"
    kinds = {row.get("intent_kind") for row in captured}
    assert "ACTUAL_PAPER_LIMIT_SUBMIT" in kinds
    assert "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL" in kinds
    assert any(str(row.get("record_id", "")).count(":") == 1 for row in captured), "v22 mirror site not exercised"
    for row in captured:
        for key in ("submitted_ts", "observed_ts"):
            value = row.get(key)
            assert isinstance(value, float) and math.isfinite(value) and value > 0, (row.get("record_id"), key, value)
        assert row.get("order_intent_ts_basis") in {
            "SOURCE", "SUBMITTED_FROM_OBSERVED", "OBSERVED_FROM_SUBMITTED", "WRITE_TIME_FALLBACK"}


def test_paper_submit_site_labels_copied_observed_ts():
    """HM #886 nit: site 1 has only the submit time, so observed_ts is a copy, not SOURCE."""
    captured = []
    real_append = research_v3_store.V3EvidenceStore.append

    def spy(self, ledger, row, **kwargs):
        if ledger == "order_intent":
            captured.append(dict(row))
        return real_append(self, ledger, row, **kwargs)

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_research_v3_bridge)
    with mock.patch.object(research_v3_store.V3EvidenceStore, "append", spy):
        with open(os.devnull, "w") as sink:
            unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    submits = [r for r in captured if r.get("intent_kind") == "ACTUAL_PAPER_LIMIT_SUBMIT"]
    assert submits
    for row in submits:
        assert row["order_intent_ts_basis"] in {"OBSERVED_FROM_SUBMITTED", "WRITE_TIME_FALLBACK"}, row
