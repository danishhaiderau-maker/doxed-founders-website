import json

import pytest

import v3_marker_quarantine as vmq
from research_v3_store import V3EvidenceStore

CURRENT = "c225f2b796696a828ec75d1b9e45c162b49e989d"
STALE = {"execution": "7299b2ab750f2496a60a394c14b73d5eb0c29056",
         "decision": "e205cd5c28d9438a9c26c1bd2d527465cef9fe31"}
EPOCH = "epoch-v22-da3e5308a31e370d877c"
ENV = {"SOURCE_GIT_REV": CURRENT}


def _identity(rev):
    return {"epoch_id": EPOCH, "source_revision": rev, "deployed_revision": rev,
            "tile_config_signature": "a" * 64}


def _fixture(tmp_path, positions=()):
    data = tmp_path / "data"
    runtime = data / "runtime"
    markers = runtime / "v3" / "receipts" / "emergency_record_idempotency_v1"
    for ledger, rev in {**STALE, "opportunity": CURRENT, "evidence_failure": CURRENT}.items():
        (markers / ledger).mkdir(parents=True)
        (markers / ledger / "complete.json").write_text(json.dumps(
            {"schema": "emergency_record_index_complete_v1", "ledger": ledger, "identity": _identity(rev)}), "utf-8")
        (markers / ledger / "abc.json").write_text("{}", "utf-8")
    (runtime / "paper_lifecycle_v1.json").write_text(json.dumps(
        {"paper_only": True, "live_armed": False, "positions": list(positions), "pending_orders": []}), "utf-8")
    (runtime / "config-7002.json").write_text(json.dumps({"live_armed": False}), "utf-8")
    return data


def test_preflight_reproduces_the_boundary_conflict_and_clears_after_quarantine(tmp_path, capsys):
    data = _fixture(tmp_path)
    runtime = str(data / "runtime")
    with pytest.raises(ValueError, match="V3 read authority identity conflict"):
        V3EvidenceStore.open_read_only(runtime)
    assert vmq.main(["preflight", "--data-dir", str(data)], env=ENV) == 9
    assert "identity conflict" in json.loads(capsys.readouterr().out)["open_read_only"]

    body = vmq.plan(runtime, CURRENT[:12])
    assert sorted(body["stale"]) == [f"v3/receipts/emergency_record_idempotency_v1/{l}/complete.json"
                                     for l in ("decision", "execution")]
    result = vmq.quarantine(str(data), CURRENT[:12], body["plan_sha256"], stamp="20261004T000000Z")
    assert result["status"] == "COMPLETE" and all(result["checks"].values())
    target = data / "quarantine" / "v3_completeness_markers" / "20261004T000000Z"
    assert json.loads((target / "decision" / "complete.json").read_text("utf-8"))["identity"] == _identity(STALE["decision"])
    assert json.loads((target / "quarantine_manifest.json").read_text("utf-8"))["status"] == "COMPLETE"
    markers = data / "runtime" / "v3" / "receipts" / "emergency_record_idempotency_v1"
    assert (markers / "decision" / "abc.json").is_file() and not (markers / "decision" / "complete.json").exists()
    assert V3EvidenceStore.open_read_only(runtime)._identity_binding() == _identity(CURRENT)
    assert vmq.main(["preflight", "--data-dir", str(data)], env=ENV) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["wal"] == "NOT_PRESENT"


def test_quarantine_binds_to_the_plan_and_refuses_out_of_scope_authorities(tmp_path):
    data = _fixture(tmp_path)
    runtime = str(data / "runtime")
    with pytest.raises(RuntimeError, match="PLAN_CHANGED"):
        vmq.quarantine(str(data), CURRENT[:12], "0" * 64)
    active = data / "runtime" / "v3" / "receipts" / "ledger_generations_v1" / "execution"
    active.mkdir(parents=True)
    (active / "ACTIVE.json").write_text("{}", "utf-8")
    body = vmq.plan(runtime, CURRENT[:12])
    assert body["refusals"]
    with pytest.raises(RuntimeError, match="NOT_APPLICABLE"):
        vmq.quarantine(str(data), CURRENT[:12], body["plan_sha256"])


def test_offline_execute_requires_token_no_bot_process_and_a_flat_lifecycle(tmp_path, capsys):
    data = _fixture(tmp_path)
    args = ["--data-dir", str(data), "--expected-rev", CURRENT[:12]]
    proc = tmp_path / "proc"
    (proc / "7").mkdir(parents=True)
    (proc / "7" / "cmdline").write_bytes(b"python\0/app/btc_conservative_agent.py\0")
    assert vmq.main(["offline-proof", *args], env=ENV) == 0
    token = json.loads(capsys.readouterr().out)["plan"]["confirm_token"]
    assert vmq.main(["offline-execute", *args, "--confirm", token], env=ENV, proc_root=str(proc)) == 9
    assert "bot process is running" in json.loads(capsys.readouterr().out)["violations"]
    (proc / "7" / "cmdline").write_bytes(b"sleep\0infinity\0")
    assert vmq.main(["offline-execute", *args, "--confirm", "QUARANTINE-V3-MARKERS:bad"],
                    env=ENV, proc_root=str(proc)) == 9
    capsys.readouterr()
    assert vmq.main(["offline-execute", *args, "--confirm", token], env=ENV, proc_root=str(proc)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "COMPLETE" and out["preflight_after"]["ok"] is True

    exposed = tmp_path / "exposed"
    exposed.mkdir()
    open_paper = _fixture(exposed, positions=[{"id": "p"}])
    assert vmq.main(["offline-proof", "--data-dir", str(open_paper), "--expected-rev", CURRENT[:12]], env=ENV) == 9
    assert "paper lifecycle not flat" in json.loads(capsys.readouterr().out)["violations"]
