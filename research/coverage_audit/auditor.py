"""Deterministic timestamp and schema coverage auditor.

COMPLETE is returned only when every required check for the declared synthetic
scope is proven. Proven coverage holes, cap hits, and unparsed input fail
closed. Missing evidence stays UNKNOWN. No real interval is claimed.
"""

from __future__ import annotations

import hashlib
import json
import re

from research.coverage_audit.cadence import analyze
from research.coverage_audit.guards import (
    activate_runtime_denies,
    restore_runtime_denies,
    NULL_COMMIT,
    ORDERING,
    REASON_SET,
    TINY_SYNTHETIC_PROFILE,
    UNKNOWN_ONLY,
    AuditStop,
    FixedMeter,
    Ledger,
    ProcessMeter,
    canonical_bytes,
    check_profile,
    forbid,
    load_registry,
    package_hashes,
    redaction_ok,
    rng_fingerprint,
    schema_errors,
    sha256_hex,
    static_trusted,
    to_day,
    to_iso,
    REPORT_SCHEMA,
)
from research.coverage_audit.parsers import Acceptor, Fingerprint, parse_input

_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9_.-]{1,64}/?$")
_ALLOWLIST_KEYS = frozenset({"allowlist_id", "registry_sha256", "profile_id", "inputs"})
_INPUT_KEYS = frozenset({"logical_id", "sha256", "size", "feed_ref", "members"})
_PARSE_CODES = frozenset(
    {
        "CSV_SYNTAX",
        "DECOMPRESSION_CAP",
        "ENCODING_ERROR",
        "FIELD_COUNT",
        "GZIP_CORRUPT",
        "GZIP_CRC",
        "GZIP_MAGIC",
        "GZIP_TRUNCATED",
        "GZIP_TRUNCATED_TAIL",
        "JSON_MALFORMED",
        "MEMBER_CAP",
        "MEMBER_HASH_MISMATCH",
        "MEMBER_MISSING",
        "MEMBER_ORDER_MISMATCH",
        "PATH_TRAVERSAL_MEMBER",
        "RECORD_SIZE_CAP",
        "SCHEMA_DRIFT",
        "SCHEMA_MISMATCH",
        "SEQUENCE_PARSE",
        "TIMESTAMP_PARSE",
        "TIMESTAMP_UNIT",
        "UNLISTED_MEMBER",
        "UNSUPPORTED_FORMAT",
        "ZIP_CORRUPT",
        "ZIP_CRC",
        "ZIP_ENCRYPTED",
        "ZIP_TRUNCATED",
        "ZIP_UNSUPPORTED",
    }
)


def run_audit(
    *,
    registry_bytes: bytes,
    allowlist: dict,
    allowlist_sha256: str,
    sources: dict,
    base_commit: str,
    clock_unix_ms: int,
    seed: int,
    profile: dict | None = None,
    meter=None,
    probe: str | None = None,
) -> dict:
    state = activate_runtime_denies()
    try:
        try:
            return _execute(
            registry_bytes=registry_bytes,
            allowlist=allowlist,
            allowlist_sha256=allowlist_sha256,
            sources=sources,
            base_commit=base_commit,
            clock_unix_ms=clock_unix_ms,
            seed=seed,
            profile=profile,
            meter=meter,
            probe=probe,
            )
        except Exception:
            return _bare("UNEXPECTED_STOP")
    finally:
        restore_runtime_denies(state)


def _execute(
    *,
    registry_bytes,
    allowlist,
    allowlist_sha256,
    sources,
    base_commit,
    clock_unix_ms,
    seed,
    profile,
    meter,
    probe,
) -> dict:
    reasons: list[str] = []
    parse_errors: list[dict] = []
    gaps: list[dict] = []
    unknown_rows: list[dict] = []
    integrity_inputs: list[dict] = []
    order: list[tuple] = []
    trusted = static_trusted()
    scope_id = "synthetic_unscoped"
    pins = None
    registry = None
    observed_allowlist = None
    start_bytes = None
    current_logical = None

    base = base_commit if isinstance(base_commit, str) and re.fullmatch(r"[0-9a-f]{40}", base_commit) else None
    if base is None:
        _add(reasons, "BASE_UNPINNED")
        base = NULL_COMMIT
    clock = clock_unix_ms if _clock_ok(clock_unix_ms) else None
    if clock is None:
        _add(reasons, "CLOCK_UNPINNED")
        clock = 0
    seed_value = seed if _seed_ok(seed) else None
    if seed_value is None:
        _add(reasons, "SEED_UNPINNED")
        seed_value = 0
    try:
        active_profile = check_profile(TINY_SYNTHETIC_PROFILE if profile is None else profile)
    except AuditStop as stop:
        _add(reasons, stop.code)
        active_profile = check_profile(TINY_SYNTHETIC_PROFILE)
    trusted.add(active_profile["profile_id"])
    if meter is None:
        meter = ProcessMeter()
    if getattr(meter, "mode", None) not in ("injected", "process"):
        _add(reasons, "PROFILE_INVALID")
        meter = FixedMeter(0, 0)
    ledger = Ledger(active_profile, meter)
    try:
        hashes = package_hashes()
    except AuditStop as stop:
        _add(reasons, stop.code)
        hashes = {
            "code_sha256": None,
            "lock_sha256": None,
            "source_registry_schema_sha256": None,
            "coverage_report_schema_sha256": None,
        }
    try:
        registry = load_registry(registry_bytes if isinstance(registry_bytes, (bytes, bytearray)) else b"")
        trusted.update(registry["trusted"])
    except AuditStop as stop:
        _add(reasons, stop.code)
        registry = None
    try:
        start_bytes = canonical_bytes(allowlist)
        observed_allowlist = sha256_hex(start_bytes)
    except Exception:
        _add(reasons, "ALLOWLIST_INVALID")
        start_bytes = None
    if not isinstance(allowlist_sha256, str) or _HEX64.fullmatch(allowlist_sha256) is None:
        _add(reasons, "ALLOWLIST_HASH_MISMATCH")
        allowlist_sha256 = None
    elif observed_allowlist is not None and observed_allowlist != allowlist_sha256:
        _add(reasons, "ALLOWLIST_HASH_MISMATCH")
    if reasons:
        return _finish(
            reasons, [], [], unknown_rows, parse_errors, integrity_inputs, order,
            scope_id, active_profile, meter, ledger, hashes, registry, base, clock,
            seed_value, allowlist_sha256, observed_allowlist, trusted,
        )
    try:
        snapshot = json.loads(start_bytes)
        scope_id, pins = _plans(snapshot, registry, active_profile, trusted)
    except AuditStop as stop:
        _add(reasons, stop.code)
        return _finish(
            reasons, [], [], unknown_rows, parse_errors, integrity_inputs, order,
            scope_id, active_profile, meter, ledger, hashes, registry, base, clock,
            seed_value, allowlist_sha256, observed_allowlist, trusted,
        )
    if probe is not None:
        try:
            forbid(str(probe) if isinstance(probe, str) else "probe")
        except AuditStop as stop:
            _add(reasons, stop.code)
        return _finish(
            reasons, [], [], unknown_rows, parse_errors, integrity_inputs, order,
            scope_id, active_profile, meter, ledger, hashes, registry, base, clock,
            seed_value, allowlist_sha256, observed_allowlist, trusted,
        )
    states = {}
    try:
        ledger.reserve_memory(active_profile["base_memory_bytes"])
        ledger.check_meter()
        for pin in pins:
            current_logical = pin["logical_id"]
            _read_and_parse(pin, sources if isinstance(sources, dict) else {}, registry, ledger, states, order, integrity_inputs)
            current_logical = None
        _analyze(pins, registry, states, ledger, gaps, unknown_rows, reasons)
        ledger.check_meter()
    except AuditStop as stop:
        _add(reasons, stop.code)
        if current_logical is not None and stop.code in _PARSE_CODES:
            parse_errors.append(
                {"code": stop.code, "logical_id": current_logical, "member_index": None, "row": None}
            )
    if start_bytes is not None:
        try:
            if canonical_bytes(allowlist) != start_bytes:
                _add(reasons, "ALLOWLIST_MUTATED")
        except Exception:
            _add(reasons, "ALLOWLIST_MUTATED")
    feed_rows = _feed_rows(pins, registry, states, unknown_rows)
    return _finish(
        reasons, feed_rows, gaps, unknown_rows, parse_errors, integrity_inputs, order,
        scope_id, active_profile, meter, ledger, hashes, registry, base, clock,
        seed_value, allowlist_sha256, observed_allowlist, trusted,
    )


def _plans(snapshot, registry, profile, trusted: set[str]):
    if not isinstance(snapshot, dict) or set(snapshot) != _ALLOWLIST_KEYS:
        raise AuditStop("ALLOWLIST_INVALID")
    scope_id = snapshot["allowlist_id"]
    if not isinstance(scope_id, str) or _ID.fullmatch(scope_id) is None:
        raise AuditStop("ALLOWLIST_UNSAFE")
    trusted.add(scope_id)
    if snapshot["registry_sha256"] != registry["sha256"] or not isinstance(snapshot["registry_sha256"], str):
        raise AuditStop("REGISTRY_HASH_MISMATCH")
    if snapshot["profile_id"] != profile["profile_id"]:
        raise AuditStop("ALLOWLIST_PROFILE_MISMATCH")
    inputs = snapshot["inputs"]
    if not isinstance(inputs, list) or not inputs:
        raise AuditStop("ALLOWLIST_INVALID")
    pins = []
    for item in inputs:
        if not isinstance(item, dict) or set(item) != _INPUT_KEYS:
            raise AuditStop("ALLOWLIST_INVALID")
        logical_id = item["logical_id"]
        ref = item["feed_ref"]
        if not isinstance(logical_id, str) or _ID.fullmatch(logical_id) is None:
            raise AuditStop("ALLOWLIST_UNSAFE")
        if not isinstance(ref, str) or ref not in registry["by_ref"]:
            raise AuditStop("ALLOWLIST_INVALID")
        if not isinstance(item["sha256"], str) or _HEX64.fullmatch(item["sha256"]) is None:
            raise AuditStop("ALLOWLIST_INVALID")
        size = item["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > 8_000_000:
            raise AuditStop("ALLOWLIST_INVALID")
        members = _member_pins(item["members"], registry["by_ref"][ref])
        trusted.add(logical_id)
        trusted.add(ref)
        for member in members:
            trusted.add(member["member_id"])
        pins.append(
            {
                "logical_id": logical_id,
                "sha256": item["sha256"],
                "size": size,
                "feed_ref": ref,
                "members": members,
            }
        )
    return scope_id, pins


def _member_pins(members, feed) -> list:
    if not isinstance(members, list):
        raise AuditStop("ALLOWLIST_INVALID")
    container = feed["format"]["container"]
    if container == "plain":
        if members:
            raise AuditStop("ALLOWLIST_INVALID")
        return []
    pins = []
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            raise AuditStop("ALLOWLIST_INVALID")
        kind = member.get("kind")
        keys = set(member)
        if kind == "gzip_member":
            expected = {"member_id", "index", "sha256", "kind"}
        elif kind in ("file", "directory"):
            expected = {"member_id", "index", "sha256", "kind", "name"}
        else:
            raise AuditStop("ALLOWLIST_INVALID")
        if keys != expected:
            raise AuditStop("ALLOWLIST_INVALID")
        if member["index"] != index or not isinstance(index, int):
            raise AuditStop("ALLOWLIST_INVALID")
        if not isinstance(member["member_id"], str) or _ID.fullmatch(member["member_id"]) is None:
            raise AuditStop("ALLOWLIST_UNSAFE")
        if not isinstance(member["sha256"], str) or _HEX64.fullmatch(member["sha256"]) is None:
            raise AuditStop("ALLOWLIST_INVALID")
        if kind != "gzip_member":
            name = member["name"]
            if not isinstance(name, str) or _NAME.fullmatch(name) is None or ".." in name.split("/"):
                raise AuditStop("ALLOWLIST_UNSAFE")
        if container == "gzip" and kind != "gzip_member":
            raise AuditStop("ALLOWLIST_INVALID")
        if container == "zip" and kind not in ("file", "directory"):
            raise AuditStop("ALLOWLIST_INVALID")
        if container == "plain":
            raise AuditStop("ALLOWLIST_INVALID")
        pins.append(dict(member))
    return pins


def _read_and_parse(pin, sources, registry, ledger, states, order, integrity_inputs) -> None:
    record = {
        "logical_id": pin["logical_id"],
        "pinned_sha256": pin["sha256"],
        "pinned_size": pin["size"],
        "observed_sha256": None,
        "observed_size": None,
        "stable": None,
    }
    integrity_inputs.append(record)
    source = sources.get(pin["logical_id"]) if isinstance(sources, dict) else None
    if source is None:
        raise AuditStop("MISSING_INPUT")
    data, ident, observed = _read_source(source, pin, ledger, record)
    feed = registry["by_ref"][pin["feed_ref"]]
    state = states.get(pin["feed_ref"])
    if state is None:
        contract = feed["sequence_contract"]
        track = bool(contract and contract.get("scope") == "per_instrument" and contract.get("contiguous") is True)

        def _on_add(event, ref=pin["feed_ref"]):
            order.append((ref, event.member_index, event.row, event.ts_ms))

        state = {
            "acceptor": Acceptor(ledger, track, _on_add),
            "fingerprint": Fingerprint(),
        }
        states[pin["feed_ref"]] = state
    parse_input(data, feed, pin["members"], ledger, state["acceptor"], state["fingerprint"], pin["logical_id"])
    data = b""
    try:
        back = source.readback()
        stable = source.identity() == ident and isinstance(back, (bytes, bytearray)) and sha256_hex(bytes(back)) == observed and len(back) == pin["size"]
    except Exception:
        stable = False
    record["stable"] = bool(stable)
    if not stable:
        raise AuditStop("SOURCE_CHANGED")


def _read_source(source, pin, ledger, record):
    try:
        size = source.size()
        ident = source.identity()
    except Exception:
        raise AuditStop("INPUT_SIZE_MISMATCH") from None
    if not isinstance(size, int) or isinstance(size, bool) or size != pin["size"]:
        raise AuditStop("INPUT_SIZE_MISMATCH")
    if size > ledger.p["max_input_bytes"] or ledger.input_bytes + size > ledger.p["max_input_bytes"]:
        raise AuditStop("INPUT_SIZE_CAP")
    ledger.charge_before_alloc(size)
    digest = hashlib.sha256()
    buf = bytearray(size)
    total = 0
    try:
        chunks = source.read_chunks(4096)
    except Exception:
        ledger.release_memory(size)
        raise AuditStop("UNEXPECTED_STOP") from None
    try:
        for chunk in chunks:
            ledger.mark_loop(True)
            try:
                ledger.check_meter()
                try:
                    if source.identity() != ident:
                        record["stable"] = False
                        raise AuditStop("MID_READ_MUTATION")
                except AuditStop:
                    raise
                except Exception:
                    raise AuditStop("MID_READ_MUTATION") from None
                if not isinstance(chunk, (bytes, bytearray)):
                    raise AuditStop("UNEXPECTED_STOP")
                if total + len(chunk) > size:
                    record["stable"] = False
                    raise AuditStop("INPUT_SIZE_CAP")
                buf[total : total + len(chunk)] = chunk
                total += len(chunk)
                digest.update(chunk)
            finally:
                ledger.mark_loop(False)
        try:
            if source.identity() != ident:
                record["stable"] = False
                raise AuditStop("MID_READ_MUTATION")
        except AuditStop:
            raise
        except Exception:
            raise AuditStop("MID_READ_MUTATION") from None
        if total != pin["size"]:
            record["observed_size"] = total
            record["stable"] = False
            raise AuditStop("INPUT_SIZE_MISMATCH")
        observed = digest.hexdigest()
        record["observed_sha256"] = observed
        record["observed_size"] = total
        if observed != pin["sha256"]:
            record["stable"] = False
            raise AuditStop("INPUT_HASH_MISMATCH")
        ledger.add_input(total)
        return bytes(buf), ident, observed
    except AuditStop:
        ledger.release_memory(size)
        raise


def _analyze(pins, registry, states, ledger, gaps, unknown_rows, reasons) -> None:
    seen = []
    for pin in pins:
        if pin["feed_ref"] not in seen:
            seen.append(pin["feed_ref"])
    for ref in seen:
        feed = registry["by_ref"][ref]
        state = states.get(ref)
        events = state["acceptor"].events if state else []
        violations = state["acceptor"].violations if state else 0
        cross_ref = feed["crosscheck_feed_ref"]
        if cross_ref is None:
            cross = []
        elif cross_ref not in seen:
            cross = None
        else:
            cross_state = states.get(cross_ref)
            cross = cross_state["acceptor"].events if cross_state else []
        local_unknowns: list[str] = []
        sequence = analyze(feed, events, violations, cross, ledger, gaps, local_unknowns)
        if state is not None:
            state["sequence"] = sequence
            state["unknowns"] = local_unknowns
        for reason in local_unknowns:
            _add(reasons, reason)
            unknown_rows.append({"feed_ref": ref, "reason": reason})
        if sequence["status"] == "FAILED" and sequence["reason"] == "SEQUENCE_NONMONOTONIC":
            _add(reasons, "SEQUENCE_NONMONOTONIC")
        if sequence["status"] == "FAILED" and sequence["reason"] == "SEQUENCE_FIELD_ABSENT":
            _add(reasons, "SCHEMA_MISMATCH")


def _feed_rows(pins, registry, states, unknown_rows) -> list:
    if registry is None:
        return []
    rows = []
    seen = []
    for pin in pins:
        if pin["feed_ref"] not in seen:
            seen.append(pin["feed_ref"])
    for ref in seen:
        feed = registry["by_ref"][ref]
        state = states.get(ref)
        if state is None:
            continue
        acceptor = state["acceptor"]
        fingerprint = state["fingerprint"]
        sequence = state.get("sequence") or {"status": "NOT_CHECKED", "reason": "NOT_CHECKED", "gap_count": 0}
        events = acceptor.events
        rows.append(
            {
                "feed_ref": ref,
                "venue": feed["venue"],
                "source": feed["source"],
                "feed": feed["feed"],
                "instrument": feed["instrument"],
                "series": feed["series"],
                "era": feed["era"],
                "format": feed["format"]["kind"],
                "container": feed["format"]["container"],
                "encoding": "utf-8",
                "members": list(fingerprint.members),
                "rows": len(events),
                "day_counts": _days(events),
                "timestamps": _span([event.ts_ms for event in events]),
                "receive": _span([event.receive_ms for event in events if event.receive_ms is not None]),
                "publication": _span([event.publication_ms for event in events if event.publication_ms is not None]),
                "vintage": _span([event.vintage_ms for event in events if event.vintage_ms is not None]),
                "monotonicity_violations": acceptor.violations,
                "duplicate_count": acceptor.dups,
                "sequence": sequence,
                "schema_fingerprint": _fingerprint(feed, fingerprint),
                "semantics": {
                    "observation": feed["observation"],
                    "sparse_rule": feed["sparse_rule"],
                    "calendar": feed["calendar"],
                    "verification_status": feed["verification_status"],
                },
            }
        )
    return rows


def _fingerprint(feed, fingerprint: Fingerprint) -> dict:
    fields = []
    bands = []
    levels = []
    for spec in feed["fields"]:
        name = spec["name"]
        if spec["role"] in ("depth_band", "executable_level"):
            entry = _depth_entry(name, fingerprint)
            if spec["role"] == "depth_band":
                bands.append(entry)
            else:
                levels.append(entry)
            continue
        if fingerprint.event_rows == 0 or name not in fingerprint.types:
            fields.append({"name": name, "type": "absent", "presence": "absent"})
            continue
        seen = fingerprint.rows.get(name, 0)
        if seen == fingerprint.event_rows:
            presence = "present"
        elif seen == 0:
            presence = "absent"
        else:
            presence = "partial"
        fields.append({"name": name, "type": fingerprint.types[name], "presence": presence})
    note = None if feed["depth"] is None else feed["depth"]["notes_code"]
    return {
        "fields": fields,
        "depth_bands": bands,
        "executable_levels": levels,
        "bands_equal_walkable_book": False,
        "undeclared_fields_max": fingerprint.undeclared_max,
        "depth_note": note,
        "band_threshold": None if feed["depth"] is None or feed["depth"]["band_thresholds_declared"] is not True else feed["depth"]["band_threshold"],
    }


def _depth_entry(name: str, fingerprint: Fingerprint) -> dict:
    if name not in fingerprint.types:
        return {"name": name, "type": "absent", "presence": "absent", "count_min": 0, "count_max": 0}
    seen = fingerprint.rows.get(name, 0)
    if seen == fingerprint.event_rows:
        presence = "present"
    elif seen == 0:
        presence = "absent"
    else:
        presence = "partial"
    return {
        "name": name,
        "type": "array",
        "presence": presence,
        "count_min": fingerprint.count_min.get(name, 0),
        "count_max": fingerprint.count_max.get(name, 0),
    }


def _days(events) -> list:
    counts: dict[str, int] = {}
    for event in events:
        day = to_day(event.ts_ms)
        counts[day] = counts.get(day, 0) + 1
    return [{"utc_day": day, "rows": counts[day]} for day in sorted(counts)]


def _span(values) -> dict:
    if not values:
        return {"first": None, "min": None, "max": None, "last": None}
    return {
        "first": to_iso(values[0]),
        "min": to_iso(min(values)),
        "max": to_iso(max(values)),
        "last": to_iso(values[-1]),
    }


def _finish(
    reasons, feeds, gaps, unknowns, parse_errors, integrity_inputs, order,
    scope_id, profile, meter, ledger, hashes, registry, base, clock, seed,
    allowlist_sha256, observed_allowlist, trusted,
) -> dict:
    if gaps:
        _add(reasons, "PROVEN_COVERAGE_GAP")
    for item in parse_errors:
        _add(reasons, item["code"])
    status = _status(reasons, gaps, unknowns, parse_errors)
    reasons = sorted(set(reasons))
    report = {
        "report_schema": "coverage_report_v1",
        "status": status,
        "reasons": reasons,
        "scope": {
            "scope_id": scope_id if scope_id in trusted else "synthetic_unscoped",
            "synthetic_only": True,
            "accepted_real_dates": None,
            "real_interval_claim": False,
            "facts_policy": "UNSOURCED_REAL_FACTS_REMAIN_UNVERIFIED",
        },
        "run": {
            "profile_id": profile["profile_id"] if profile["profile_id"] in trusted else "rejected_profile",
            "clock_unix_ms": clock,
            "seed": seed,
            "ordering": ORDERING,
            "rng_fingerprint": rng_fingerprint(seed),
            "event_order_sha256": _order_hash(order),
            "base_commit": base,
        },
        "hashes": {
            "base_commit": base,
            "code_sha256": hashes.get("code_sha256"),
            "registry_sha256": None if registry is None else registry["sha256"],
            "lock_sha256": hashes.get("lock_sha256"),
            "source_registry_schema_sha256": hashes.get("source_registry_schema_sha256"),
            "coverage_report_schema_sha256": hashes.get("coverage_report_schema_sha256"),
            "allowlist_sha256": allowlist_sha256,
            "allowlist_observed_sha256": observed_allowlist,
            "inputs": [
                {"logical_id": item["logical_id"], "sha256": item["pinned_sha256"]}
                for item in integrity_inputs
                if item["logical_id"] in trusted
            ],
        },
        "feeds": feeds,
        "gaps": sorted(gaps, key=lambda gap: (gap["feed_ref"], gap["utc_start"], gap["rule"], gap["justification"])),
        "unknowns": sorted(unknowns, key=lambda item: (item["feed_ref"], item["reason"])),
        "caps": _caps(profile, meter, ledger),
        "parse_errors": sorted(
            parse_errors,
            key=lambda item: (item["code"], item["logical_id"] or "", item["member_index"] or -1, item["row"] or -1),
        ),
        "integrity": {
            "allowlist_sha256": allowlist_sha256,
            "allowlist_observed_sha256": observed_allowlist,
            "inputs": [item for item in integrity_inputs if item["logical_id"] in trusted],
            "source_mutation_apis": "denied",
        },
    }
    report["status"] = _status(report["reasons"], report["gaps"], report["unknowns"], report["parse_errors"])
    return _guard_output(report, profile["output_bytes"], trusted, profile, meter, ledger, base, clock, seed, scope_id)


def _guard_output(report, cap, trusted, profile, meter, ledger, base, clock, seed, scope_id) -> dict:
    schema_ok = not schema_errors(report, REPORT_SCHEMA)
    clean = redaction_ok(report, trusted)
    raw = _stabilize(report) if schema_ok and clean else b""
    if not clean:
        report = _minimal("REDACTION_VIOLATION", profile, meter, ledger, report, base, clock, seed, scope_id)
    elif not schema_ok:
        report = _minimal("OUTPUT_SCHEMA_INVALID", profile, meter, ledger, report, base, clock, seed, scope_id)
    elif len(raw) > cap:
        report = _minimal("OUTPUT_CAP", profile, meter, ledger, report, base, clock, seed, scope_id)
    _stabilize(report)
    if schema_errors(report, REPORT_SCHEMA) or not redaction_ok(report, static_trusted() | trusted):
        report = _bare("OUTPUT_SCHEMA_INVALID")
        _stabilize(report)
    return report


def _minimal(code, profile, meter, ledger, source, base, clock, seed, scope_id) -> dict:
    reasons = sorted(set([code, "INCOMPLETE_FAIL"] if False else [code]))
    if code not in REASON_SET:
        reasons = ["UNEXPECTED_STOP"]
    report = {
        "report_schema": "coverage_report_v1",
        "status": "INCOMPLETE_FAIL",
        "reasons": reasons,
        "scope": {
            "scope_id": scope_id if isinstance(scope_id, str) and _ID.fullmatch(scope_id) else "synthetic_unscoped",
            "synthetic_only": True,
            "accepted_real_dates": None,
            "real_interval_claim": False,
            "facts_policy": "UNSOURCED_REAL_FACTS_REMAIN_UNVERIFIED",
        },
        "run": {
            "profile_id": profile["profile_id"],
            "clock_unix_ms": clock,
            "seed": seed,
            "ordering": ORDERING,
            "rng_fingerprint": rng_fingerprint(seed),
            "event_order_sha256": hashlib.sha256(b"").hexdigest(),
            "base_commit": base,
        },
        "hashes": {
            "base_commit": base,
            "code_sha256": source["hashes"]["code_sha256"],
            "registry_sha256": source["hashes"]["registry_sha256"],
            "lock_sha256": source["hashes"]["lock_sha256"],
            "source_registry_schema_sha256": source["hashes"]["source_registry_schema_sha256"],
            "coverage_report_schema_sha256": source["hashes"]["coverage_report_schema_sha256"],
            "allowlist_sha256": source["hashes"]["allowlist_sha256"],
            "allowlist_observed_sha256": source["hashes"]["allowlist_observed_sha256"],
            "inputs": [],
        },
        "feeds": [],
        "gaps": [],
        "unknowns": [],
        "caps": _caps(profile, meter, ledger),
        "parse_errors": [],
        "integrity": {
            "allowlist_sha256": source["integrity"]["allowlist_sha256"],
            "allowlist_observed_sha256": source["integrity"]["allowlist_observed_sha256"],
            "inputs": [],
            "source_mutation_apis": "denied",
        },
    }
    return report


def _bare(code: str) -> dict:
    profile = check_profile(TINY_SYNTHETIC_PROFILE)
    meter = FixedMeter(0, 0)
    ledger = Ledger(profile, meter)
    empty = hashlib.sha256(b"").hexdigest()
    report = {
        "report_schema": "coverage_report_v1",
        "status": "INCOMPLETE_FAIL",
        "reasons": [code if code in REASON_SET else "UNEXPECTED_STOP"],
        "scope": {
            "scope_id": "synthetic_unscoped",
            "synthetic_only": True,
            "accepted_real_dates": None,
            "real_interval_claim": False,
            "facts_policy": "UNSOURCED_REAL_FACTS_REMAIN_UNVERIFIED",
        },
        "run": {
            "profile_id": profile["profile_id"],
            "clock_unix_ms": 0,
            "seed": 0,
            "ordering": ORDERING,
            "rng_fingerprint": rng_fingerprint(0),
            "event_order_sha256": empty,
            "base_commit": NULL_COMMIT,
        },
        "hashes": {
            "base_commit": NULL_COMMIT,
            "code_sha256": None,
            "registry_sha256": None,
            "lock_sha256": None,
            "source_registry_schema_sha256": None,
            "coverage_report_schema_sha256": None,
            "allowlist_sha256": None,
            "allowlist_observed_sha256": None,
            "inputs": [],
        },
        "feeds": [],
        "gaps": [],
        "unknowns": [],
        "caps": _caps(profile, meter, ledger),
        "parse_errors": [],
        "integrity": {
            "allowlist_sha256": None,
            "allowlist_observed_sha256": None,
            "inputs": [],
            "source_mutation_apis": "denied",
        },
    }
    _stabilize(report)
    return report


def _caps(profile, meter, ledger) -> dict:
    observed = ledger.snapshot()
    observed["cpu_ms"] = min(max(observed["cpu_ms"], 0), 60000)
    observed["wall_ms"] = min(max(observed["wall_ms"], 0), 60000)
    return {
        "profile_id": profile["profile_id"],
        "meter_mode": meter.mode if meter.mode in ("injected", "process") else "injected",
        "memory_bytes": profile["memory_bytes"],
        "cpu_ms": profile["cpu_ms"],
        "wall_ms": profile["wall_ms"],
        "output_bytes": profile["output_bytes"],
        "max_gaps": profile["max_gaps"],
        "max_record_bytes": profile["max_record_bytes"],
        "max_decompressed_bytes": profile["max_decompressed_bytes"],
        "max_input_bytes": profile["max_input_bytes"],
        "max_archive_members": profile["max_archive_members"],
        "max_duplicate_keys": profile["max_duplicate_keys"],
        "max_rows": profile["max_rows"],
        "observed": observed,
    }


def _stabilize(report: dict) -> bytes:
    size = 0
    raw = b""
    for _ in range(8):
        report["caps"]["observed"]["output_bytes"] = size
        raw = canonical_bytes(report)
        if len(raw) == size:
            return raw
        size = len(raw)
    report["caps"]["observed"]["output_bytes"] = len(raw)
    return canonical_bytes(report)


def _status(reasons, gaps, unknowns, parse_errors) -> str:
    hard = [reason for reason in reasons if reason not in UNKNOWN_ONLY]
    if hard or parse_errors or gaps:
        return "INCOMPLETE_FAIL"
    if unknowns or any(reason in UNKNOWN_ONLY for reason in reasons):
        return "UNKNOWN"
    return "COMPLETE"


def _order_hash(order) -> str:
    digest = hashlib.sha256()
    for ref, member, row, ts_ms in order:
        digest.update(f"{ref},{member},{row},{ts_ms}\n".encode("ascii"))
    return digest.hexdigest()


def _add(reasons: list, code: str) -> None:
    if code not in REASON_SET:
        code = "UNEXPECTED_STOP"
    if code not in reasons:
        reasons.append(code)


def _clock_ok(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4_000_000_000_000


def _seed_ok(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2_147_483_647
