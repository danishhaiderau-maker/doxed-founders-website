"""Incremental V3 ledger index: always identical to a full parse."""
import json

import pytest

from research import v3_ledger_index as lri


def _reference(path, byte_limit=None):
    """The streaming reader's semantics, re-serialized canonically."""
    data = path.read_bytes()
    if byte_limit is not None:
        data = data[:byte_limit]
    out = []
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break
        try:
            value = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return out


def _canonical(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _rows(start, count, pad=40):
    return b"".join(_canonical({"record_id": f"r{i}", "entry_children": ["x" * pad] * (i % 5)})
                    for i in range(start, start + count))


@pytest.fixture(autouse=True)
def small_blocks(monkeypatch):
    monkeypatch.setattr(lri, "BLOCK_BYTES", 256)


def _run(path, cache, **kwargs):
    stats = {}
    rows = list(lri.iter_canonical_rows(path, cache_dir=cache, stats=stats, **kwargs))
    return rows, stats


def test_append_only_growth_reuses_verified_rows_and_parses_only_new_ones(tmp_path):
    ledger, cache = tmp_path / "order_intent.jsonl", tmp_path / "idx"
    ledger.write_bytes(_rows(0, 40))
    first, stats = _run(ledger, cache)
    assert first == _reference(ledger) and stats["parsed_rows"] == 40 and stats["reused_rows"] == 0
    with ledger.open("ab") as handle:
        handle.write(_rows(40, 7))
    second, stats = _run(ledger, cache)
    assert second == _reference(ledger)
    assert stats["reused_rows"] == 40 and stats["parsed_rows"] == 7 and stats["index_valid"] is True
    third, stats = _run(ledger, cache)
    assert third == second and stats["parsed_rows"] == 0


def test_rewritten_history_falls_back_to_parsing_from_the_first_changed_block(tmp_path):
    ledger, cache = tmp_path / "order_intent.jsonl", tmp_path / "idx"
    ledger.write_bytes(_rows(0, 40))
    _run(ledger, cache)
    data = bytearray(ledger.read_bytes())
    middle = data.index(b'"r20"')
    data[middle:middle + 5] = b'"R20"'
    ledger.write_bytes(bytes(data))
    rows, stats = _run(ledger, cache)
    assert rows == _reference(ledger) and stats["index_valid"] is False
    assert 0 < stats["reused_rows"] < 20 and stats["parsed_rows"] > 0


def test_truncated_file_and_byte_limits_match_the_streaming_reader(tmp_path):
    ledger, cache = tmp_path / "order_intent.jsonl", tmp_path / "idx"
    ledger.write_bytes(_rows(0, 30))
    _run(ledger, cache)
    full = ledger.read_bytes()
    for limit in (0, 1, 100, 257, len(full) // 2, len(full) - 1, len(full), len(full) + 50):
        rows, _stats = _run(ledger, cache, byte_limit=limit)
        assert rows == _reference(ledger, limit), limit
    ledger.write_bytes(full[: len(full) // 3])
    rows, _stats = _run(ledger, cache)
    assert rows == _reference(ledger)


def test_non_canonical_invalid_non_object_crlf_and_partial_rows(tmp_path):
    ledger, cache = tmp_path / "order_intent.jsonl", tmp_path / "idx"
    body = (_canonical({"a": 1}) + b'{"b": 2, "a": 1}\n' + b"not json\n" + b"[1,2]\n" + b"\n"
            + b'{"c":"\xff\xfe"}\n' + b'{"d":3}\r\n' + _canonical({"e": "\u00e9"}) + b'{"tail":')
    ledger.write_bytes(body)
    first, stats = _run(ledger, cache)
    assert first == _reference(ledger) and len(first) == 5
    with ledger.open("ab") as handle:
        handle.write(b'1}\n' + _rows(0, 3))
    second, stats = _run(ledger, cache)
    assert second == _reference(ledger) and stats["reused_rows"] == 8


def test_a_corrupt_or_foreign_index_is_ignored(tmp_path):
    ledger, cache = tmp_path / "order_intent.jsonl", tmp_path / "idx"
    ledger.write_bytes(_rows(0, 20))
    _run(ledger, cache)
    index_file = next(cache.iterdir())
    raw = bytearray(index_file.read_bytes())
    raw[-1] ^= 0xFF
    index_file.write_bytes(bytes(raw))
    rows, stats = _run(ledger, cache)
    assert rows == _reference(ledger) and stats["index_valid"] is False and stats["reused_rows"] == 0
    other = tmp_path / "other.jsonl"
    other.write_bytes(ledger.read_bytes())
    (cache / lri._index_file(other, cache).name).write_bytes(index_file.read_bytes())
    rows, stats = _run(other, cache)
    assert rows == _reference(other) and stats["reused_rows"] == 0


def test_without_a_cache_directory_results_are_identical(tmp_path):
    ledger = tmp_path / "v3" / "ledgers" / "order_intent.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_rows(0, 12))
    assert list(lri.iter_canonical_rows(ledger)) == _reference(ledger)
    assert not (tmp_path / "analyzer").exists()


def _old_snapshot_digest(root):
    import hashlib
    from research.v3_policy_report_adapter import _iter_jsonl
    digest, counts = hashlib.sha256(), {}
    for name in ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment"):
        path = root / "v3" / "ledgers" / f"{name}.jsonl"
        counts[name] = 0
        for row in _iter_jsonl(path, byte_limit=path.stat().st_size if path.exists() else 0):
            counts[name] += 1
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    return "policy-v3-snapshot-" + digest.hexdigest()[:24], counts


def test_cycle_snapshot_identity_is_unchanged_with_a_warm_index(tmp_path):
    from research.v3_policy_report_adapter import load_v3_cycle_snapshot
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    (tmp_path / "analyzer").mkdir()
    (ledgers / "opportunity.jsonl").write_bytes(
        _canonical({"episode_id": "e1", "epoch_id": "ep", "signal_ts": 2.0}) + b'{"signal_ts": 1.0, "epoch_id": "ep"}\n')
    (ledgers / "decision.jsonl").write_bytes(_canonical({"epoch_id": "ep", "policy_signature": "sig"}))
    (ledgers / "order_intent.jsonl").write_bytes(_rows(0, 25) + b'{"z": 1, "a": [1.50, 2]}\n' + b"oops\n")
    (ledgers / "execution.jsonl").write_bytes(b"")
    (ledgers / "lifecycle.jsonl").write_bytes(_rows(100, 9) + b'{"partial":')
    for append in (b"", _rows(200, 6), b'{"b":2,"a":1}\n'):
        with (ledgers / "order_intent.jsonl").open("ab") as handle:
            handle.write(append)
        for _ in range(2):
            snapshot = load_v3_cycle_snapshot(tmp_path)
            assert (snapshot["snapshot_id"], snapshot["ledger_counts"]) == _old_snapshot_digest(tmp_path)
            assert snapshot["epoch_id"] == "ep" and snapshot["policy_signature"] == "sig"
    assert any((tmp_path / "analyzer" / "ledger_index").iterdir())
