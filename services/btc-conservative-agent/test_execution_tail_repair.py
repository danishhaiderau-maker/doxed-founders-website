import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import execution_tail_repair as repair
import execution_tail_repair_verify as verify


def fixture(tmp_path, monkeypatch):
    prefix=b'{"record_id":"one"}\n{"record_id":"two"}\n'; tail=b'{"record_id":"bad"'
    source=prefix+tail; root=tmp_path
    ledger=root/"v3/ledgers/execution.jsonl"; ledger.parent.mkdir(parents=True); ledger.write_bytes(source)
    db=root/"v3/lifecycle_bundle_index/lifecycle_index.sqlite3"; db.parent.mkdir(parents=True)
    stat=ledger.stat(); anchor=hashlib.sha256(prefix[-4096:]).hexdigest()
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE ledger_cursor(ledger TEXT PRIMARY KEY,source_dev INTEGER,source_ino INTEGER,byte_offset INTEGER,source_anchor_sha256 TEXT,source_mtime_ns INTEGER)")
        c.execute("INSERT INTO ledger_cursor VALUES(?,?,?,?,?,?)",("execution",stat.st_dev,stat.st_ino,len(prefix),anchor,stat.st_mtime_ns))
    values={"SOURCE_SIZE":len(source),"SOURCE_SHA256":hashlib.sha256(source).hexdigest(),"PREFIX_SIZE":len(prefix),
        "PREFIX_SHA256":hashlib.sha256(prefix).hexdigest(),"TAIL_SIZE":len(tail),"TAIL_SHA256":hashlib.sha256(tail).hexdigest(),
        "OLD_DEV":stat.st_dev,"OLD_INO":stat.st_ino,"OLD_MTIME_NS":stat.st_mtime_ns,"CURSOR_ANCHOR_SHA256":anchor}
    for module in (repair,verify):
        for key,value in values.items():
            if hasattr(module,key): monkeypatch.setattr(module,key,value)
    monkeypatch.setattr(repair,"REPAIR_ID",f"execution-tail-{values['SOURCE_SHA256'][:16]}")
    monkeypatch.setattr(verify,"REPAIR_ID",repair.REPAIR_ID); monkeypatch.setattr(verify,"ANCHOR_SHA256",anchor)
    return root,ledger,db,prefix,tail,source,values


def call(root, values):
    return repair.repair_execution_tail(root,expected_source_size=values["SOURCE_SIZE"],expected_source_sha256=values["SOURCE_SHA256"],
        expected_prefix_size=values["PREFIX_SIZE"],expected_prefix_sha256=values["PREFIX_SHA256"],expected_tail_size=values["TAIL_SIZE"],
        expected_tail_sha256=values["TAIL_SHA256"],expected_inode=values["OLD_INO"],expected_mtime_ns=values["OLD_MTIME_NS"])


def test_repairs_tail_and_cursor_and_preserves_unknown(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    receipt=call(root,values); assert ledger.read_bytes()==prefix
    q=ledger.parent/"corrupt_evidence_quarantine"/receipt["repair_id"]
    assert (q/"execution.jsonl.original").read_bytes()==source
    assert (q/"execution.jsonl.incomplete-tail").read_bytes()==tail
    with sqlite3.connect(db) as c: row=c.execute("SELECT source_ino,byte_offset FROM ledger_cursor WHERE ledger='execution'").fetchone()
    assert row==(ledger.stat().st_ino,len(prefix)); assert receipt["ranking_eligible"] is False


def test_idempotent_replay_and_independent_verify(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    first=call(root,values); second=call(root,values); assert first==second
    monkeypatch.setenv("SOURCE_GIT_REV","abcdef123456789")
    result=verify.verify(root,expected_revision="abcdef123456")
    assert result["ok"] and result["cursor_identity_matches"]


@pytest.mark.parametrize("field",["source_size","source_sha256","prefix_size","prefix_sha256","tail_size","tail_sha256","inode","mtime_ns"])
def test_wrong_expectation_refuses_without_mutation(tmp_path,monkeypatch,field):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch); before=ledger.read_bytes()
    kwargs={"expected_source_size":values["SOURCE_SIZE"],"expected_source_sha256":values["SOURCE_SHA256"],
        "expected_prefix_size":values["PREFIX_SIZE"],"expected_prefix_sha256":values["PREFIX_SHA256"],
        "expected_tail_size":values["TAIL_SIZE"],"expected_tail_sha256":values["TAIL_SHA256"],
        "expected_inode":values["OLD_INO"],"expected_mtime_ns":values["OLD_MTIME_NS"]}
    key="expected_"+field; kwargs[key]=(kwargs[key]+1 if isinstance(kwargs[key],int) else "f"*64)
    with pytest.raises(ValueError,match="EXPECTATION_MISMATCH"): repair.repair_execution_tail(root,**kwargs)
    assert ledger.read_bytes()==before


def test_cursor_boundary_mismatch_refuses_before_replace(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    with sqlite3.connect(db) as c: c.execute("UPDATE ledger_cursor SET byte_offset=byte_offset-1 WHERE ledger='execution'")
    with pytest.raises(ValueError,match="CURSOR_BOUNDARY_MISMATCH"): call(root,values)
    assert ledger.read_bytes()==source


def test_bad_prefix_refuses_before_replace(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    bad=b'{bad}\n'; changed=bad+tail; ledger.write_bytes(changed)
    monkeypatch.setattr(repair,"SOURCE_SHA256",hashlib.sha256(changed).hexdigest()); monkeypatch.setattr(repair,"SOURCE_SIZE",len(changed))
    monkeypatch.setattr(repair,"PREFIX_SHA256",hashlib.sha256(bad).hexdigest()); monkeypatch.setattr(repair,"PREFIX_SIZE",len(bad))
    monkeypatch.setattr(repair,"OLD_MTIME_NS",ledger.stat().st_mtime_ns)
    monkeypatch.setattr(repair,"REPAIR_ID",f"execution-tail-{repair.SOURCE_SHA256[:16]}")
    with pytest.raises(ValueError,match="PREFIX_INVALID_JSON"): repair.repair_execution_tail(root,expected_source_size=len(changed),
        expected_source_sha256=repair.SOURCE_SHA256,expected_prefix_size=len(bad),expected_prefix_sha256=repair.PREFIX_SHA256,
        expected_tail_size=len(tail),expected_tail_sha256=hashlib.sha256(tail).hexdigest(),expected_inode=values["OLD_INO"],expected_mtime_ns=repair.OLD_MTIME_NS)


def test_crash_after_replace_replays_with_valid_appended_row(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    original=repair._atomic_bytes; crashed={"done":False}
    def crash_after_replace(path,raw):
        original(path,raw)
        if path==ledger and raw==prefix and not crashed["done"]:
            crashed["done"]=True
            with path.open("ab") as handle: handle.write(b'{"record_id":"later"}\n')
            raise RuntimeError("crash-after-replace")
    monkeypatch.setattr(repair,"_atomic_bytes",crash_after_replace)
    with pytest.raises(RuntimeError,match="crash-after-replace"): call(root,values)
    monkeypatch.setattr(repair,"_atomic_bytes",original)
    receipt=call(root,values)
    assert ledger.read_bytes()==prefix+b'{"record_id":"later"}\n'
    assert receipt["status"]=="REPAIRED"


def test_crash_after_database_commit_replays_receipt(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    original=repair._write_once_json
    def crash(path,payload,error):
        if path.name=="repair_receipt.json": raise RuntimeError("crash-after-db")
        return original(path,payload,error)
    monkeypatch.setattr(repair,"_write_once_json",crash)
    with pytest.raises(RuntimeError,match="crash-after-db"): call(root,values)
    monkeypatch.setattr(repair,"_write_once_json",original)
    assert call(root,values)["status"]=="REPAIRED"


def test_quarantine_metadata_tamper_refuses_replay(tmp_path,monkeypatch):
    root,ledger,db,prefix,tail,source,values=fixture(tmp_path,monkeypatch)
    receipt=call(root,values); q=ledger.parent/"corrupt_evidence_quarantine"/receipt["repair_id"]
    value=json.loads((q/"excluded_unknown.json").read_text()); value["ranking_eligible"]=True
    (q/"excluded_unknown.json").write_text(json.dumps(value)+"\n")
    with pytest.raises(ValueError,match="QUARANTINE_METADATA_TAMPERED"): call(root,values)
