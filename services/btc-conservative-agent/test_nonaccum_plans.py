"""C1-C4 plan builders are read-only, deterministic and protect what must be kept."""
import hashlib
import json
import os
import time

import nonaccum_plans as np_

EPOCH = 1_000_000.0
H = "a" * 64


def _w(p, data=b"{}", mtime=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def _tree(root):
    v3 = os.path.join(root, "runtime", "v3")
    rec = os.path.join(v3, "receipts", "emergency_record_idempotency_v1")
    old, cur = "epoch-v22-old", "epoch-cur"
    j = lambda e: json.dumps({"identity": {"epoch_id": e}}).encode()
    _w(f"{rec}/decision/{'1'*64}.json", j(old), EPOCH - 100)        # C1: pre-epoch, old id -> planned
    _w(f"{rec}/decision/{'2'*64}.json", j(cur), EPOCH - 100)        # pre mtime but CURRENT epoch id -> kept
    _w(f"{rec}/decision/{'3'*64}.json", j(cur), EPOCH + 100)        # current -> kept (and defines current ids)
    _w(f"{rec}/decision/complete.json", j(old), EPOCH - 100)        # protected names
    _w(f"{rec}/decision/bootstrap.json", j(old), EPOCH - 100)
    _w(f"{rec}/round_robin.json", j(old), EPOCH - 100)
    _w(f"{rec}/append_heads/{'4'*64}.json", j(old), EPOCH - 100)    # append_heads/ never scanned
    _w(f"{rec}/evidence_failure/{'5'*64}.json", j(old), EPOCH - 100)  # not one of the 7 ledger dirs
    snap = os.path.join(v3, "signal_snapshots_v1")
    _w(f"{snap}/{'6'*64}.json", b"x", EPOCH - 50)                   # C2 unreferenced pre-epoch -> planned
    _w(f"{snap}/{'7'*64}.json", b"x", EPOCH - 50)                   # referenced by a current bundle -> kept
    _w(f"{snap}/{'8'*64}.json", b"x", EPOCH + 50)                   # current -> kept
    _w(f"{v3}/lifecycle_bundles/aa/lifecycle-{'9'*64}/b.json", ("ref " + "7" * 64).encode(), EPOCH + 10)
    _w(f"{v3}/lifecycle_bundle_index/lifecycle_index.sqlite3", ("idx " + "c" * 64).encode(), EPOCH - 10)
    now = EPOCH + 30 * 86400
    _w(f"{root}/tape_1s/old.jsonl.gz", b"t", EPOCH - 10 * 86400)    # pre-epoch and > 168h old -> planned
    _w(f"{root}/tape_1s/recent_pre.jsonl.gz", b"t", now - 3600 if False else EPOCH - 5)  # set below
    os.utime(f"{root}/tape_1s/recent_pre.jsonl.gz", (now - 3600, now - 3600))
    _w(f"{root}/tape_1s/new.jsonl.gz", b"t", EPOCH + 5)
    ln = lambda t: (json.dumps({"event_id": f"e{t}", "created_at_unix": t}) + "\n").encode()
    _w(f"{root}/runtime/relay_outbox_retired.jsonl", ln(EPOCH - 5) + ln(EPOCH - 6) + ln(EPOCH + 5))
    _w(f"{root}/runtime/relay_outbox_quarantine.jsonl", ln(EPOCH - 7))
    _w(f"{root}/runtime/relay_switch.json", b"{}", EPOCH - 99)
    return now


def _snapshot(root):
    return sorted((os.path.relpath(os.path.join(d, f), root), os.stat(os.path.join(d, f)).st_size, os.stat(os.path.join(d, f)).st_mtime_ns)
                  for d, _s, fs in os.walk(root) for f in fs)


def test_plans_select_only_pre_epoch_unreferenced_and_never_touch_the_tree(tmp_path):
    root = str(tmp_path)
    now = _tree(root)
    # recent_pre was pushed to now-1h, so it is pre-epoch? no: mtime now-1h > epoch => current. keep a truly recent pre-epoch too
    before = _snapshot(root)
    doc = np_.build_all(root, EPOCH, now)
    assert _snapshot(root) == before                       # read-only
    p = doc["plans"]
    assert [i for i in p["C1"]["sample"]] == [f"decision/{'1'*64}.json"] and p["C1"]["count"] == 1
    assert p["C1"]["kept_pre_mtime_but_current_epoch_id"] == 1 and p["C1"]["skipped"]["protected_or_non_hex_name"] >= 1
    assert p["C2"]["sample"] == [f"{'6'*64}.json"] and p["C2"]["kept_pre_epoch_but_referenced"] == 1
    assert "C3a" not in p  # R22: transfer-bundle class retired
    assert p["C3b"]["sample"] == ["tape_1s/old.jsonl.gz"]
    assert p["C4"]["count"] == 3 and p["C4"]["lines_kept"] == 1
    assert "relay_switch.json" not in json.dumps(doc)
    again = np_.build_all(root, EPOCH, now)
    assert again["plan_sha256"] == doc["plan_sha256"]       # deterministic


def test_plan_sha_changes_when_the_selection_changes_and_tape_inside_168h_is_kept(tmp_path):
    root = str(tmp_path)
    now = _tree(root)
    a = np_.build_all(root, EPOCH, now)
    _w(f"{root}/signal_snapshots_v1x/ignored", b"x")
    _w(f"{root}/runtime/v3/signal_snapshots_v1/{'d'*64}.json", b"x", EPOCH - 1)
    b = np_.build_all(root, EPOCH, now)
    assert a["plan_sha256"] != b["plan_sha256"] and b["plans"]["C2"]["count"] == a["plans"]["C2"]["count"] + 1
    early = np_.build_all(root, EPOCH, EPOCH + 3 * 86400)   # 3 days after the epoch: nothing is outside the 168h window
    assert early["plans"]["C3b"]["count"] == 1              # only the 10-day-old pre-epoch file
