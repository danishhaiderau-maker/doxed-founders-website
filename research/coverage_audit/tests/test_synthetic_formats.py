"""Synthetic container and record fixtures. Helpers live here so the package stays 18 files."""

from __future__ import annotations

import io
import json
import struct
import unittest
import zipfile
import zlib
from pathlib import Path

from research.coverage_audit.auditor import run_audit
from research.coverage_audit.guards import (
    REPORT_SCHEMA,
    TINY_SYNTHETIC_PROFILE,
    AuditStop,
    FixedMeter,
    canonical_bytes,
    forbid,
    schema_errors,
    sha256_hex,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic_cases.json"
BASE = "40087a65f2157f8c6701ea3670adc5c29a28c87d"
CLOCK = 1700000000000
T0 = 1700000000
CANARIES = ("SECRET_CANARY", "SECRET_CANARY_EXC", "PX_CANARY_12.5", "SZ_CANARY_7")


def load_cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def registry_bytes():
    return (ROOT / "source_registry.json").read_bytes()


def ref_for(name: str) -> str:
    if name == "unspecified":
        return "unverified/placeholder/unspecified/unspecified/era1"
    return f"synthetic/fixture/{name}/instr/era1"


class MemorySource:
    def __init__(self, data: bytes, mode: str = "normal", replacement: bytes = b"", allowlist=None):
        self.data = data
        self.mode = mode
        self.replacement = replacement
        self.allowlist = allowlist
        self.reads = 0
        self.mutation_calls = 0
        self._token = "0"
        self._chunks = 0

    def size(self):
        return len(self.data)

    def identity(self):
        return (len(self.data), self._token)

    def read_chunks(self, size=4096):
        self.reads += 1
        if self.mode == "mid":
            self._chunks += 1
            yield self.data[:1]
            self._token = "SECRET_CANARY"
            yield self.data[1:]
            return
        if self.mode == "mutate_allowlist" and self.allowlist is not None:
            self.allowlist["SECRET_CANARY"] = "PX_CANARY_12.5"
        view = self.replacement if self.mode == "replace" else self.data
        for index in range(0, len(view), size):
            yield view[index : index + size]
        if not view:
            return

    def readback(self):
        self.reads += 1
        if self.mode == "readback":
            self._token = "changed"
            self.data = self.replacement
            return self.replacement
        if self.mode == "replace":
            return self.replacement
        return self.data

    def chmod(self, path):
        self.mutation_calls += 1
        forbid("chmod", path)


def _profile(**overrides):
    profile = dict(TINY_SYNTHETIC_PROFILE)
    if overrides:
        profile["profile_id"] = "boundary_test"
        profile.update(overrides)
    return profile


def _csv(header, rows):
    lines = []
    if header:
        lines.append(header)
    for row in rows:
        lines.append(",".join(_field(cell) for cell in row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _field(text: str) -> str:
    if any(char in text for char in ",\"\n\r"):
        return '"' + text.replace('"', '""') + '"'
    return text


def _periodic_rows(offsets=(0, 60, 120), labels=None):
    rows = []
    for index, offset in enumerate(offsets):
        label = CANARIES[index % len(CANARIES)] if labels is None else labels[index]
        rows.append((str(T0 + offset), label))
    return rows


def _gzip(payload: bytes, fname: bytes | None = None) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = compressor.compress(payload) + compressor.flush()
    flag = 8 if fname is not None else 0
    header = bytes([0x1F, 0x8B, 8, flag, 0, 0, 0, 0, 0, 255])
    name = b"" if fname is None else fname + b"\x00"
    trailer = struct.pack("<II", zlib.crc32(payload) & 0xFFFFFFFF, len(payload) & 0xFFFFFFFF)
    return header + name + body + trailer


def _zip(pairs, compress=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress) as archive:
        for name, payload in pairs:
            if name.endswith("/"):
                info = zipfile.ZipInfo(name)
                info.external_attr = 0o40755 << 16
                archive.writestr(info, b"")
            else:
                archive.writestr(name, payload)
    return buffer.getvalue()


def _member(member_id, index, payload, kind="file", name=None):
    item = {
        "member_id": member_id,
        "index": index,
        "sha256": sha256_hex(payload),
        "kind": kind,
    }
    if name is not None:
        item["name"] = name
    return item


def _input(logical, feed, payload, members=None):
    return {
        "logical_id": logical,
        "feed_ref": ref_for(feed),
        "payload": payload,
        "members": [] if members is None else members,
        "mode": "normal",
        "replacement": b"",
    }


def _plain(feed, rows, logical="case_main", header="event_ts,note"):
    return _input(logical, feed, _csv(header, rows))


def _run_built(built, meter=None, probe=None):
    registry = built.get("registry_bytes", registry_bytes())
    profile = built.get("profile")
    allow_inputs = []
    sources = {}
    allowlist = {
        "allowlist_id": "synthetic_fixture",
        "registry_sha256": sha256_hex(registry),
        "profile_id": (profile or TINY_SYNTHETIC_PROFILE)["profile_id"],
        "inputs": allow_inputs,
    }
    for item in built["inputs"]:
        allow_inputs.append(
            {
                "logical_id": item["logical_id"],
                "sha256": sha256_hex(item["payload"] if item["mode"] != "replace" else item.get("pinned", item["payload"])),
                "size": len(item["payload"] if item["mode"] != "replace" else item.get("pinned", item["payload"])),
                "feed_ref": item["feed_ref"],
                "members": item["members"],
            }
        )
        pinned = item.get("pinned", item["payload"])
        data = item["payload"]
        mode = item["mode"]
        if mode == "replace":
            data = pinned
        sources[item["logical_id"]] = MemorySource(
            data if mode != "replace" else pinned,
            mode=mode,
            replacement=item.get("replacement", b"SECRET_CANARY"),
            allowlist=allowlist,
        )
        if mode == "replace":
            sources[item["logical_id"]].data = pinned
    if built.get("extra_source"):
        sources["extra_ignored"] = MemorySource(b"SECRET_CANARY")
    digest = sha256_hex(canonical_bytes(allowlist))
    if built.get("tamper"):
        digest = "0" * 64
    report = run_audit(
        registry_bytes=registry,
        allowlist=allowlist,
        allowlist_sha256=digest,
        sources=sources,
        base_commit=BASE,
        clock_unix_ms=CLOCK,
        seed=built.get("seed", 0),
        profile=profile,
        meter=meter if meter is not None else built.get("meter", FixedMeter(0, 0)),
        probe=probe if probe is not None else built.get("probe"),
    )
    reads = sum(source.reads for source in sources.values())
    mutations = sum(source.mutation_calls for source in sources.values())
    return report, {"reads": reads, "mutation_calls": mutations, "sources": sources}


def assert_case(test, case):
    built = BUILDERS[case["id"]]()
    report, extra = _run_built(built)
    blob = json.dumps(report)
    test.assertEqual(schema_errors(report, REPORT_SCHEMA), [], case["id"])
    test.assertEqual(report["status"], case["expect_status"], (case["id"], report["reasons"], report["gaps"][:1]))
    for reason in case.get("expect_reasons", []):
        test.assertIn(reason, report["reasons"], case["id"])
    for reason in case.get("forbid_reasons", []):
        test.assertNotIn(reason, report["reasons"], case["id"])
    if case["expect_status"] != "COMPLETE":
        test.assertNotEqual(report["status"], "COMPLETE", case["id"])
    test.assertFalse(report["scope"]["real_interval_claim"])
    test.assertIsNone(report["scope"]["accepted_real_dates"])
    test.assertTrue(report["scope"]["synthetic_only"])
    for canary in CANARIES:
        test.assertNotIn(canary, blob, case["id"])
    if case.get("must_not_read"):
        test.assertEqual(extra["reads"], 0, case["id"])
    if case["id"] == "extra_source_ignored":
        test.assertEqual(extra["sources"]["extra_ignored"].reads, 0)
    test.assertEqual(extra["mutation_calls"], 0, case["id"])
    if "expect_gap_count" in case:
        test.assertEqual(len(report["gaps"]), case["expect_gap_count"], case["id"])
    if case.get("expect_gap_rule"):
        test.assertTrue(report["gaps"], case["id"])
        test.assertEqual(report["gaps"][0]["rule"], case["expect_gap_rule"])
    for gap in report["gaps"]:
        test.assertIn("T", gap["utc_start"])
        test.assertIn("T", gap["utc_end"])
        test.assertTrue(gap["rule"])
    _special(test, case["id"], report)
    return report, extra


def _special(test, case_id, report):
    if not report["feeds"]:
        return
    feed = report["feeds"][0]
    if case_id == "depth_bands":
        fingerprint = feed["schema_fingerprint"]
        test.assertFalse(fingerprint["bands_equal_walkable_book"])
        test.assertNotEqual(fingerprint["depth_bands"][0]["count_max"], fingerprint["executable_levels"][0]["count_max"])
        test.assertGreater(fingerprint["undeclared_fields_max"], 0)
    if case_id == "timed_meta":
        test.assertIsNotNone(feed["receive"]["min"])
        test.assertIsNotNone(feed["publication"]["min"])
        test.assertIsNone(feed["vintage"]["min"])
    if case_id == "seq_nocontract":
        test.assertEqual(feed["sequence"]["reason"], "NO_SEQUENCE_CONTRACT")
    if case_id == "seq_interleaved":
        test.assertEqual(feed["sequence"]["reason"], "INTERLEAVED_IDS_NOT_PER_INSTRUMENT")
    if case_id == "seq_contract_ok":
        test.assertEqual(feed["sequence"]["status"], "PROVEN")
    if case_id == "seq_contract_hole":
        test.assertEqual(feed["sequence"]["reason"], "SEQUENCE_GAP")
        test.assertEqual(report["gaps"][0]["evidence"]["expected_sequence"], 3)
        test.assertEqual(report["gaps"][0]["evidence"]["observed_sequence"], 4)
    if case_id == "gap_above":
        test.assertLessEqual(len(report["gaps"]), 2)


def _with_profile(built, **overrides):
    built["profile"] = _profile(**overrides)
    return built


def _rows_csv(offsets, feed="heartbeat", logical="case_main", labels=None, header="event_ts,note"):
    return {"inputs": [_plain(feed, _periodic_rows(offsets, labels), logical, header)]}


def _seq_csv(seqs, offsets=(0, 60, 120), feed="seq_contract"):
    rows = []
    for index, (offset, seq) in enumerate(zip(offsets, seqs)):
        rows.append((str(T0 + offset), str(seq), CANARIES[index % 3]))
    return {"inputs": [_plain(feed, rows, header="event_ts,seq,note")]}


BUILDERS = {}


def _register(name):
    def wrap(fn):
        BUILDERS[name] = fn
        return fn
    return wrap


@_register("gzip_single")
def _gzip_single():
    payload = _csv("event_ts,note", _periodic_rows())
    return {"inputs": [_input("case_main", "heartbeat_gz", _gzip(payload), [_member("g0", 0, payload, "gzip_member")])]}


@_register("periodic_complete")
def _periodic_complete():
    return _rows_csv((0, 60, 120))


@_register("gzip_multi")
def _gzip_multi():
    first = _csv("event_ts,note", _periodic_rows((0, 60)))
    second = _csv("event_ts,note", _periodic_rows((120,), labels=("SZ_CANARY_7",)))
    blob = _gzip(first) + _gzip(second)
    return {"inputs": [_input("case_main", "heartbeat_gz", blob, [
        _member("g0", 0, first, "gzip_member"),
        _member("g1", 1, second, "gzip_member"),
    ])]}


@_register("gzip_tail")
def _gzip_tail():
    payload = _csv("event_ts,note", _periodic_rows())
    return {"inputs": [_input("case_main", "heartbeat_gz", _gzip(payload) + b"\x01", [_member("g0", 0, payload, "gzip_member")])]}


@_register("gzip_trunc")
def _gzip_trunc():
    payload = _csv("event_ts,note", _periodic_rows())
    wrapped = _gzip(payload)
    return {"inputs": [_input("case_main", "heartbeat_gz", wrapped[: len(wrapped) // 2], [_member("g0", 0, payload, "gzip_member")])]}


@_register("gzip_flip")
def _gzip_flip():
    payload = _csv("event_ts,note", _periodic_rows())
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = bytearray(compressor.compress(payload) + compressor.flush())
    body[len(body) // 2] ^= 0xFF
    header = bytes([0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 255])
    trailer = struct.pack("<II", zlib.crc32(payload) & 0xFFFFFFFF, len(payload) & 0xFFFFFFFF)
    return {"inputs": [_input("case_main", "heartbeat_gz", header + bytes(body) + trailer, [_member("g0", 0, payload, "gzip_member")])]}


@_register("gzip_fname")
def _gzip_fname():
    payload = _csv("event_ts,note", _periodic_rows())
    return {"inputs": [_input("case_main", "heartbeat_gz", _gzip(payload, b"SECRET_CANARY"), [_member("g0", 0, payload, "gzip_member")])]}


@_register("gzip_bomb")
def _gzip_bomb():
    payload = b"A" * 5000 + b"SECRET_CANARY"
    return _with_profile(
        {"inputs": [_input("case_main", "heartbeat_gz", _gzip(payload), [_member("g0", 0, payload, "gzip_member")])]},
        max_decompressed_bytes=1024,
    )


@_register("csv_quoted")
def _csv_quoted():
    rows = [
        ("1700000000", 'say "SECRET_CANARY", hi\nstill'),
        ("1700000060", "PX_CANARY_12.5"),
        ("1700000120", "SZ_CANARY_7"),
    ]
    return {"inputs": [_plain("heartbeat", rows)]}


@_register("headerless")
def _headerless():
    return {"inputs": [_plain("headerless", _periodic_rows(), header=None)]}


@_register("field_count")
def _field_count():
    payload = b"event_ts,note\n1700000000,alpha,SECRET_CANARY\n"
    return {"inputs": [_input("case_main", "heartbeat", payload)]}


@_register("encoding_error")
def _encoding_error():
    payload = b"event_ts,note\n1700000000,SECRET_CANARY\xff\n"
    return {"inputs": [_input("case_main", "heartbeat", payload)]}


@_register("timestamp_unit")
def _timestamp_unit():
    return {"inputs": [_plain("heartbeat_ms", _periodic_rows())]}


@_register("jsonl_ok")
def _jsonl_ok():
    rows = []
    for index, offset in enumerate((0, 60, 120)):
        rows.append({"event_ts": T0 + offset, "note": CANARIES[index], "px": "PX_CANARY_12.5"})
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows).encode("utf-8")
    return {"inputs": [_input("case_main", "heartbeat_jsonl", payload)]}


@_register("jsonl_malformed")
def _jsonl_malformed():
    good = json.dumps({"event_ts": T0, "note": "alpha"}, separators=(",", ":")).encode("utf-8")
    return {"inputs": [_input("case_main", "heartbeat_jsonl", good + b"\n{SECRET_CANARY\n")]}


@_register("json_array")
def _json_array():
    rows = [{"event_ts": T0 + offset, "note": CANARIES[index]} for index, offset in enumerate((0, 60, 120))]
    return {"inputs": [_input("case_main", "heartbeat_jsona", json.dumps(rows).encode("utf-8"))]}


@_register("header_mismatch")
def _header_mismatch():
    payload = b"event_ts,SECRET_CANARY\n1700000000,alpha\n"
    return {"inputs": [_input("case_main", "heartbeat", payload)]}


@_register("iso_ok")
def _iso_ok():
    rows = [
        ("2023-11-14T22:13:20Z", "SECRET_CANARY"),
        ("2023-11-14T22:14:20Z", "PX_CANARY_12.5"),
        ("2023-11-14T22:15:20Z", "SZ_CANARY_7"),
    ]
    return {"inputs": [_plain("heartbeat_iso", rows)]}


@_register("iso_offset")
def _iso_offset():
    payload = b"event_ts,note\n2023-11-14T22:13:20+00:00,SECRET_CANARY\n"
    return {"inputs": [_input("case_main", "heartbeat_iso", payload)]}


def _zip_pair():
    first = _csv("event_ts,note", _periodic_rows((0, 60)))
    second = _csv("event_ts,note", [("1700000120", "SZ_CANARY_7")])
    return first, second


@_register("zip_multi")
def _zip_multi():
    first, second = _zip_pair()
    blob = _zip([("sub/", b""), ("a.csv", first), ("b.csv", second)])
    members = [
        _member("dir_sub", 0, b"", "directory", "sub/"),
        _member("part_a", 1, first, "file", "a.csv"),
        _member("part_b", 2, second, "file", "b.csv"),
    ]
    return {"inputs": [_input("case_main", "heartbeat_zip", blob, members)]}


@_register("zip_reorder")
def _zip_reorder():
    first, second = _zip_pair()
    blob = _zip([("b.csv", second), ("a.csv", first)])
    members = [
        _member("part_a", 0, first, "file", "a.csv"),
        _member("part_b", 1, second, "file", "b.csv"),
    ]
    return {"inputs": [_input("case_main", "heartbeat_zip", blob, members)]}


@_register("zip_extra")
def _zip_extra():
    first, second = _zip_pair()
    extra = b"SECRET_CANARY\n"
    blob = _zip([("a.csv", first), ("b.csv", second), ("c.csv", extra)])
    members = [
        _member("part_a", 0, first, "file", "a.csv"),
        _member("part_b", 1, second, "file", "b.csv"),
    ]
    return {"inputs": [_input("case_main", "heartbeat_zip", blob, members)]}


@_register("zip_traversal")
def _zip_traversal():
    blob = _zip([("../SECRET_CANARY.csv", b"PX_CANARY_12.5")])
    members = [_member("part_a", 0, b"alpha", "file", "a.csv")]
    return {"inputs": [_input("case_main", "heartbeat_zip", blob, members)]}


@_register("zip_crc")
def _zip_crc():
    first = _csv("event_ts,note", [("1700000000", "alpha"), ("1700000060", "beta")])
    second = b"SECRET_CANARY_BODY"
    blob = bytearray(_zip([("a.csv", first), ("b.csv", second)], compress=zipfile.ZIP_STORED))
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        info = archive.getinfo("b.csv")
        offset = info.header_offset
    name_len = int.from_bytes(blob[offset + 26 : offset + 28], "little")
    extra_len = int.from_bytes(blob[offset + 28 : offset + 30], "little")
    blob[offset + 30 + name_len + extra_len] ^= 0xFF
    members = [
        _member("part_a", 0, first, "file", "a.csv"),
        _member("part_b", 1, second, "file", "b.csv"),
    ]
    return {"inputs": [_input("case_main", "heartbeat_zip", bytes(blob), members)]}


@_register("zip_trunc")
def _zip_trunc():
    payload = _csv("event_ts,note", _periodic_rows())
    blob = _zip([("a.csv", payload)])[:20]
    return {"inputs": [_input("case_main", "heartbeat_zip", blob, [_member("part_a", 0, payload, "file", "a.csv")])]}


@_register("zip_encrypted")
def _zip_encrypted():
    payload = _csv("event_ts,note", _periodic_rows())
    blob = bytearray(_zip([("a.csv", payload)], compress=zipfile.ZIP_STORED))
    for signature, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while True:
            found = blob.find(signature, start)
            if found < 0:
                break
            blob[found + flag_at] |= 1
            start = found + 4
    return {"inputs": [_input("case_main", "heartbeat_zip", bytes(blob), [_member("part_a", 0, payload, "file", "a.csv")])]}


@_register("sparse_not_outage")
def _sparse():
    rows = [{"event_ts": stamp, "note": "SECRET_CANARY"} for stamp in (T0, T0 + 1000, T0 + 5000)]
    payload = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    return {"inputs": [_input("case_main", "sparse_trades", payload)]}


@_register("periodic_hole")
def _periodic_hole():
    return _rows_csv((0, 60, 180))


@_register("tolerance_inside")
def _tolerance_inside():
    return _rows_csv((0, 62), feed="heartbeat_tol")


@_register("tolerance_outside")
def _tolerance_outside():
    return _rows_csv((0, 65), feed="heartbeat_tol")


@_register("seq_contract_ok")
def _seq_ok():
    return _seq_csv((1, 2, 3))


@_register("seq_contract_hole")
def _seq_hole():
    return _seq_csv((1, 2, 4))


@_register("seq_nocontract")
def _seq_none():
    rows = [(str(T0), "SECRET_CANARY", "PX_CANARY_12.5"), (str(T0 + 60), "2", "SZ_CANARY_7"), (str(T0 + 120), "4", "alpha")]
    return {"inputs": [_plain("seq_nocontract", rows, header="event_ts,seq,note")]}


@_register("seq_interleaved")
def _seq_interleaved():
    return _seq_csv((10, 12, 99), feed="seq_interleaved")


def _candle_inputs(trade_offsets, candle_offsets):
    trades = _csv(None, [(str(T0 + offset), "SECRET_CANARY") for offset in trade_offsets])
    candles = _csv(None, [(str(T0 + offset), "PX_CANARY_12.5") for offset in candle_offsets])
    return {"inputs": [_input("trades", "candle_trades", trades), _input("candles", "candles", candles)]}


@_register("candle_gap")
def _candle_gap():
    return _candle_inputs((0, 120), (0,))


@_register("candle_ok")
def _candle_ok():
    return _candle_inputs((0, 120), (0, 120))


@_register("candle_no_crosscheck")
def _candle_no():
    candles = _csv(None, [("1700000000", "SECRET_CANARY")])
    return {"inputs": [_input("candles", "candles", candles)]}


@_register("candle_quiet_minute")
def _candle_quiet():
    return _candle_inputs((0,), (0,))


@_register("unverified_placeholder")
def _unverified():
    payload = b"1700000000,SECRET_CANARY\n"
    return {"inputs": [_input("case_main", "unspecified", payload)]}


@_register("nonmonotonic")
def _nonmonotonic():
    return _rows_csv((0, 120, 60))


@_register("filename_not_cadence")
def _filename():
    built = _rows_csv((0, 60, 180), logical="range_1700000000_1700003600")
    return built


@_register("depth_bands")
def _depth():
    rows = []
    for offset in (0, 60, 120):
        rows.append(
            {
                "event_ts": T0 + offset,
                "band_near": ["PX_CANARY_12.5", 1],
                "band_far": [1],
                "lvl_0": [1, 2, 3],
                "lvl_1": ["SZ_CANARY_7"],
                "px": "PX_CANARY_12.5",
                "sz": "SZ_CANARY_7",
            }
        )
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows).encode("utf-8")
    return {"inputs": [_input("case_main", "depth", payload)]}


@_register("timed_meta")
def _timed():
    rows = []
    for index, offset in enumerate((0, 60, 120)):
        stamp = T0 + offset
        rows.append((str(stamp), str(stamp + 1), str(stamp + 2), CANARIES[index]))
    return {"inputs": [_plain("timed_meta", rows, header="event_ts,recv_ts,pub_ts,note")]}


@_register("zero_rows")
def _zero():
    return {"inputs": [_input("case_main", "heartbeat", b"event_ts,note\n")]}


@_register("registry_rejects_manufactured_delay")
def _registry_bad():
    document = json.loads(registry_bytes().decode("utf-8"))
    document["feeds"][0]["permissible_publication_delay_ms"] = 15
    built = _rows_csv((0, 60, 120))
    built["registry_bytes"] = json.dumps(document).encode("utf-8")
    return built


@_register("cpu_at")
def _cpu_at():
    return {**_rows_csv((0, 60, 120)), "meter": FixedMeter(10000, 0)}


@_register("cpu_above")
def _cpu_above():
    return {**_rows_csv((0, 60, 120)), "meter": FixedMeter(10001, 0)}


@_register("wall_at")
def _wall_at():
    return {**_rows_csv((0, 60, 120)), "meter": FixedMeter(0, 10000)}


@_register("wall_above")
def _wall_above():
    return {**_rows_csv((0, 60, 120)), "meter": FixedMeter(0, 10001)}


def _memory_cap(at_cap: bool):
    probe = _run_built(_rows_csv((0, 60, 120)))
    observed = probe[0]["caps"]["observed"]["memory_bytes"]
    limit = observed if at_cap else observed - 1
    return _with_profile(_rows_csv((0, 60, 120)), memory_bytes=limit)


@_register("memory_at")
def _memory_at():
    return _memory_cap(True)


@_register("memory_above")
def _memory_above():
    return _memory_cap(False)


def _output_cap(at_cap: bool):
    cap = 65536
    report = None
    for _ in range(8):
        built = _with_profile(_rows_csv((0, 60, 120)), output_bytes=cap)
        report, _extra = _run_built(built)
        length = len(canonical_bytes(report))
        if report["status"] == "COMPLETE" and length == cap:
            break
        cap = length
    if not at_cap:
        cap -= 1
    return _with_profile(_rows_csv((0, 60, 120)), output_bytes=cap)


@_register("output_at")
def _output_at():
    return _output_cap(True)


@_register("output_above")
def _output_above():
    return _output_cap(False)


@_register("gap_at")
def _gap_at():
    return _with_profile(_rows_csv((0, 60, 180, 300)), max_gaps=2)


@_register("gap_above")
def _gap_above():
    return _with_profile(_rows_csv((0, 60, 180, 300, 420)), max_gaps=2)


def _record_limit(at_cap: bool):
    probe, _extra = _run_built(_rows_csv((0, 60, 120)))
    observed = probe["caps"]["observed"]["max_record_bytes"]
    return _with_profile(_rows_csv((0, 60, 120)), max_record_bytes=observed if at_cap else observed - 1)


@_register("record_at")
def _record_at():
    return _record_limit(True)


@_register("record_above")
def _record_above():
    return _record_limit(False)


def _size_limit(kind, at_cap: bool):
    payload = _csv("event_ts,note", _periodic_rows())
    limit = len(payload) if at_cap else len(payload) - 1
    overrides = {"max_input_bytes": max(limit, 1), "max_decompressed_bytes": len(payload)}
    if kind == "decomp":
        overrides = {"max_decompressed_bytes": limit, "max_input_bytes": len(payload)}
    built = _with_profile({"inputs": [_input("case_main", "heartbeat", payload)]}, **overrides)
    return built


@_register("decomp_at")
def _decomp_at():
    return _size_limit("decomp", True)


@_register("decomp_above")
def _decomp_above():
    return _size_limit("decomp", False)


@_register("input_at")
def _input_at():
    return _size_limit("input", True)


@_register("input_above")
def _input_above():
    return _size_limit("input", False)


def _member_zip(count, limit):
    parts = []
    offsets = (0, 60, 120)
    for index in range(count):
        payload = _csv("event_ts,note", [(str(T0 + offsets[index]), CANARIES[index])])
        parts.append((f"p{index}.csv", payload))
    blob = _zip(parts)
    members = [_member(f"part_{index}", index, payload, "file", name) for index, (name, payload) in enumerate(parts)]
    return _with_profile(
        {"inputs": [_input("case_main", "heartbeat_zip", blob, members)]},
        max_archive_members=limit,
    )


@_register("member_at")
def _member_at():
    return _member_zip(2, 2)


@_register("member_above")
def _member_above():
    return _member_zip(3, 2)


@_register("dup_at")
def _dup_at():
    return _with_profile(_rows_csv((0, 60)), max_duplicate_keys=2, max_rows=10)


@_register("dup_above")
def _dup_above():
    return _with_profile(_rows_csv((0, 60, 120)), max_duplicate_keys=2, max_rows=10)


@_register("row_at")
def _row_at():
    return _with_profile(_rows_csv((0, 60)), max_rows=2, max_duplicate_keys=10)


@_register("row_above")
def _row_above():
    return _with_profile(_rows_csv((0, 60, 120)), max_rows=2, max_duplicate_keys=10)


@_register("allowlist_tamper")
def _tamper():
    built = _rows_csv((0, 60, 120))
    built["tamper"] = True
    return built


@_register("replace_before_read")
def _replace():
    pinned = _csv("event_ts,note", _periodic_rows())
    item = _plain("heartbeat", _periodic_rows())
    item["mode"] = "replace"
    item["pinned"] = pinned
    item["payload"] = pinned
    secret = b"SECRET_CANARY"
    item["replacement"] = (secret * (len(pinned) // len(secret) + 1))[: len(pinned)]
    return {"inputs": [item]}


@_register("readback_mutation")
def _readback():
    item = _plain("heartbeat", _periodic_rows())
    item["mode"] = "readback"
    item["replacement"] = b"SECRET_CANARY"
    return {"inputs": [item]}


@_register("mid_read_mutation")
def _mid():
    item = _plain("heartbeat", _periodic_rows())
    item["mode"] = "mid"
    return {"inputs": [item]}


@_register("allowlist_mutated")
def _allow_mut():
    item = _plain("heartbeat", _periodic_rows())
    item["mode"] = "mutate_allowlist"
    return {"inputs": [item]}


@_register("profile_ceiling")
def _ceiling():
    return _with_profile(_rows_csv((0, 60, 120)), memory_bytes=9_000_000)


@_register("extra_source_ignored")
def _extra():
    built = _rows_csv((0, 60, 120))
    built["extra_source"] = True
    return built


@_register("env_probe")
def _env():
    return {**_rows_csv((0, 60, 120)), "probe": "env_read"}


@_register("network_probe")
def _net():
    return {**_rows_csv((0, 60, 120)), "probe": "network"}


@_register("vault_probe")
def _vault():
    return {**_rows_csv((0, 60, 120)), "probe": "vault_read"}


@_register("chmod_probe")
def _chmod():
    return {**_rows_csv((0, 60, 120)), "probe": "chmod"}


def run_group(test, group):
    for case in load_cases():
        if case["group"] != group:
            continue
        with test.subTest(case["id"]):
            assert_case(test, case)


class FormatTests(unittest.TestCase):
    def test_formats(self):
        run_group(self, "format")


if __name__ == "__main__":
    unittest.main()
