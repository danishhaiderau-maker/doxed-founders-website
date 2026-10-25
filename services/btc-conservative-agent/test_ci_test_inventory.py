"""Drift guard: every test file is in ci/unit_test_allowlist.txt or ci/known_red.txt (with owner and date)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).parent
CI = ROOT / "ci"


def _entries(name):
    out = []
    for line in (CI / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _deploy_step_script_tests():
    """Every ../../scripts test file the Fly deploy job's pytest/python commands reference (runner cwd = ROOT)."""
    wf = (ROOT.parent.parent / ".github" / "workflows" / "fly-bot-deploy.yml").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\.\./\.\./scripts/(?:[\w-]+/)*test[_-][\w-]+\.py", wf)))


def _all_test_files():
    local = {p.relative_to(ROOT).as_posix() for p in list(ROOT.glob("test_*.py")) + list(ROOT.glob("tests/test_*.py"))}
    return sorted(local | set(_deploy_step_script_tests()))


def test_fly_deploy_step_tests_run_in_pr_ci():
    """Deploy run 40147646292 (25 Oct 2026) died on a scripts/ test PR CI never ran: every deploy-step test is allowlisted."""
    scripts = _deploy_step_script_tests()
    assert "../../scripts/test_fly_postdeploy_active_gate.py" in scripts, scripts
    assert all((ROOT / f).is_file() for f in scripts), scripts
    missing = sorted(set(scripts) - set(_entries("unit_test_allowlist.txt")))
    assert not missing, f"Fly deploy-step tests missing from ci/unit_test_allowlist.txt: {missing}"


def test_every_test_file_is_allowlisted_or_known_red():
    allow = set(_entries("unit_test_allowlist.txt"))
    red = {line.split("|")[0].strip() for line in _entries("known_red.txt")}
    missing = [f for f in _all_test_files() if f not in allow and f not in red]
    assert not missing, f"test files in neither ci/unit_test_allowlist.txt nor ci/known_red.txt: {missing}"


def test_lists_are_disjoint_and_point_at_real_files():
    allow = set(_entries("unit_test_allowlist.txt"))
    red = {line.split("|")[0].strip() for line in _entries("known_red.txt")}
    files = set(_all_test_files())
    assert not (allow & red), sorted(allow & red)
    assert not ((allow | red) - files), sorted((allow | red) - files)


def test_known_red_entries_have_owner_date_and_reason():
    for name in ("known_red.txt", "known_red_tests.txt"):
        for line in _entries(name):
            parts = [p.strip() for p in line.split("|")]
            assert len(parts) >= 4 and parts[1] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[2]) and parts[3], line


def test_deselects_belong_to_allowlisted_files():
    allow = set(_entries("unit_test_allowlist.txt"))
    for line in _entries("known_red_tests.txt"):
        assert line.split("|")[0].strip().split("::")[0] in allow, line


def test_phase_one_files_are_green_and_allowlisted():
    allow = set(_entries("unit_test_allowlist.txt"))
    for f in ("test_paper_fill.py", "test_tile_fill_migration.py", "test_keep_manifest.py", "test_fresh_start_epoch.py",
              "test_integrity_gaps.py", "test_showcase_manual_close.py", "test_ci_test_inventory.py"):
        assert f in allow, f


def test_script_style_list_is_real_and_stays_in_known_red():
    red = {line.split("|")[0].strip() for line in _entries("known_red.txt")}
    files = set(_all_test_files())
    for f in _entries("script_style_tests.txt"):
        assert f in files and f in red, f  # run as `python file`; never also pytest-allowlisted


def test_workflow_runs_script_style_step():
    wf = (ROOT.parent.parent / ".github" / "workflows" / "unit-tests.yml").read_text(encoding="utf-8")
    assert "ci/script_style_tests.txt" in wf and 'python "$f"' in wf
