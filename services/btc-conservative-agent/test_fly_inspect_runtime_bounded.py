from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "fly-bot-deploy.yml"


def test_read_only_fly_inspection_cannot_hang_the_entire_job():
    source = WORKFLOW.read_text(encoding="utf-8")
    section = source.split("jobs:", 1)[1].split("  restart-only:", 1)[0]

    assert "run_probe()" in section
    assert "timeout --signal=TERM --kill-after=5s 45s" in section
    assert "timeout --signal=TERM --kill-after=5s 30s flyctl machines list" in section
    for label in (
        "FLY_STATUS", "MACHINE_STATUS", "RECENT_PLATFORM_LOGS",
        "PROCESS_AND_BOT_LOG", "RELAY_STREAM_VALIDATION", "INCIDENT_LOG_SCAN",
    ):
        assert f"run_probe {label}" in section
    assert "continuing with the remaining bounded probes" in section


def test_inspection_mode_remains_read_only():
    source = WORKFLOW.read_text(encoding="utf-8")
    section = source.split("jobs:", 1)[1].split("  restart-only:", 1)[0]

    for mutation in (
        "flyctl deploy", "machines restart", "machine restart",
        "machines stop", "machines destroy", "scale count",
    ):
        assert mutation not in section
