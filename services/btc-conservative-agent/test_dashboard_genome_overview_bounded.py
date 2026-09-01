from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parent / "research" / "research_dashboard.py"
).read_text(encoding="utf-8")


def test_genome_overview_summarizes_large_collection_evidence():
    assert "const compactCollection = {" in SOURCE
    assert "effective_paper_execution_identity_count" in SOURCE
    assert "orphan_expected_order_count" in SOURCE
    assert "full_report: '/safe-policy-genome-v3.1'" in SOURCE
    assert "JSON.stringify({epoch_id:d.epoch_id, collection:c" not in SOURCE
    assert "JSON.stringify({collection:c, blockers:d.blockers}" not in SOURCE
