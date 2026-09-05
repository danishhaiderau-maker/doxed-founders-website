import pytest
from research_reset_kernel_continuity import _verify, BOOT_ID, FAILURE_MTIME


def fixture(root):
    (root / "663").mkdir()
    (root / "sys/kernel/random").mkdir(parents=True)
    (root / "sys/kernel/random/boot_id").write_text(BOOT_ID)
    (root / "stat").write_text("btime 1788604316\n")
    fields = ["S"] + ["0"] * 18 + ["106"]
    (root / "663/stat").write_text("663 (python (worker)) " + " ".join(fields))


def test_exact_kernel_identity(tmp_path):
    fixture(tmp_path)
    assert _verify(tmp_path, 1788605000, FAILURE_MTIME, 100)["start_ticks"] == 106


@pytest.mark.parametrize("defect", ["boot", "restart", "mtime", "anchor", "missing"])
def test_refuses_noncontinuous_incident(tmp_path, defect):
    fixture(tmp_path)
    anchor, mtime = 1788605000, FAILURE_MTIME
    if defect == "boot": (tmp_path / "sys/kernel/random/boot_id").write_text("other")
    if defect == "restart":
        path = tmp_path / "663/stat"; path.write_text(path.read_text().replace("106", "107"))
    if defect == "mtime": mtime += 1
    if defect == "anchor": anchor = 1788604000
    if defect == "missing": (tmp_path / "663/stat").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        _verify(tmp_path, anchor, mtime, 100)
