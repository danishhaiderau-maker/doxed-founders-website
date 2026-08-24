from pathlib import Path


SOURCE = Path(__file__).parent / "research" / "research_dashboard.py"


def test_genome_navigation_is_not_hard_coded_unavailable():
    source = SOURCE.read_text(encoding="utf-8")

    assert '("genome", "Safe Policy Genome V3.1"' in source
    assert "Genome (Unavailable)" not in source
    assert "genome: ['CURRENT V3.1 SAFE POLICY GENOME'" in source
    assert "setEvidenceScope('genome', 'SOURCE UNAVAILABLE'" in source
    assert "setEvidenceScope('genome', 'CURRENT V3.1 SAFE POLICY GENOME'" in source


def test_restored_non_summary_tab_still_loads_global_provenance():
    source = SOURCE.read_text(encoding="utf-8")

    assert "if (activeSection !== 'summary') void loadSummary();" in source
    assert source.index("if (activeSection !== 'summary') void loadSummary();") < source.index(
        "show(activeSection);"
    )


def test_dashboard_source_has_no_double_encoded_separator():
    source = SOURCE.read_text(encoding="utf-8")

    assert "Â·" not in source
