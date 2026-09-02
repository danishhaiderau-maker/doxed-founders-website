import re
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "fly-bot-deploy.yml"
STORE = Path(__file__).with_name("research_v3_store.py")


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
    section = source.split(
        "Prove liveness, execution safety, and exact revision", 1
    )[1].split("Complete receipt bootstrap inside exact-revision maintenance", 1)[0]

    assert "deadline = time.monotonic() + 12 * 60" in section
    assert "while time.monotonic() < deadline" in section
    assert "for _ in range(60)" not in section
    for stage in ("health", "status", "ready"):
        assert f"stage={stage}" in section
    assert "latency_ms=" in section
    assert "stage=request status=failed" in section


def test_postdeploy_receipt_bootstrap_completes_before_paper_resume():
    source = WORKFLOW.read_text(encoding="utf-8")
    gate_name = "Complete receipt bootstrap inside exact-revision maintenance"
    resume_name = "Resume paper execution after exact-revision acceptance"
    assert gate_name in source
    gate_start = source.index(gate_name)
    resume_start = source.index(resume_name)
    assert gate_start < resume_start
    section = source[gate_start:resume_start]
    deploy_job = source.split("  test-and-deploy:", 1)[1]
    assert "timeout-minutes: 90" in deploy_job

    assert "observed_bootstrap_rows = 21_353" in section
    assert "conservative_records_per_cycle = 64" in section
    assert "backlog_interval_seconds = 1" in section
    assert "bootstrap_timeout_seconds = 45 * 60" in section
    assert "minimum_cycles = (" in section
    assert "deadline = time.monotonic() + bootstrap_timeout_seconds" in section
    observed_rows = 21_353
    records_per_cycle = 64
    store = STORE.read_text(encoding="utf-8")
    configured = re.search(r"^_BOOTSTRAP_RECORDS_PER_STEP\s*=\s*(\d+)\s*$", store, re.MULTILINE)
    assert configured is not None
    assert int(configured.group(1)) == records_per_cycle
    assert "_BOOTSTRAP_BYTES_PER_STEP = 8 * 1024 * 1024" in store
    minimum_cycles = (observed_rows + records_per_cycle - 1) // records_per_cycle
    assert 45 * 60 >= minimum_cycles
    assert 'call("/api/pause", {})' in section
    assert section.count('call("/api/pause", {})') == 1
    assert 'call("/api/status")' in section
    assert 'str(status.get("source_git_rev") or "").lower() == expected' in section
    assert 'status.get("execution_paused") is True' in section
    assert 'status.get("manual_admin_pause") is True' in section
    assert 'pipeline.get("owner") is True' in section
    assert 'pipeline.get("running") is True' in section
    assert 'pipeline.get("source_revision_match") is True' in section
    assert 'bootstrap.get("required") is True' in section
    assert 'bootstrap.get("status") == "COMPLETE"' in section
    assert 'bootstrap.get("complete") is True' in section
    assert "receipt bootstrap did not complete before bounded maintenance timeout" in section


def test_lifecycle_worker_start_is_not_conditioned_on_paper_pause_state():
    bot = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    start = bot.index("def _start_lifecycle_pipeline_runtime()")
    stop = bot.index("def _stop_lifecycle_pipeline_runtime", start)
    helper = bot[start:stop]
    assert "manual_admin_pause" not in helper
    assert "execution_paused" not in helper
    assert "runtime_module.start(" in helper
