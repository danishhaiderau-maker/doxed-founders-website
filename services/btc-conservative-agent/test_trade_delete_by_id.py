import csv, json, os, threading
import pytest
import trade_delete_by_id as m


def _mk(tmp_path, ids, extra_dup=None):
    p = tmp_path / "trades_3factor.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trade_id", "pnl", "note"])
        for i, t in enumerate(ids):
            w.writerow([t, str(i), 'a,"b"'])
        if extra_dup:
            w.writerow([extra_dup, "x", "dup"])
    return str(p)


def _ids(p):
    with open(p, newline="") as f:
        return [r["trade_id"] for r in csv.DictReader(f)]


ALL = [f"t-{i}" for i in range(10)]
DEL = ["t-1", "t-4", "t-7"]


def test_sha_matches_rk_file_convention(tmp_path):
    f = tmp_path / "l.json"
    json.dump(DEL, open(f, "w"))
    import hashlib
    assert m.list_sha256(DEL) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_dry_run_is_default_and_writes_nothing(tmp_path):
    p = _mk(tmp_path, ALL)
    before = open(p, "rb").read()
    r = m.delete_trades_by_id(p, DEL, m.list_sha256(DEL))
    assert r["dry_run"] is True and r["removed_count"] == 3 and r["after_count"] == 7
    assert r["snapshot_path"] is None and open(p, "rb").read() == before
    assert not os.path.exists(os.path.join(tmp_path, m.SNAPSHOT_DIRNAME))


@pytest.mark.parametrize("ids,sha,code", [
    ([], None, "EMPTY_LIST"),
    (None, None, "EMPTY_LIST"),
    (["t-1", "t-1"], None, "DUPLICATE_IDS"),
    (["t-1"], "0" * 64, "SHA_MISMATCH"),
    (["t-1", "nope"], None, "IDS_NOT_PRESENT"),
])
def test_refusals_write_nothing(tmp_path, ids, sha, code):
    p = _mk(tmp_path, ALL)
    before = open(p, "rb").read()
    if sha is None and isinstance(ids, list) and ids:
        sha = m.list_sha256(ids)
    with pytest.raises(m.DeleteRefused) as e:
        m.delete_trades_by_id(p, ids, sha, dry_run=False)
    assert e.value.code == code
    assert open(p, "rb").read() == before
    assert not os.path.exists(os.path.join(tmp_path, m.SNAPSHOT_DIRNAME))


def test_id_with_two_rows_in_file_refused(tmp_path):
    p = _mk(tmp_path, ALL, extra_dup="t-4")
    with pytest.raises(m.DeleteRefused) as e:
        m.delete_trades_by_id(p, DEL, m.list_sha256(DEL), dry_run=False)
    assert e.value.code == "ID_MULTIPLE_ROWS_IN_FILE"


def test_real_run_invariant_and_snapshot(tmp_path):
    p = _mk(tmp_path, ALL)
    r = m.delete_trades_by_id(p, DEL, m.list_sha256(DEL), dry_run=False, lock=threading.RLock())
    assert _ids(p) == [t for t in ALL if t not in DEL]
    assert r["before_count"] == 10 and r["after_count"] == 7 and r["removed_count"] == 3
    assert r["remaining_rows_sha256_before"] == r["remaining_rows_sha256_after"]
    assert os.path.isfile(r["snapshot_path"]) and _ids(r["snapshot_path"]) == ALL
    with open(p, newline="") as f:
        assert list(csv.reader(f))[1][2] == 'a,"b"'
    d = m.delete_rollback_snapshot(p, r["snapshot_path"])
    assert d == {"snapshot_path": os.path.realpath(r["snapshot_path"]), "deleted": True, "exists_after": False}


def test_atomic_failure_leaves_original(tmp_path):
    p = _mk(tmp_path, ALL)
    before = open(p, "rb").read()

    def boom(a, b):
        raise OSError("disk")
    with pytest.raises(OSError):
        m.delete_trades_by_id(p, DEL, m.list_sha256(DEL), dry_run=False, _replace=boom)
    assert open(p, "rb").read() == before
    assert not [x for x in os.listdir(tmp_path) if x.endswith(".tmp")]


def test_invariant_breach_restores(tmp_path):
    p = _mk(tmp_path, ALL)
    before = open(p, "rb").read()

    def corrupt(tmp, dst):
        os.replace(tmp, dst)
        with open(dst, "a") as f:
            f.write("t-99,1,x\n")
    with pytest.raises(m.DeleteRefused) as e:
        m.delete_trades_by_id(p, DEL, m.list_sha256(DEL), dry_run=False, _replace=corrupt)
    assert e.value.code == "INVARIANT_FAILED_RESTORED"
    assert open(p, "rb").read() == before


def test_lock_is_held(tmp_path):
    p = _mk(tmp_path, ALL)
    seen = []

    class L:
        def __enter__(s): seen.append("in")
        def __exit__(s, *a): seen.append("out")
    m.delete_trades_by_id(p, DEL, m.list_sha256(DEL), dry_run=False, lock=L())
    assert seen == ["in", "out"]


def test_snapshot_delete_refuses_outside_dir_and_missing(tmp_path):
    p = _mk(tmp_path, ALL)
    with pytest.raises(m.DeleteRefused) as e:
        m.delete_rollback_snapshot(p, p)
    assert e.value.code == "SNAPSHOT_PATH_NOT_ALLOWED"
    os.makedirs(tmp_path / m.SNAPSHOT_DIRNAME)
    with pytest.raises(m.DeleteRefused) as e:
        m.delete_rollback_snapshot(p, str(tmp_path / m.SNAPSHOT_DIRNAME / "x.1.rollback.csv"))
    assert e.value.code == "SNAPSHOT_MISSING"
    assert os.path.exists(p)


def test_bot_routes_are_admin_gated_and_locked():
    import ast, pathlib
    src = pathlib.Path(__file__).with_name("bot.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef)
           and n.name in ("api_admin_trades_delete_by_id", "api_admin_trades_delete_rollback_snapshot")}
    assert len(fns) == 2
    for body in fns.values():
        assert "if not _admin_authed():" in body.split("import trade_delete_by_id")[0]
    d = fns["api_admin_trades_delete_by_id"]
    assert 'body.get("dry_run", True) is not False' in d
    assert "csv_lock.acquire()" in d and "research_gate.acquire()" in d
    assert "CSV_FALLBACK_PENDING" in d
