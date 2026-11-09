"""Windows Job Object start order. The live case runs only on Windows CI."""
import os
import sys

import pytest

from research.ext2_supervisor import (
    CREATE_SUSPENDED,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    start_worker,
    windows_child_start_order,
    windows_job_members,
)


def test_child_is_created_suspended_then_assigned_then_resumed():
    assert windows_child_start_order() == ("create_suspended", "assign_job", "resume")
    assert CREATE_SUSPENDED == 0x4
    assert JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000
    plan = windows_job_members(10, 11)
    assert plan["supervisor_outside_job"] is True
    assert 10 not in plan["members"]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")
def test_suspended_child_is_assigned_before_it_runs():
    proc, job = start_worker([sys.executable, "-c", "import sys; sys.exit(0)"], env={"PATH": os.environ.get("PATH", "")}, cwd=os.getcwd())
    assert job is not None
    assert proc.wait(timeout=15) == 0
    assert os.getpid() not in windows_job_members(os.getpid(), proc.pid)["members"]
