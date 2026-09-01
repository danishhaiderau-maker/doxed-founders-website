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
        "DURABLE_PAPER_LIFECYCLE", "PROCESS_AND_BOT_LOG",
        "RELAY_STREAM_VALIDATION", "INCIDENT_LOG_SCAN",
    ):
        assert f"run_probe {label}" in section
    assert "flyctl machine exec" in section
    assert "/app/data/runtime/paper_lifecycle_v1.json" in section
    for field in (
        '"saved_at"', '"snapshot_git_rev"', '"runtime_source_git_rev"',
        '"paper_only"', '"live_armed"', '"open_position_count"',
        '"open_position_id_sample"', '"open_position_ids_truncated"',
        '"pending_order_count"', '"pending_order_id_sample"',
        '"pending_order_ids_truncated"',
    ):
        assert field.replace('"', '\\"') in section
    assert 'assert d.get(\\"schema\\")==\\"paper_lifecycle_v1\\"' in section
    assert 'assert d.get(\\"paper_only\\") is True and d.get(\\"live_armed\\") is False' in section
    assert "assert isinstance(positions,list) and isinstance(orders,list)" in section
    assert "len(positions)<=100000 and len(orders)<=100000" in section
    assert 'upper()==\\"OPEN\\" for x in positions' in section
    assert 'upper()==\\"PENDING\\" for x in orders' in section
    assert "pids[:20]" in section
    assert "oids[:20]" in section
    assert "continuing with the remaining bounded probes" in section


def test_inspection_mode_remains_read_only():
    source = WORKFLOW.read_text(encoding="utf-8")
    section = source.split("jobs:", 1)[1].split("  restart-only:", 1)[0]

    for mutation in (
        "flyctl deploy", "machines restart", "machine restart",
        "machines stop", "machines destroy", "scale count",
    ):
        assert mutation not in section


def test_postdeploy_acceptance_has_one_deadline_and_endpoint_receipts():
    source = WORKFLOW.read_text(encoding="utf-8")
    section = source.split("Prove liveness, execution safety, and exact revision", 1)[1]

    assert "deadline = time.monotonic() + 12 * 60" in section
    assert "while time.monotonic() < deadline" in section
    assert "for _ in range(60)" not in section
    for stage in ("health", "status", "ready"):
        assert f"stage={stage}" in section
    assert "latency_ms=" in section
    assert "stage=request status=failed" in section
