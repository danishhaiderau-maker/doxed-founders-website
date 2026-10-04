"""Data compatibility: which data version / clean epoch every collected row belongs to, and whether they mix.

Per stream of the Fly mirror (zero Fly calls) this reports: the versions present with exact row counts
(incremental full-file index; sealed rotations are scanned once, live heads only for appended bytes), the
epoch class of every row against the declared clean epoch (``data_epoch.json``), a schema fingerprint per
(version, record kind) with populated-ness, drift against the announced schema registry, and fields that are
dead within a version.

Incompatible rows are segregated into a labelled partition: a byte-range manifest per file under
``<self-aware home>/compat/segregated/<label>/`` (no copy, so it costs no disk). The analyzer epoch guard
(``analyzer_epoch_guard.py``) reads the same epoch manifest so those rows never enter a current-cohort
result; deletion stays a manual ``clean-epoch-wipe`` run by Danish.

Severity: AMBER when mixed versions would enter one analysis or a schema appears unannounced; RED once a
clean epoch is declared and a pre-epoch / foreign-epoch row sits in an analyzer-visible file, unless the
current-epoch analyzer generation proves (read monitor, :func:`reconcile_retained`) that the stream's
pre-epoch rows stayed retained on disk and never entered a result.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from .facts import iso, read_json

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "services" / "btc-conservative-agent"
SCHEMA = "self_aware_data_compat_v1"
BYTES_PER_RUN = int(float(os.environ.get("SELF_AWARE_COMPAT_BYTES_PER_RUN", 1.5e9)))
SAMPLE_TAIL_BYTES = 600_000
SAMPLE_HEAD_BYTES = 200_000
SAMPLE_MAX_ROWS = 600
MAX_RUNS_PER_FILE = 4000
SKIP_DIRS = ("v3/receipts", "v3/market_segments", "v3/lifecycle_bundle_index", "research-timing-declarations",
             "corrupt_evidence_quarantine")
SKIP_SUFFIXES = (".validation.json", ".malformed_rows.jsonl", ".tmp", ".partial")
STREAM_RE = re.compile(r"^(?P<base>.+\.(?:jsonl|csv))(?:\.(?P<n>\d+))?$")
WIPE_CMD = ("python services\\btc-conservative-agent\\clean_epoch_wipe.py plan --scope laptop --epoch {epoch}  "
            "# review, then: execute --epoch {epoch} --confirm DELETE-PRE-EPOCH:{epoch}:<cert8> --expect-plan-sha256 <sha>")
REGISTRY_FILE = Path(__file__).with_name("schema_registry.json")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


de = _load_module("_sa_data_epoch", SERVICE / "data_epoch.py")
custody = _load_module("_sa_segment_custody", SERVICE / "segment_custody.py")


def retired_custody(mirror: Path, puller: Path | None = None) -> set[str]:
    """Shadow-tree files Fly retired (e.g. the clean-epoch reset) that the laptop keeps only as custody copies.

    They are not current Fly data, never reach the promotion view or the analyzer, and so are not scanned
    as analyzer-visible input (#420). The puller state defaults to ``<shadow>/.puller`` next to the tree.
    """
    meta = Path(puller) if puller is not None else Path(mirror).parent / ".puller"
    return set(custody.load_retired_custody_paths(meta, Path(mirror)))


def current_release() -> str | None:
    try:
        text = (SERVICE / "combo_pathway_config.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'^RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def analyzer_cycle_running(paths) -> bool:
    doc = read_json(paths.chain / "segment-analyzer-cycle.status.json") or {}
    return bool(doc) and not doc.get("finishedAt") and doc.get("phase") not in (None, "DONE")


# ------------------------------------------------------------------ discovery

def discover(mirror: Path, exclude: set[str] | frozenset = frozenset()) -> dict[str, list[tuple[str, int, int]]]:
    """stream base -> [(relpath, size, mtime_ns)] oldest rotation first, live head last."""
    streams: dict[str, list[tuple[int, str, int, int]]] = {}
    for directory, dirnames, filenames in os.walk(mirror):
        rel_dir = os.path.relpath(directory, mirror).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in dirnames if not f"{rel_dir}/{d}".lstrip("/").startswith(SKIP_DIRS)]
        for name in filenames:
            rel = f"{rel_dir}/{name}".lstrip("/")
            if name.endswith(SKIP_SUFFIXES) or rel in exclude:
                continue
            m = STREAM_RE.match(rel)
            if not m:
                continue
            try:
                st = os.stat(os.path.join(directory, name))
            except OSError:
                continue
            n = int(m.group("n")) if m.group("n") else 10 ** 9
            streams.setdefault(m.group("base"), []).append((n, rel, st.st_size, st.st_mtime_ns))
    return {b: [(r, s, t) for _, r, s, t in sorted(v)] for b, v in sorted(streams.items())}


# ------------------------------------------------------------------ incremental row index

def _csv_columns(path: Path) -> tuple[list[str], int]:
    with open(path, "rb") as fh:
        header = fh.readline()
    cols = next(csv.reader([header.decode("utf-8", "replace")]), [])
    return [c.strip() for c in cols], len(header)


def _csv_line_version_ts(cols: list[str], line: bytes) -> tuple[str, str | None, float | None]:
    try:
        vals = next(csv.reader([line.decode("utf-8", "replace")]))
    except (csv.Error, StopIteration):
        return "UNVERSIONED", None, None
    row = dict(zip(cols, vals))
    version = "UNVERSIONED"
    for key in de.VERSION_KEYS:
        v = (row.get(key) or "").strip()
        if v and (key != "policy_version" or de._RELEASE_VALUE.match(v)):
            version = f"{key}={v}"
            break
    ts = None
    for key in de.TS_KEYS:
        ts = de.parse_ts(row.get(key))
        if ts is not None:
            break
    stamp = (row.get(de.STAMP_FIELD) or "").strip() or None
    return version, stamp, ts


def scan_file(mirror: Path, rel: str, entry: dict, manifest: dict | None, budget: int) -> int:
    """Advance ``entry`` over appended bytes of one file; returns bytes consumed."""
    path = mirror / rel
    is_csv = rel.split(".")[-2:-1] == ["csv"] or rel.endswith(".csv")
    cols, header_len = _csv_columns(path) if is_csv else ([], 0)
    start = max(int(entry.get("offset") or 0), header_len)
    consumed = 0
    versions, classes = entry.setdefault("versions", {}), entry.setdefault("classes", {})
    label_bytes, runs = entry.setdefault("label_bytes", {}), entry.setdefault("runs", [])
    label_rows = entry.setdefault("label_rows", {})
    with open(path, "rb") as fh:
        fh.seek(start)
        pos = start
        for line in fh:
            if not line.endswith(b"\n"):
                break  # incomplete tail: next run
            n = len(line)
            pos_end = pos + n
            body = line.strip()
            if body:
                if is_csv:
                    version, stamp, ts = _csv_line_version_ts(cols, body)
                else:
                    if not body.startswith(b"{"):
                        entry["bad"] = int(entry.get("bad") or 0) + 1
                        pos, consumed = pos_end, consumed + n
                        continue
                    version = de.line_version(body)
                    stamp = version.split("=", 1)[1] if version.startswith(de.STAMP_FIELD + "=") else None
                    ts = de.line_ts(body)
                cls = de.classify(rel, stamp_value=stamp, ts=ts, manifest=manifest)
                versions[version] = versions.get(version, 0) + 1
                classes[cls] = classes.get(cls, 0) + 1
                label = f"{cls}|{version}"
                label_bytes[label] = label_bytes.get(label, 0) + n
                label_rows[label] = label_rows.get(label, 0) + 1
                if runs and runs[-1][2] == label and runs[-1][1] == pos:
                    runs[-1][1] = pos_end
                elif len(runs) < MAX_RUNS_PER_FILE:
                    runs.append([pos, pos_end, label])
                else:
                    entry["runs_truncated"] = True
                entry["rows"] = int(entry.get("rows") or 0) + 1
                if ts:
                    entry["first_ts"] = min(entry.get("first_ts") or ts, ts)
                    entry["last_ts"] = max(entry.get("last_ts") or ts, ts)
            pos, consumed = pos_end, consumed + n
            if consumed >= budget:
                break
    entry["offset"] = pos
    return consumed


def update_index(mirror: Path, index: dict, manifest: dict | None, budget: int = BYTES_PER_RUN,
                 puller: Path | None = None) -> dict:
    epoch_key = (manifest or {}).get("epoch_id") or "NO_EPOCH"
    if index.get("epoch_key") != epoch_key:
        index.clear()
        index["epoch_key"] = epoch_key
    files = index.setdefault("files", {})
    retired = retired_custody(mirror, puller)
    index["retired_custody_files"] = len(retired)
    streams = discover(mirror, retired)
    seen = set()
    remaining = budget
    # Live heads first (small appends keep the current picture fresh), then rotations oldest first.
    order = [(b, f) for b, fl in streams.items() for f in fl[-1:]] + [(b, f) for b, fl in streams.items() for f in fl[:-1]]
    for base, (rel, size, mtime_ns) in order:
        seen.add(rel)
        e = files.get(rel)
        if e is None or size < int(e.get("offset") or 0):
            # new file, or truncated/replaced: rescan from the start
            e = files[rel] = {"stream": base}
        e.update(size=size, mtime_ns=mtime_ns, stream=base)
        if remaining > 0 and int(e.get("offset") or 0) < size:
            try:
                remaining -= scan_file(mirror, rel, e, manifest, remaining)
            except OSError as exc:
                e["error"] = f"{type(exc).__name__}: {exc}"[:200]
    for rel in [r for r in files if r not in seen]:
        files.pop(rel)
    index["streams"] = {b: [f[0] for f in fl] for b, fl in streams.items()}
    index["updated_at"] = time.time()
    return index


# ------------------------------------------------------------------ schema sampling

def _sample_rows(path: Path, is_csv: bool, tail: bool) -> list[dict]:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if is_csv:
                header = fh.readline().decode("utf-8", "replace")
            budget = SAMPLE_TAIL_BYTES if tail else SAMPLE_HEAD_BYTES
            if tail and size > budget:
                fh.seek(size - budget)
                fh.readline()
            blob = fh.read(budget)
    except OSError:
        return []
    lines = blob.split(b"\n")[:-1]
    rows: list[dict] = []
    if is_csv:
        reader = csv.DictReader(io.StringIO(header + "\n".join(x.decode("utf-8", "replace") for x in lines)))
        try:
            rows = [dict(r) for r in reader]
        except csv.Error:
            rows = []
    else:
        for line in lines:
            line = line.strip()
            if line.startswith(b"{"):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return rows[-SAMPLE_MAX_ROWS:]


def _row_version(row: dict) -> str:
    for key in de.VERSION_KEYS:
        v = row.get(key)
        if isinstance(v, str) and v and (key != "policy_version" or de._RELEASE_VALUE.match(v)):
            return f"{key}={v}"
    return "UNVERSIONED"


def sample_schema(mirror: Path, rels: list[str]) -> dict[str, dict[str, list[dict]]]:
    """version -> kind -> rows, from the live head tail and the oldest file's head."""
    is_csv = rels[-1].endswith(".csv")
    rows = _sample_rows(mirror / rels[-1], is_csv, tail=True)
    if len(rels) > 1:
        rows += _sample_rows(mirror / rels[0], is_csv, tail=False)
    out: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        out.setdefault(_row_version(row), {}).setdefault(de.record_kind(row), []).append(row)
    return out


def load_registry(paths) -> dict:
    reg = {}
    for p in (REGISTRY_FILE, paths.home / "schema_announcements.json"):
        doc = read_json(p) or {}
        for stream, kinds in (doc.get("streams") or {}).items():
            for kind, fields in kinds.items():
                reg.setdefault(stream, {}).setdefault(kind, {}).update(fields)
    return reg


# ------------------------------------------------------------------ document

def _segregation_label(cls: str, version: str, manifest: dict | None, current: str | None) -> str | None:
    """Partition label for rows that must not enter a current-cohort analysis, else None."""
    if manifest:
        return None if cls in de.COMPATIBLE_CLASSES else cls
    if cls == de.INDEPENDENT or version == "UNVERSIONED" or current is None or version == current:
        return None
    return "LEGACY_VERSION"


def build(paths, index: dict, manifest: dict | None, registry: dict, now: float) -> dict:
    files = index.get("files") or {}
    release = current_release()
    streams_out = []
    seg_total: dict[str, dict[str, int]] = {}
    for base, rels in sorted((index.get("streams") or {}).items()):
        entries = [files[r] for r in rels if r in files]
        total_bytes = sum(int(e.get("size") or 0) for e in entries)
        scanned = sum(int(e.get("offset") or 0) for e in entries)
        versions: dict[str, int] = {}
        classes: dict[str, int] = {}
        label_bytes: dict[str, int] = {}
        label_rows: dict[str, int] = {}
        for e in entries:
            for src, dst in ((e.get("versions"), versions), (e.get("classes"), classes),
                             (e.get("label_bytes"), label_bytes), (e.get("label_rows"), label_rows)):
                for k, v in (src or {}).items():
                    dst[k] = dst.get(k, 0) + v
        head = files.get(rels[-1]) or {}
        newest = head["runs"][-1][2].split("|", 1)[1] if head.get("runs") else None
        if manifest:
            current = f"{de.STAMP_FIELD}={manifest['epoch_id']}"
        elif release and f"bot_version={release}" in versions:
            current = f"bot_version={release}"
        else:
            current = newest
        segregated: dict[str, dict[str, int]] = {}
        for label, nbytes in label_bytes.items():
            cls, version = label.split("|", 1)
            seg = _segregation_label(cls, version, manifest, current)
            if seg:
                s = segregated.setdefault(seg, {"rows": 0, "bytes": 0})
                s["bytes"] += nbytes
                s["rows"] += label_rows.get(label, 0)
        for seg, s in segregated.items():
            t = seg_total.setdefault(seg, {"rows": 0, "bytes": 0, "streams": 0})
            t["rows"] += s["rows"]
            t["bytes"] += s["bytes"]
            t["streams"] += 1
        whole_files = [r for r in rels if r in files and set(
            (files[r].get("label_bytes") or {})) and all(
            _segregation_label(*lab.split("|", 1), manifest, current) for lab in files[r]["label_bytes"])
            and int(files[r].get("offset") or 0) >= int(files[r].get("size") or 0) and r != rels[-1]]
        # schema fingerprint and drift for the current version (sampled)
        sample = sample_schema(paths.mirror, rels) if entries else {}
        cur_kinds = sample.get(current or "", {}) or (sample.get(newest or "", {}) if newest else {})
        announced = registry.get(base)
        drift_new, drift_types, unannounced_kinds = [], [], []
        fp_out = {}
        for kind, rows in sorted(cur_kinds.items()):
            fp = de.fingerprint(rows)
            ann = (announced or {}).get(kind)
            drift = de.schema_drift(ann, fp)
            if drift["status"] == "UNANNOUNCED":
                unannounced_kinds.append(kind)
            drift_new += [f"{kind}:{f}" for f in drift["new_fields"]] if drift["status"] == "DRIFT" else []
            drift_types += [{**t, "kind": kind} for t in drift["type_changes"]]
            fp_out[kind] = {"fingerprint": fp["fingerprint"], "rows_sampled": fp["rows_sampled"],
                            "fields": len(fp["fields"])}
        dead_by_version = {}
        for version, kinds in sample.items():
            dead = []
            for kind, rows in kinds.items():
                dead += [f"{d['field']}={d['status']}" + ("" if kind == "*" else f" [{kind}]")
                         for d in de.dead_fields(de.fingerprint(rows))]
            if dead:
                dead_by_version[version] = sorted(set(dead))[:40]
        real_versions = [v for v in versions if v != "UNVERSIONED"]
        independent = de.epoch_independent(base)
        # Fixed-header CSVs cannot carry data_epoch_id; under a declared epoch their post-epoch rows are dated by
        # the epoch itself, so the stream is declared once no post-epoch row is merely timestamp-inferred.
        epoch_declared = bool(manifest and de.unstampable(base) and classes.get(de.CURRENT_UNSTAMPED)
                              and not classes.get(de.UNSTAMPED_POST_EPOCH))
        # Under a declared epoch a stream that has written no row since the epoch start has nothing to declare
        # yet (its older rows are classified PRE_EPOCH and handled by the purity/retention rules); it is judged
        # on its first post-epoch row.
        quiet_this_epoch = bool(manifest and not any(classes.get(c) for c in (
            de.CURRENT, de.CURRENT_UNSTAMPED, de.UNSTAMPED_POST_EPOCH, de.UNDATED)))
        problems = []
        sev = "GREEN"
        non_evidence, guarded = de.non_evidence(base), de.read_guarded(base)
        if manifest and non_evidence:
            # Ops ledger (relay quarantine/retirement, runtime telemetry, receipts): spans epochs by design and
            # is read by no analyzer path (#420); its classes stay visible but never raise severity.
            problems.append("ops stream, not analyzer evidence (data_epoch.NON_EVIDENCE_BASES)")
        elif manifest:
            bad = {c: n for c, n in classes.items() if c in (de.PRE_EPOCH, de.FOREIGN)}
            if bad and guarded:
                sev = "AMBER"
                problems.append("pre-epoch/foreign rows on disk, rejected by every analyzer reader "
                                "(data_epoch.READ_GUARDED_BASES; wiped by clean-epoch-wipe): " +
                                ", ".join(f"{c} {n}" for c, n in bad.items()))
            elif bad:
                sev = "RED"
                problems.append("pre-epoch/foreign rows in an analyzer-visible file: " +
                                ", ".join(f"{c} {n}" for c, n in bad.items()))
        if manifest and not non_evidence:
            if classes.get(de.UNSTAMPED_POST_EPOCH):
                sev = "AMBER" if sev == "GREEN" else sev
                problems.append(f"{classes[de.UNSTAMPED_POST_EPOCH]} post-epoch rows without data_epoch_id")
            if classes.get(de.UNDATED):
                sev = "AMBER" if sev == "GREEN" else sev
                problems.append(f"{classes[de.UNDATED]} undated rows (epoch unknown)")
        elif not manifest and len(real_versions) > 1 and not independent:
            sev = "AMBER"
            problems.append(f"{len(real_versions)} data versions mixed in one input")
        if unannounced_kinds or drift_new or drift_types:
            sev = "AMBER" if sev == "GREEN" else sev
            problems.append("schema " + ("UNANNOUNCED " + ",".join(unannounced_kinds[:3]) if unannounced_kinds else "DRIFT")
                            + (f" new fields {drift_new[:5]}" if drift_new else "")
                            + (f" type changes {[t['field'] for t in drift_types[:5]]}" if drift_types else ""))
        streams_out.append({
            "stream": base, "files": len(rels), "bytes": total_bytes,
            "scanned_pct": round(100.0 * scanned / total_bytes, 1) if total_bytes else 100.0,
            "rows": sum(versions.values()), "versions": dict(sorted(versions.items(), key=lambda kv: -kv[1])),
            "version_declared": bool(real_versions) or epoch_declared or quiet_this_epoch,
            "quiet_this_epoch": quiet_this_epoch, "epoch_independent": independent,
            "non_evidence": non_evidence, "read_guarded": guarded,
            "current_version": current, "classes": classes, "segregated": segregated,
            "segregated_bytes": sum(s["bytes"] for s in segregated.values()),
            "whole_files_incompatible": whole_files,
            "schema": {"fingerprints": fp_out, "unannounced_kinds": unannounced_kinds, "new_fields": drift_new[:40],
                       "type_changes": drift_types[:20]},
            "dead_in_version": dead_by_version, "severity": sev, "problems": problems,
            "first_ts": iso(min((e["first_ts"] for e in entries if e.get("first_ts")), default=0)) if any(
                e.get("first_ts") for e in entries) else None,
            "last_ts": iso(max((e["last_ts"] for e in entries if e.get("last_ts")), default=0)) if any(
                e.get("last_ts") for e in entries) else None,
        })
    total = sum(s["bytes"] for s in streams_out)
    scanned = sum(int(e.get("offset") or 0) for e in files.values())
    counts = {s: sum(1 for x in streams_out if x["severity"] == s) for s in ("RED", "AMBER", "GREEN")}
    epoch = manifest["epoch_id"] if manifest else None
    return {
        "schema": SCHEMA, "generated_at": iso(now), "mirror": str(paths.mirror),
        "epoch": {"declared": bool(manifest), "epoch_id": epoch,
                  "started_at_utc": (manifest or {}).get("started_at_utc"), "manifest": str(paths.mirror / de.MANIFEST_NAME),
                  "release": release,
                  "note": None if manifest else "no clean epoch declared yet: every row is LEGACY; versions are compared "
                                                "with the registry release (" + str(release) + ")"},
        "coverage": {"bytes_total": total, "bytes_scanned": scanned,
                     "scanned_pct": round(100.0 * scanned / total, 1) if total else 100.0,
                     "complete": scanned >= total},
        "counts": counts, "streams": streams_out,
        "segregated": {"label_root": str(paths.home / "compat" / "segregated"), "partitions": seg_total,
                       "bytes": sum(t["bytes"] for t in seg_total.values()),
                       "note": "byte-range manifests (no copies); excluded from current-cohort analysis by "
                               "analyzer_epoch_guard.py; delete with clean-epoch-wipe (manual)",
                       "delete_command": WIPE_CMD.format(epoch=epoch or "<epoch-id>")},
        "undeclared_streams": [s["stream"] for s in streams_out if not s["version_declared"] and not s["epoch_independent"]
                               and not s["non_evidence"]],
        "retired_custody_files": int(index.get("retired_custody_files") or 0),
    }


def write_partitions(paths, index: dict, doc: dict, manifest: dict | None) -> int:
    """Byte-range manifests of every segregated run, one JSON per (label, stream)."""
    root = paths.home / "compat" / "segregated"
    root.mkdir(parents=True, exist_ok=True)
    current_by_stream = {s["stream"]: s["current_version"] for s in doc["streams"]}
    written = 0
    keep = set()
    for rel, e in (index.get("files") or {}).items():
        current = current_by_stream.get(e.get("stream"))
        for start, end, label in e.get("runs") or []:
            cls, version = label.split("|", 1)
            seg = _segregation_label(cls, version, manifest, current)
            if not seg:
                continue
            out = root / seg / (e["stream"].replace("/", "__") + ".json")
            keep.add(out)
    for target in keep:
        seg, stream = target.parent.name, target.stem.replace("__", "/")
        ranges = []
        for rel, e in (index.get("files") or {}).items():
            if e.get("stream") != stream:
                continue
            current = current_by_stream.get(stream)
            for start, end, label in e.get("runs") or []:
                cls, version = label.split("|", 1)
                if _segregation_label(cls, version, manifest, current) == seg:
                    ranges.append({"file": rel, "start": start, "end": end, "version": version, "class": cls})
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": "self_aware_segregated_partition_v1", "label": seg, "stream": stream,
                                   "epoch_id": (manifest or {}).get("epoch_id"), "generated_at": doc["generated_at"],
                                   "bytes": sum(r["end"] - r["start"] for r in ranges), "ranges": ranges},
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, target)
        written += 1
    for old in root.glob("*/*.json"):
        if old not in keep:
            try:
                old.unlink()
            except OSError:
                pass
    return written


def run(paths, state: dict, now: float | None = None, budget: int = BYTES_PER_RUN) -> dict:
    now = now or time.time()
    started = time.time()
    manifest = de.load_manifest(paths.mirror / de.MANIFEST_NAME)
    index_path = paths.home / "compat" / "index.json"
    index = read_json(index_path) or {}
    deferred = analyzer_cycle_running(paths) and bool(index.get("files"))
    update_index(paths.mirror, index, manifest, budget=0 if deferred else budget, puller=paths.puller)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, index_path)
    doc = build(paths, index, manifest, load_registry(paths), now)
    doc["partitions_written"] = write_partitions(paths, index, doc, manifest)
    doc["scan_deferred_for_analyzer_cycle"] = deferred
    doc["ms"] = int((time.time() - started) * 1000)
    return doc


def epoch_purity(paths, doc: dict) -> dict:
    """Does the latest analyzer generation prove it admitted only clean-epoch rows?"""
    epoch = doc["epoch"]
    if not epoch["declared"]:
        return {"severity": "SKIP", "observed": "no clean epoch declared"}
    receipt_path = (paths.analyzer_repo / "services" / "btc-conservative-agent" / "canonical-research-data" /
                    "analyzer" / "analyzer_generation_receipt.json")
    receipt = read_json(receipt_path) or {}
    block = receipt.get("data_epoch") or {}
    base = {"receipt": str(receipt_path), "generation_id": receipt.get("generation_id"), "data_epoch": block}
    if not block:
        return {**base, "severity": "RED", "observed": "analyzer generation receipt has no data_epoch block (epoch guard "
                                                       "not wired) - pre-epoch rows may enter results"}
    if block.get("epoch_id") != epoch["epoch_id"]:
        return {**base, "severity": "RED",
                "observed": f"analyzer read epoch {block.get('epoch_id')} but the declared epoch is {epoch['epoch_id']}"}
    if block.get("pre_epoch_rows_admitted") is None:
        return {**base, "severity": "RED",
                "observed": "analyzer generation could not account for pre-epoch rows: " + str(block.get("error"))}
    admitted = int(block.get("pre_epoch_rows_admitted") or 0)
    if admitted:
        by_stream = block.get("pre_epoch_rows_admitted_by_stream") or {}
        sites = {k: (v or {}).get("sites", [])[:1] for k, v in
                 ((block.get("read_monitor") or {}).get("unguarded_stream_reads") or {}).items() if k in by_stream}
        return {**base, "severity": "RED",
                "observed": f"{admitted} pre-epoch rows entered analyzer results: " +
                            "; ".join(f"{k} {n} via {sites.get(k) or 'unmonitored read'}"
                                      for k, n in sorted(by_stream.items(), key=lambda kv: -kv[1])[:6])}
    retained = int(block.get("pre_epoch_rows_retained") or 0)
    return {**base, "severity": "GREEN",
            "observed": f"generation {receipt.get('generation_id')} read epoch {block.get('epoch_id')} only; "
                        f"{int(block.get('pre_epoch_rows_rejected') or 0)} pre-epoch rows rejected at load"
                        + (f"; {retained} retained on disk, never opened unguarded" if retained else "")}


def reconcile_retained(doc: dict, purity: dict | None) -> dict:
    """Pre-epoch rows the analyzer provably never admitted are retained, not mixed.

    Retained files are kept on purpose. A stream stops being RED only when the
    current-epoch generation receipt has an active read monitor, reports purity
    GREEN and does not name the stream among admitted pre-epoch rows; anything
    less keeps the file-level RED.
    """
    epoch = doc.get("epoch") or {}
    block = (purity or {}).get("data_epoch") or {}
    monitor = block.get("read_monitor") or {}
    proven = bool(epoch.get("declared") and (purity or {}).get("severity") == "GREEN" and monitor.get("active")
                  and block.get("epoch_id") == epoch.get("epoch_id"))
    admitted = block.get("pre_epoch_rows_admitted_by_stream") or {}
    total_rows = streams = 0
    for s in doc.get("streams") or []:
        bad = {c: n for c, n in (s.get("classes") or {}).items() if c in (de.PRE_EPOCH, de.FOREIGN)}
        if not bad or not proven or s["stream"] in admitted:
            continue
        rows = sum(bad.values())
        s["retained_pre_epoch_rows"] = rows
        s["problems"] = [p for p in s["problems"] if not p.startswith("pre-epoch/foreign rows")]
        s["severity"] = "AMBER" if s["problems"] else "GREEN"
        s["problems"].append(f"{rows} pre-epoch/foreign rows retained on disk; excluded at load "
                             f"(generation {purity.get('generation_id')})")
        total_rows += rows
        streams += 1
    doc["counts"] = {sev: sum(1 for x in doc.get("streams") or [] if x["severity"] == sev)
                     for sev in ("RED", "AMBER", "GREEN")}
    doc["retained"] = {"proven": proven, "generation_id": (purity or {}).get("generation_id"),
                       "streams": streams, "rows": total_rows}
    return doc


# ------------------------------------------------------------------ findings

def findings(doc: dict | None, now: float, max_age_sec: float, epoch_purity: dict | None = None) -> list[dict]:
    ids = ("data.compat_mixed", "data.compat_schema", "data.compat_declared", "data.compat_epoch_purity")
    from .facts import parse_ts  # noqa: PLC0415
    gen = parse_ts((doc or {}).get("generated_at"))
    if not doc or not gen or now - gen > max_age_sec:
        return [{"id": i, "severity": "SKIP", "observed": "compatibility scan has not run in the last 2 h",
                 "expected": "compat job every 30 min", "emit_alarm": False} for i in ids]
    streams = doc["streams"]
    epoch = doc["epoch"]
    cov = doc["coverage"]
    cov_note = "" if cov["complete"] else f" (index {cov['scanned_pct']}% built; counts partial)"
    out = []
    red = [s for s in streams if s["severity"] == "RED"]
    mixed = [s for s in streams if any("versions mixed" in p for p in s["problems"])]
    if epoch["declared"]:
        sev = "RED" if red else "GREEN"
        retained = doc.get("retained") or {}
        obs = (f"{len(red)} streams hold pre-epoch/foreign rows in analyzer-visible files: " +
               "; ".join(f"{s['stream']} {s['classes']}" for s in red[:5])) if red else \
            f"every analysis input is clean-epoch {epoch['epoch_id']} only" + (
                f"; {retained['streams']} streams retain {retained['rows']} pre-epoch rows on disk, excluded at "
                f"load (generation {retained.get('generation_id')})" if retained.get("streams") else "")
    else:
        sev = "AMBER" if mixed else "GREEN"
        seg = doc["segregated"]
        obs = (f"no clean epoch declared; {len(mixed)} streams mix data versions in one input, "
               f"{seg['bytes'] / 1e6:,.1f} MB segregated as LEGACY_VERSION: " +
               "; ".join(f"{s['stream']} ({len([v for v in s['versions'] if v != 'UNVERSIONED'])} versions)"
                         for s in sorted(mixed, key=lambda x: -x["segregated_bytes"])[:6])) if mixed else \
            "no stream mixes data versions"
    out.append({"id": "data.compat_mixed", "severity": sev, "observed": obs + cov_note,
                "expected": "one data version / clean epoch per analysis input; pre-epoch rows segregated and wiped",
                "evidence": {"segregated": doc["segregated"], "epoch": epoch}})
    schema_bad = [s for s in streams if s["schema"]["unannounced_kinds"] or s["schema"]["new_fields"]
                  or s["schema"]["type_changes"]]
    out.append({"id": "data.compat_schema", "severity": "AMBER" if schema_bad else "GREEN",
                "observed": ("; ".join(f"{s['stream']}: " + "; ".join(p for p in s["problems"] if p.startswith("schema"))
                                       for s in schema_bad[:6]) if schema_bad else
                             f"{len(streams)} streams match the announced schema registry"),
                "expected": "every new field / type / record kind announced in scripts/self_aware/schema_registry.json "
                            "(or via --announce) before it ships"})
    undeclared = doc.get("undeclared_streams") or []
    out.append({"id": "data.compat_declared", "severity": "AMBER" if undeclared else "GREEN",
                "observed": (f"{len(undeclared)} streams declare no data_version/epoch on their rows: "
                             + ", ".join(undeclared[:10])) if undeclared else "every stream declares its data version",
                "expected": "every row carries data_epoch_id (stamped by the collector from the clean epoch on)"})
    ep = epoch_purity or {}
    if not epoch["declared"]:
        out.append({"id": "data.compat_epoch_purity", "severity": "SKIP", "emit_alarm": False,
                    "observed": "no clean epoch declared; analyzer epoch guard idle",
                    "expected": "analyzer generation proves it read only the clean epoch"})
    else:
        out.append({"id": "data.compat_epoch_purity", "severity": ep.get("severity") or "RED",
                    "observed": ep.get("observed") or "analyzer generation does not report its data epoch",
                    "expected": f"analyzer_generation_receipt.json data_epoch.epoch_id == {epoch['epoch_id']} and "
                                "pre_epoch_rows_admitted == 0", "evidence": ep})
    return out


def main(argv=None) -> int:
    import argparse  # noqa: PLC0415
    from .config import Paths  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="data compatibility scan / schema announcements")
    ap.add_argument("--once", action="store_true", help="run one scan and print the summary")
    ap.add_argument("--budget-gb", type=float, default=BYTES_PER_RUN / 1e9)
    ap.add_argument("--seed-registry", action="store_true", help="write the current schemas as announced (repo file)")
    ap.add_argument("--announce", metavar="STREAM", help="announce the current schema of one stream (local overlay)")
    args = ap.parse_args(argv)
    paths = Paths()
    if args.seed_registry or args.announce:
        index = read_json(paths.home / "compat" / "index.json") or {}
        streams = discover(paths.mirror)
        target = REGISTRY_FILE if args.seed_registry else paths.home / "schema_announcements.json"
        doc = read_json(target) or {"schema": "self_aware_schema_registry_v1", "streams": {}}
        release = current_release()
        for base, fl in streams.items():
            if args.announce and base != args.announce:
                continue
            sample = sample_schema(paths.mirror, [f[0] for f in fl])
            kinds = {}
            for version, by_kind in sample.items():
                for kind, rows in by_kind.items():
                    fp = de.fingerprint(rows)
                    merged = kinds.setdefault(kind, {})
                    for name, f in fp["fields"].items():
                        merged[name] = sorted(set(merged.get(name, [])) | set(f["types"]))
            doc["streams"][base] = kinds
        doc["announced_at"] = iso(time.time())
        doc["release"] = release
        target.write_text(json.dumps(doc, indent=0, sort_keys=True), encoding="utf-8")
        print(f"announced {len(doc['streams'])} streams -> {target} (index files {len(index.get('files') or {})})")
        return 0
    doc = run(paths, {}, budget=int(args.budget_gb * 1e9))
    print(json.dumps({k: doc[k] for k in ("generated_at", "epoch", "coverage", "counts", "segregated",
                                          "undeclared_streams", "ms")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
