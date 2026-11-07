"""Resource, schema, redaction, and isolation guards.

Stdlib startup effects are disclosed in STDLIB_STARTUP_EFFECTS. This module
does not read process environment, open sockets, or mutate source bytes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import resource
import time

STDLIB_STARTUP_EFFECTS = (
    "The Python interpreter may read process environment variables and locale "
    "settings during its own startup, before application code runs. This "
    "package does not read environment variables, does not enumerate their "
    "names, and does not use them as configuration or data. Importing it loads the stdlib "
    "modules json, hashlib, re, datetime, time, and resource. resource.getrusage "
    "and time.monotonic are CPU and wall meters only. zipfile, used by the "
    "parser, imports os; application code does not access os.environ."
)

SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "type",
        "const",
        "enum",
        "pattern",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "anyOf",
    }
)

CODE_FILES = (
    "__init__.py",
    "auditor.py",
    "cadence.py",
    "cli.py",
    "guards.py",
    "launch_audit.py",
    "parsers.py",
)

ORDERING = "allowlist_index_member_index_event_ts_row_offset"
FACTS_POLICY = "UNSOURCED_REAL_FACTS_REMAIN_UNVERIFIED"
NULL_COMMIT = "0" * 40

UNIX_S_MIN = 1_000_000_000
UNIX_S_MAX = 4_000_000_000
UNIX_MS_MIN = 1_000_000_000_000
UNIX_MS_MAX = 4_000_000_000_000
UNIX_US_MIN = 1_000_000_000_000_000
UNIX_US_MAX = 4_000_000_000_000_000
CLOCK_MAX = 4_000_000_000_000
SEED_MAX = 2_147_483_647
FILE_READ_CAP = 1_000_000

_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{3})?Z$")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

TINY_SYNTHETIC_PROFILE = {
    "profile_id": "tiny-synthetic-v1",
    "memory_bytes": 262144,
    "cpu_ms": 10000,
    "wall_ms": 10000,
    "output_bytes": 65536,
    "max_gaps": 16,
    "max_record_bytes": 2048,
    "max_decompressed_bytes": 65536,
    "max_input_bytes": 32768,
    "max_archive_members": 8,
    "max_duplicate_keys": 64,
    "max_rows": 128,
    "base_memory_bytes": 2048,
    "row_memory_bytes": 48,
    "dup_memory_bytes": 64,
    "day_memory_bytes": 32,
    "gap_memory_bytes": 128,
    "member_memory_bytes": 40,
}

_PROFILE_RANGES = {
    "memory_bytes": (1, 8_000_000),
    "cpu_ms": (1, 60_000),
    "wall_ms": (1, 60_000),
    "output_bytes": (1, 1_000_000),
    "max_gaps": (1, 10_000),
    "max_record_bytes": (1, 1_000_000),
    "max_decompressed_bytes": (1, 8_000_000),
    "max_input_bytes": (1, 8_000_000),
    "max_archive_members": (1, 1_000),
    "max_duplicate_keys": (1, 100_000),
    "max_rows": (1, 100_000),
    "base_memory_bytes": (1, 1_048_576),
    "row_memory_bytes": (1, 1024),
    "dup_memory_bytes": (1, 1024),
    "day_memory_bytes": (1, 1024),
    "gap_memory_bytes": (1, 1024),
    "member_memory_bytes": (1, 1024),
}

PROBE_CODES = {
    "env_read": "ENV_READ_FORBIDDEN",
    "getenv": "ENV_READ_FORBIDDEN",
    "network": "NETWORK_FORBIDDEN",
    "socket": "NETWORK_FORBIDDEN",
    "http": "NETWORK_FORBIDDEN",
    "vault_read": "VAULT_READ_FORBIDDEN",
    "keyring": "VAULT_READ_FORBIDDEN",
    "chmod": "SOURCE_MUTATION_FORBIDDEN",
    "utime": "SOURCE_MUTATION_FORBIDDEN",
    "rename": "SOURCE_MUTATION_FORBIDDEN",
    "delete": "SOURCE_MUTATION_FORBIDDEN",
    "cleanup": "SOURCE_MUTATION_FORBIDDEN",
    "unlink": "SOURCE_MUTATION_FORBIDDEN",
    "write_source": "SOURCE_MUTATION_FORBIDDEN",
    "truncate": "SOURCE_MUTATION_FORBIDDEN",
}

UNKNOWN_ONLY = frozenset(
    {
        "CADENCE_UNKNOWN",
        "CALENDAR_UNVERIFIED",
        "MISSING_EVIDENCE",
        "MONOTONICITY_BLOCKS_CADENCE",
        "NO_TRADE_CROSSCHECK",
        "PUBLICATION_DELAY_UNVERIFIED",
        "VERIFICATION_UNVERIFIED",
    }
)


class AuditStop(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def forbid(op: str, detail: object = None) -> None:
    """Fail closed. The detail argument is discarded and never stored."""
    del detail
    raise AuditStop(PROBE_CODES.get(op, "PROBE_REJECTED"))


def clean_call(fn, fallback: str, *args, **kwargs):
    """Run fn and re-raise AuditStop without input-bearing frames or messages."""
    code = None
    try:
        return fn(*args, **kwargs)
    except AuditStop as stop:
        code = stop.code
    except Exception:
        code = fallback
    finally:
        args = None
        kwargs = None
        fn = None
    raise AuditStop(code or fallback) from None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def package_root() -> str:
    return __file__.rsplit("/", 1)[0]


def read_package_bytes(rel: str) -> bytes:
    path = package_root() + "/" + rel
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) > FILE_READ_CAP:
        raise AuditStop("CODE_HASH_FAILED")
    return data


def _load_json_package(rel: str) -> object:
    try:
        return json.loads(read_package_bytes(rel))
    except AuditStop:
        raise
    except Exception:
        raise AuditStop("CODE_HASH_FAILED") from None


REPORT_SCHEMA = _load_json_package("schemas/coverage_report.schema.json")
REGISTRY_SCHEMA = _load_json_package("schemas/source_registry.schema.json")
REASON_CODES = tuple(REPORT_SCHEMA["properties"]["reasons"]["items"]["enum"])
REASON_SET = frozenset(REASON_CODES)


def unexpected_schema_keywords(schema: object, found: list[str] | None = None) -> list[str]:
    if found is None:
        found = []
    if not isinstance(schema, dict):
        return found
    for key, value in schema.items():
        if key not in SUPPORTED_SCHEMA_KEYWORDS:
            found.append(key)
        if key == "properties" and isinstance(value, dict):
            for child in value.values():
                unexpected_schema_keywords(child, found)
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            unexpected_schema_keywords(value, found)
        elif key == "anyOf" and isinstance(value, list):
            for child in value:
                unexpected_schema_keywords(child, found)
        elif key == "$defs" and isinstance(value, dict):
            for child in value.values():
                unexpected_schema_keywords(child, found)
    return found


def _resolve_ref(ref: str, root: dict) -> dict:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise AuditStop("OUTPUT_SCHEMA_INVALID")
    node: object = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise AuditStop("OUTPUT_SCHEMA_INVALID")
        node = node[part]
    if not isinstance(node, dict):
        raise AuditStop("OUTPUT_SCHEMA_INVALID")
    return node


def _type_ok(instance: object, declared: object) -> bool:
    names = (declared,) if isinstance(declared, str) else tuple(declared)
    for name in names:
        if name == "object" and isinstance(instance, dict):
            return True
        if name == "array" and isinstance(instance, list):
            return True
        if name == "string" and isinstance(instance, str):
            return True
        if name == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
            return True
        if name == "boolean" and isinstance(instance, bool):
            return True
        if name == "null" and instance is None:
            return True
        if name == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
            return True
    return False


def validate(instance: object, schema: dict, pointer: str = "", root: dict | None = None) -> list[str]:
    if root is None:
        root = schema
    if "$ref" in schema:
        try:
            target = _resolve_ref(schema["$ref"], root)
        except AuditStop:
            return [pointer or "/"]
        return validate(instance, target, pointer, root)
    errors: list[str] = []
    if "anyOf" in schema:
        matched = False
        fallback: list[str] = [pointer or "/"]
        for branch in schema["anyOf"]:
            branch_errors = validate(instance, branch, pointer, root)
            if not branch_errors:
                matched = True
                break
            fallback = branch_errors
        if not matched:
            errors.extend(fallback)
        return errors
    declared = schema.get("type")
    if declared is not None and not _type_ok(instance, declared):
        return [pointer or "/"]
    if "const" in schema and instance != schema["const"]:
        errors.append(pointer or "/")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(pointer or "/")
    if "pattern" in schema and isinstance(instance, str):
        if re.fullmatch(schema["pattern"], instance) is None:
            errors.append(pointer or "/")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(pointer or "/")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(pointer or "/")
    if isinstance(instance, dict) and declared == "object":
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{pointer}/{key}")
        for key, value in instance.items():
            child = f"{pointer}/{key}"
            if key in props:
                errors.extend(validate(value, props[key], child, root))
            elif additional is False:
                errors.append(child)
    if isinstance(instance, list) and declared == "array":
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(pointer or "/")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(pointer or "/")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(validate(value, item_schema, f"{pointer}/{index}", root))
    return errors


def schema_errors(instance: object, schema: dict) -> list[str]:
    try:
        return validate(instance, schema)
    except AuditStop:
        return ["/"]


class FixedMeter:
    mode = "injected"

    def __init__(self, cpu_ms: int, wall_ms: int):
        self._cpu = cpu_ms
        self._wall = wall_ms

    def cpu_ms(self) -> int:
        return self._cpu

    def wall_ms(self) -> int:
        return self._wall


class ProcessMeter:
    mode = "process"

    def __init__(self) -> None:
        self._r0 = resource.getrusage(resource.RUSAGE_SELF)
        self._t0 = time.monotonic()

    def cpu_ms(self) -> int:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        delta = (usage.ru_utime + usage.ru_stime) - (self._r0.ru_utime + self._r0.ru_stime)
        return int(delta * 1000)

    def wall_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)


class Ledger:
    def __init__(self, profile: dict, meter: FixedMeter | ProcessMeter):
        self.p = profile
        self.meter = meter
        self.memory = 0
        self.rows = 0
        self.decompressed = 0
        self.input_bytes = 0
        self.members = 0
        self.duplicate_keys = 0
        self.gaps = 0
        self.max_record = 0

    def fail(self, code: str) -> None:
        raise AuditStop(code)

    def check_meter(self) -> None:
        cpu = self.meter.cpu_ms()
        wall = self.meter.wall_ms()
        if not isinstance(cpu, int) or isinstance(cpu, bool) or cpu < 0:
            self.fail("CPU_CAP")
        if not isinstance(wall, int) or isinstance(wall, bool) or wall < 0:
            self.fail("WALL_CAP")
        if cpu > self.p["cpu_ms"]:
            self.fail("CPU_CAP")
        if wall > self.p["wall_ms"]:
            self.fail("WALL_CAP")

    def reserve_memory(self, amount: int) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            self.fail("MEMORY_CAP")
        if self.memory + amount > self.p["memory_bytes"]:
            self.fail("MEMORY_CAP")
        self.memory += amount

    def add_input(self, amount: int) -> None:
        if amount < 0 or self.input_bytes + amount > self.p["max_input_bytes"]:
            self.fail("INPUT_SIZE_CAP")
        self.input_bytes += amount

    def add_decomp(self, amount: int) -> None:
        if amount < 0 or self.decompressed + amount > self.p["max_decompressed_bytes"]:
            self.fail("DECOMPRESSION_CAP")
        self.decompressed += amount

    def add_member(self) -> None:
        if self.members + 1 > self.p["max_archive_members"]:
            self.fail("MEMBER_CAP")
        self.reserve_memory(self.p["member_memory_bytes"])
        self.members += 1

    def note_record(self, amount: int) -> None:
        if amount < 0 or amount > self.p["max_record_bytes"]:
            self.fail("RECORD_SIZE_CAP")
        if amount > self.max_record:
            self.max_record = amount

    def add_gap(self) -> None:
        if self.gaps + 1 > self.p["max_gaps"]:
            self.fail("GAP_ENUMERATION_CAP")
        self.reserve_memory(self.p["gap_memory_bytes"])
        self.gaps += 1

    def snapshot(self) -> dict:
        cpu = self.meter.cpu_ms()
        wall = self.meter.wall_ms()
        if not isinstance(cpu, int) or isinstance(cpu, bool) or cpu < 0:
            cpu = self.p["cpu_ms"] + 1
        if not isinstance(wall, int) or isinstance(wall, bool) or wall < 0:
            wall = self.p["wall_ms"] + 1
        return {
            "memory_bytes": self.memory,
            "cpu_ms": cpu,
            "wall_ms": wall,
            "output_bytes": 0,
            "gaps": self.gaps,
            "rows": self.rows,
            "decompressed_bytes": self.decompressed,
            "input_bytes": self.input_bytes,
            "members": self.members,
            "duplicate_keys": self.duplicate_keys,
            "max_record_bytes": self.max_record,
        }


def check_profile(profile: object) -> dict:
    if not isinstance(profile, dict):
        raise AuditStop("PROFILE_INVALID")
    expected = {"profile_id", *_PROFILE_RANGES}
    if set(profile) != expected:
        raise AuditStop("PROFILE_INVALID")
    if not isinstance(profile["profile_id"], str) or _PROFILE_ID.fullmatch(profile["profile_id"]) is None:
        raise AuditStop("PROFILE_INVALID")
    cleaned = {"profile_id": profile["profile_id"]}
    for key, (low, high) in _PROFILE_RANGES.items():
        value = profile[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < low or value > high:
            raise AuditStop("PROFILE_INVALID")
        cleaned[key] = value
    return cleaned


def feed_ref(feed: dict) -> str:
    return "/".join(
        (feed["venue"], feed["source"], feed["feed"], feed["instrument"], feed["era"])
    )


def _optional_names(feed: dict) -> None:
    mapping = {
        "receive": "receive_timestamp",
        "publication": "publication_timestamp",
        "vintage": "vintage_timestamp",
    }
    fields = feed["fields"]
    for key, role in mapping.items():
        name = feed["optional_timestamps"][key]
        matched = [field for field in fields if field["role"] == role]
        if name is None and matched:
            raise AuditStop("REGISTRY_INVALID")
        if name is not None and (len(matched) != 1 or matched[0]["name"] != name):
            raise AuditStop("REGISTRY_INVALID")


def _check_feed(feed: dict, refs: set[str]) -> None:
    fields = feed["fields"]
    indexes = [field["index"] for field in fields]
    names = [field["name"] for field in fields]
    if sorted(indexes) != list(range(len(fields))) or len(names) != len(set(names)):
        raise AuditStop("REGISTRY_INVALID")
    stamped = [field for field in fields if field["role"] == "event_timestamp"]
    if len(stamped) != 1 or stamped[0]["name"] != feed["timestamp"]["field"]:
        raise AuditStop("REGISTRY_INVALID")
    if stamped[0]["type"] != "timestamp" or stamped[0]["required"] is not True:
        raise AuditStop("REGISTRY_INVALID")
    fmt = feed["format"]
    if fmt["kind"] == "header_csv":
        if fmt["header"] is not True:
            raise AuditStop("REGISTRY_INVALID")
    elif fmt["header"] is not False:
        raise AuditStop("REGISTRY_INVALID")
    _optional_names(feed)
    contract = feed["sequence_contract"]
    sequenced = [field for field in fields if field["role"] == "sequence"]
    if contract is None and sequenced:
        raise AuditStop("REGISTRY_INVALID")
    if contract is not None:
        if contract["scope"] == "per_instrument" and contract["contiguous"] is not True:
            raise AuditStop("REGISTRY_INVALID")
        if contract["scope"] == "global_interleaved" and contract["contiguous"] is not False:
            raise AuditStop("REGISTRY_INVALID")
        if len(sequenced) != 1 or sequenced[0]["name"] != contract["id_field"]:
            raise AuditStop("REGISTRY_INVALID")
        if sequenced[0]["type"] != "sequence":
            raise AuditStop("REGISTRY_INVALID")
    depth = feed["depth"]
    bands = [field["name"] for field in fields if field["role"] == "depth_band"]
    levels = [field["name"] for field in fields if field["role"] == "executable_level"]
    if (bands or levels) and feed["format"]["kind"] not in ("jsonl", "json_array"):
        raise AuditStop("REGISTRY_INVALID")
    if depth is None and (bands or levels):
        raise AuditStop("REGISTRY_INVALID")
    if depth is not None:
        if list(depth["band_fields"]) != bands or list(depth["level_fields"]) != levels:
            raise AuditStop("REGISTRY_INVALID")
        if depth["bands_are_not_executable_levels"] is not True:
            raise AuditStop("REGISTRY_INVALID")
    xref = feed["crosscheck_feed_ref"]
    if xref is not None and (xref not in refs or xref == feed_ref(feed)):
        raise AuditStop("REGISTRY_INVALID")
    if feed["permissible_publication_delay_ms"] is not None:
        raise AuditStop("REGISTRY_INVALID")
    status = feed["verification_status"]
    if status == "UNVERIFIED":
        if (
            feed["cadence_ms"] is not None
            or feed["tolerance_ms"] is not None
            or feed["calendar"] != "UNVERIFIED"
            or feed["observation"] != "unknown"
            or feed["sparse_rule"] is not None
            or contract is not None
            or depth is not None
            or xref is not None
            or feed["audit_publication_delay"] is not True
        ):
            raise AuditStop("REGISTRY_INVALID")
        return
    if status != "SYNTHETIC_DECLARED" or feed["calendar"] != "UTC_CONTINUOUS":
        raise AuditStop("REGISTRY_INVALID")
    if feed["audit_publication_delay"] is not False:
        raise AuditStop("REGISTRY_INVALID")
    if feed["observation"] == "periodic":
        if (
            not isinstance(feed["cadence_ms"], int)
            or isinstance(feed["cadence_ms"], bool)
            or feed["cadence_ms"] < 1
            or not isinstance(feed["tolerance_ms"], int)
            or isinstance(feed["tolerance_ms"], bool)
            or feed["tolerance_ms"] < 0
            or feed["sparse_rule"] is not None
        ):
            raise AuditStop("REGISTRY_INVALID")
        return
    if feed["observation"] != "sparse" or feed["cadence_ms"] is not None or feed["tolerance_ms"] is not None:
        raise AuditStop("REGISTRY_INVALID")
    if feed["sparse_rule"] == "CANDLE_CROSSCHECK_TRADES" and xref is None:
        raise AuditStop("REGISTRY_INVALID")
    if feed["sparse_rule"] == "NO_EVENT_NOT_OUTAGE" and xref is not None:
        raise AuditStop("REGISTRY_INVALID")
    if feed["sparse_rule"] not in ("NO_EVENT_NOT_OUTAGE", "CANDLE_CROSSCHECK_TRADES"):
        raise AuditStop("REGISTRY_INVALID")


def load_registry(raw: bytes) -> dict:
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > FILE_READ_CAP:
        raise AuditStop("REGISTRY_INVALID")
    data = bytes(raw)
    try:
        document = json.loads(data.decode("utf-8"))
    except Exception:
        raise AuditStop("REGISTRY_INVALID") from None
    if schema_errors(document, REGISTRY_SCHEMA):
        raise AuditStop("REGISTRY_INVALID")
    refs = [feed_ref(feed) for feed in document["feeds"]]
    if len(refs) != len(set(refs)):
        raise AuditStop("REGISTRY_INVALID")
    refset = set(refs)
    trusted = {FACTS_POLICY, "source_registry_v1", "coverage_report_v1"}
    by_ref = {}
    for feed in document["feeds"]:
        _check_feed(feed, refset)
        ref = feed_ref(feed)
        by_ref[ref] = feed
        trusted.add(ref)
        for key in ("venue", "source", "feed", "instrument", "series", "era"):
            trusted.add(feed[key])
        for field in feed["fields"]:
            trusted.add(field["name"])
        if feed["depth"] is not None:
            trusted.add(feed["depth"]["notes_code"])
    return {"sha256": sha256_hex(data), "document": document, "by_ref": by_ref, "trusted": trusted}


def to_iso(ts_ms: int) -> str:
    if not isinstance(ts_ms, int) or isinstance(ts_ms, bool) or ts_ms < 0:
        raise AuditStop("TIMESTAMP_PARSE")
    seconds, millis = divmod(ts_ms, 1000)
    try:
        stamp = dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise AuditStop("TIMESTAMP_PARSE") from None
    text = stamp.strftime("%Y-%m-%dT%H:%M:%S")
    if millis:
        text = f"{text}.{millis:03d}"
    text = text + "Z"
    if _ISO.fullmatch(text) is None:
        raise AuditStop("TIMESTAMP_PARSE")
    return text


def to_day(ts_ms: int) -> str:
    day = to_iso(ts_ms)[:10]
    if _DAY.fullmatch(day) is None:
        raise AuditStop("TIMESTAMP_PARSE")
    return day


def rng_fingerprint(seed: int) -> str:
    return sha256_hex(f"coverage-audit-rng-v1|{seed}".encode("ascii"))


def code_sha256() -> str:
    digest = hashlib.sha256()
    for name in CODE_FILES:
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(read_package_bytes(name))
        digest.update(b"\0")
    return digest.hexdigest()


def package_hashes() -> dict:
    return {
        "code_sha256": code_sha256(),
        "lock_sha256": sha256_hex(read_package_bytes("requirements.lock")),
        "source_registry_schema_sha256": sha256_hex(read_package_bytes("schemas/source_registry.schema.json")),
        "coverage_report_schema_sha256": sha256_hex(read_package_bytes("schemas/coverage_report.schema.json")),
    }


STRUCTURAL_KEYS = frozenset(
    {
        "accepted_real_dates",
        "allowlist_observed_sha256",
        "allowlist_sha256",
        "bands_equal_walkable_book",
        "base_commit",
        "cadence_ms",
        "calendar",
        "caps",
        "clock_unix_ms",
        "code",
        "code_sha256",
        "container",
        "count_max",
        "count_min",
        "coverage_report_schema_sha256",
        "cpu_ms",
        "day_counts",
        "decompressed_bytes",
        "depth_bands",
        "depth_note",
        "duplicate_count",
        "duplicate_keys",
        "encoding",
        "era",
        "event_order_sha256",
        "evidence",
        "executable_levels",
        "expected_sequence",
        "facts_policy",
        "feed",
        "feed_ref",
        "feeds",
        "fields",
        "first",
        "format",
        "gap_count",
        "gaps",
        "hashes",
        "index",
        "input_bytes",
        "inputs",
        "instrument",
        "integrity",
        "justification",
        "last",
        "lock_sha256",
        "logical_id",
        "max",
        "max_archive_members",
        "max_decompressed_bytes",
        "max_duplicate_keys",
        "max_gaps",
        "max_input_bytes",
        "max_record_bytes",
        "max_rows",
        "member_id",
        "member_index",
        "members",
        "memory_bytes",
        "meter_mode",
        "min",
        "monotonicity_violations",
        "name",
        "observation",
        "observed",
        "observed_delta_ms",
        "observed_sequence",
        "observed_sha256",
        "observed_size",
        "ordering",
        "output_bytes",
        "parse_errors",
        "payload_sha256",
        "pinned_sha256",
        "pinned_size",
        "presence",
        "profile_id",
        "publication",
        "real_interval_claim",
        "reason",
        "reasons",
        "receive",
        "registry_sha256",
        "report_schema",
        "rng_fingerprint",
        "role",
        "row",
        "rows",
        "rule",
        "run",
        "schema_fingerprint",
        "scope",
        "scope_id",
        "seed",
        "semantics",
        "sequence",
        "series",
        "sha256",
        "source",
        "source_mutation_apis",
        "source_registry_schema_sha256",
        "sparse_rule",
        "stable",
        "status",
        "synthetic_only",
        "timestamps",
        "tolerance_ms",
        "type",
        "undeclared_fields_max",
        "unknowns",
        "utc_day",
        "utc_end",
        "utc_start",
        "venue",
        "verification_status",
        "vintage",
        "wall_ms",
    }
)


def static_trusted() -> set[str]:
    trusted = {
        "COMPLETE",
        "UNKNOWN",
        "INCOMPLETE_FAIL",
        "coverage_report_v1",
        "source_registry_v1",
        FACTS_POLICY,
        ORDERING,
        "denied",
        "injected",
        "process",
        "utf-8",
        "plain",
        "gzip",
        "zip",
        "header_csv",
        "headerless_csv",
        "jsonl",
        "json_array",
        "file",
        "directory",
        "gzip_member",
        "periodic",
        "sparse",
        "unknown",
        "UTC_CONTINUOUS",
        "UNVERIFIED",
        "SYNTHETIC_DECLARED",
        "PROVEN",
        "NOT_APPLICABLE",
        "FAILED",
        "NOT_CHECKED",
        "NO_SEQUENCE_CONTRACT",
        "INTERLEAVED_IDS_NOT_PER_INSTRUMENT",
        "MONOTONIC_PROVEN",
        "SEQUENCE_GAP",
        "SEQUENCE_FIELD_ABSENT",
        "SEQUENCE_NONMONOTONIC",
        "PERIODIC_CADENCE_V1",
        "SEQUENCE_CONTRACT",
        "CANDLE_MISSING_WHILE_TRADE_PRESENT",
        "DELTA_EXCEEDS_CADENCE_PLUS_TOLERANCE",
        "SEQUENCE_HOLE",
        "TRADE_MINUTE_WITHOUT_CANDLE",
        "BANDS_DISTINCT_FROM_WALKABLE_LEVELS",
        "timestamp",
        "discarded",
        "empty",
        "array",
        "number",
        "string",
        "bool",
        "null",
        "object",
        "absent",
        "present",
        "partial",
        "synthetic_unscoped",
        "rejected_profile",
        "tiny-synthetic-v1",
        "NO_EVENT_NOT_OUTAGE",
        "CANDLE_CROSSCHECK_TRADES",
    }
    trusted.update(REASON_CODES)
    trusted.update(STRUCTURAL_KEYS)
    return trusted


def string_allowed(value: str, trusted: set[str]) -> bool:
    if value in trusted:
        return True
    if _HEX64.fullmatch(value) or _HEX40.fullmatch(value):
        return True
    if _ISO.fullmatch(value) or _DAY.fullmatch(value):
        return True
    return False


def walk_strings(node: object, found: list[str] | None = None) -> list[str]:
    if found is None:
        found = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            walk_strings(key, found)
            walk_strings(value, found)
    elif isinstance(node, list):
        for value in node:
            walk_strings(value, found)
    elif isinstance(node, (int, bool)) or node is None:
        if isinstance(node, bool) or isinstance(node, int):
            return found
    else:
        found.append("<NON_PRIMITIVE>")
    return found


def redaction_ok(report: object, trusted: set[str]) -> bool:
    for value in walk_strings(report):
        if value == "<NON_PRIMITIVE>" or not string_allowed(value, trusted):
            return False
    return True
