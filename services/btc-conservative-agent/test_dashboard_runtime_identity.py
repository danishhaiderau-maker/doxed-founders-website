"""Keep execution-build, collector, writer and deployed revision identities distinct."""

from pathlib import Path


BOT_SOURCE = Path(__file__).resolve().parent / "bot.py"


def test_dashboard_renders_runtime_identity_from_api_state() -> None:
    source = BOT_SOURCE.read_text(encoding="utf-8")

    assert "Execution/UI build" in source
    assert 'id="collectorVersionBanner"' in source
    assert 'id="runtimeRevisionBanner"' in source
    assert 'id="legacyCollectorVersionBanner"' in source
    assert "safeText('collectorVersionBanner', d.collector_version || 'UNKNOWN')" in source
    assert "safeText('runtimeRevisionBanner', d.git_rev || d.source_git_rev || 'UNKNOWN')" in source
    assert "safeText('legacyCollectorVersionBanner', d.legacy_collector_version || 'none')" in source

