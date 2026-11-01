"""Quarantined pre-existing red (0ea19749). Boss follow-up card 2026-11-01. Do not fix here."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_analyzer_mirror as pub  # noqa: E402


def test_r3_quarantine_lists_match_the_producer_module():
    from research import r3_quarantine

    assert pub.R3_QUARANTINED_FILES == r3_quarantine.QUARANTINED_FILES
    import self_aware.analyzer_sections as sections
    import self_aware.contracts as contracts

    assert sections.GENOME_GRID_R3_QUARANTINED is contracts.GENOME_GRID_R3_QUARANTINED is \
        r3_quarantine.is_active("GENOME_GRID")
