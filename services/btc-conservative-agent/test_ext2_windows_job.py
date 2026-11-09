"""Windows Job Object. The live cases run only on the windows-latest CI job."""
import json
import os
import sys

import pytest

from research.ext2_supervisor import (
    CREATE_SUSPENDED,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    job_attestation,
    job_struct_sizes,
    start_worker,
    supervise,
    windows_child_start_order,
    windows_job_members,
    DISK_FLOOR_BYTES,
)


def test_child_is_created_suspended_then_assigned_then_resumed():
    assert windows_child_start_order() == ("create_suspended", "assign_job", "resume")
    assert CREATE_SUSPENDED == 0x4
    assert JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000
    plan = windows_job_members(10, 11)
    assert 10 not in plan["members"] and 10 in plan["excludes"]
    assert "supervisor_outside_job" not in plan  # never asserted; measured by job_attestation


def test_struct_sizes_match_the_x64_abi():
    sizes = job_struct_sizes()
    if sizes["pointer_bits"] == 64:
        assert sizes["extended"] == 144 and sizes["basic"] == 64 and sizes["io"] == 48


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")
def test_live_job_membership_is_measured(tmp_path):
    proc, job = start_worker([sys.executable, "-c", "import time; time.sleep(2)"],
                             env=dict(os.environ), cwd=str(tmp_path))
    try:
        assert job is not None
        att = job_attestation(proc, job)
        assert att["kind"] == "windows_job_object"
        assert att["child_in_job"] is True and att["supervisor_outside_job"] is True
    finally:
        job.close()
    assert proc.wait(timeout=15) == 1  # TerminateJobObject(job, 1) killed the member
    assert job.closed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")
def test_supervise_on_windows_completes_with_a_nonce_marker(tmp_path):
    data = tmp_path / "external-data" / "bitfinex"
    data.mkdir(parents=True)
    out = tmp_path / "runs" / "r1"
    script = (
        "import json, os, pathlib\n"
        "root = pathlib.Path.cwd()\n"
        "(root / 'heartbeat').write_text('a')\n"
        "(root / 'COMPLETE').write_text(json.dumps({'run_nonce': os.environ['EXT2_RUN_NONCE'],"
        " 'card_sha12': os.environ['EXT2_CARD_SHA12'], 'finished_aedt': 'x'}))\n"
    )
    receipt = supervise([sys.executable, "-c", script], data_root=data, output_root=out,
                        disk_free=lambda: DISK_FLOOR_BYTES + 1, hard_stop_sec=60)
    assert receipt["status"] == "COMPLETED", receipt["reason"]
    assert receipt["job"]["kind"] == "windows_job_object"
    assert receipt["job"]["child_in_job"] is True
    assert receipt["job"]["supervisor_outside_job"] is True
    assert json.loads((out / "receipt.json").read_text())["child_exit_code"] == 0
