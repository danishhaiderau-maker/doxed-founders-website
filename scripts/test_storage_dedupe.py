"""scripts/storage_dedupe.py: verified reclaim never deletes an only copy and counts hardlinks as free."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_dedupe as sd  # noqa: E402


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_plan_and_apply_keep_every_unique_byte(tmp_path):
    keep = tmp_path / "keep"
    junk = tmp_path / "junk"
    _write(keep / "a.jsonl", b"row\n" * 50)
    _write(keep / "stream.jsonl", b"x\n" * 100)
    _write(junk / "snap" / "a.jsonl", b"row\n" * 50)              # identical copy
    _write(junk / "snap" / "stream.jsonl", b"x\n" * 40)           # older prefix of a keep stream
    _write(junk / "snap" / "only.jsonl", b"unique bytes\n")       # only copy anywhere
    _write(junk / "snap2" / "only.jsonl", b"unique bytes\n")      # duplicate of the only copy
    _write(junk / "__pycache__" / "m.pyc", b"\0" * 10)
    os.link(keep / "a.jsonl", junk / "linked.jsonl")              # same inode as a keep file
    db = str(tmp_path / "inv.sqlite3")
    sd.inventory(db, [keep, junk], min_hash_size=1, workers=2, hash_all_under=[junk])
    result = sd.plan_reclaim(db, [junk], [keep])
    summary = result["summary"]
    assert summary["COVERED_IDENTICAL"]["files"] == 2
    assert summary["COVERED_IDENTICAL"]["physical_gb"] < summary["COVERED_IDENTICAL"]["gb"] or \
        summary["COVERED_IDENTICAL"]["gb"] == 0
    linked = next(r for r in result["plan"]["COVERED_IDENTICAL"] if r["path"].endswith("linked.jsonl"))
    assert linked["physical_bytes"] == 0
    assert summary["COVERED_PREFIX"]["files"] == 1
    assert summary["UNIQUE_RETAIN"]["files"] == 1 and summary["DUPLICATE_WITHIN_JUNK"]["files"] == 1
    assert summary["REGENERABLE"]["files"] == 1

    retained = tmp_path / "retained"
    ledger = tmp_path / "ledger.jsonl"
    counts = sd.apply_reclaim(result, retained, ledger)
    assert counts["skipped"] == 0 and counts["retained"] == 1
    kept = list(retained.rglob("only.jsonl"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"unique bytes\n"
    assert (keep / "a.jsonl").read_bytes() == b"row\n" * 50
    assert not any(p.is_file() for p in junk.rglob("*"))
    actions = [json.loads(line)["action"] for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert actions.count("RETAIN") == 1 and "SKIP_UNVERIFIED" not in actions


def test_changed_copy_is_never_deleted(tmp_path):
    keep = tmp_path / "keep"
    junk = tmp_path / "junk"
    _write(keep / "a.jsonl", b"row\n" * 50)
    _write(junk / "a.jsonl", b"row\n" * 50)
    db = str(tmp_path / "inv.sqlite3")
    sd.inventory(db, [keep, junk], min_hash_size=1, workers=1, hash_all_under=[junk])
    result = sd.plan_reclaim(db, [junk], [keep])
    (keep / "a.jsonl").write_bytes(b"ROW\n" * 50)  # keep copy changed after planning
    counts = sd.apply_reclaim(result, tmp_path / "retained", tmp_path / "ledger.jsonl")
    assert counts["deleted"] == 0 and counts["skipped"] == 1
    assert (junk / "a.jsonl").is_file()
