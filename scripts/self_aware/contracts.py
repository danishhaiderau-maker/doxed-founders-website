"""Section contracts: declarative CONTENT checks for every dashboard section.

Liveness and freshness checks stay GREEN while a section silently loses its
dimensions, rows or meaning (the Top-100 policy combos collapsed to
ADX x gap x DIRECT x lane with every check green). A contract states what a
section must contain, so collapse, dead fields, label/data contradictions,
roster drift and reconciliation mismatches are findings, not surprises.

One spec per section lives in ``section_contracts.json``. Each evaluation
stores its metrics and table dimensions as history; drift is judged against
that history (collapse of N, dimensions dropped). A generic report-shape drift
pass covers every analyzer report archived by generation, so sections without
a hand-written contract are still watched.

Severity: RED = silently wrong/empty/collapsed/unreachable, AMBER = honest but
degraded (declared empty, dead column, stale, roster drift), INFO = noted only.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .facts import iso, parse_ts, read_json

REGISTRY_FILE = Path(__file__).with_name("section_contracts.json")
SCHEMA = "self_aware_contracts_v1"
GREEN, AMBER, RED, SKIP, INFO = "GREEN", "AMBER", "RED", "SKIP", "INFO"
_RANK = {RED: 3, AMBER: 2, SKIP: 1, INFO: 0, GREEN: 0}
MISSING = object()
SOURCE_KINDS = ("http", "fly", "file", "export_table", "self")
DEFAULT_MAX_BYTES = 20_000_000
FLY_BASE = "https://doxed-btc-bot.fly.dev"
FLY_CACHE_TTL_SEC = 3600
FLY_MAX_CALLS_PER_RUN = 6
HISTORY_TABLE = "contract_history"

VIOLATION_HELP = {
    "UNREACHABLE": "endpoint/file could not be fetched", "HTTP_STATUS": "non-200 status (declared ones are AMBER)",
    "NOT_JSON": "body is not JSON", "OVERSIZE": "payload above the contract's max_bytes; dashboard too heavy",
    "STALE": "freshness timestamp older than max_age (RED beyond 3x)", "NO_TIMESTAMP": "freshness field absent",
    "MISSING_FIELD": "required field absent", "NULL_FIELD": "required field null", "DEAD_FIELD": "live field null/empty/zero",
    "MISSING_TABLE": "declared table path absent", "NOT_A_TABLE": "table path is not a list of rows",
    "EMPTY_DECLARED": "fewer rows than min_rows, with a declared status/blocker explaining why",
    "EMPTY_SILENT": "fewer rows than min_rows and nothing on the page says why",
    "MISSING_COLUMNS": "expected columns absent", "DEAD_COLUMN": "column null or zero on every row",
    "CONSTANT_COLUMN": "column carries one value on every row", "DIMENSION_COLLAPSE": "a dimension/gene no longer varies",
    "ROSTER_MISMATCH": "lanes differ from the Fly active-tile roster", "RETIRED_LANE_PRESENT": "retired lane in a current cohort",
    "RECONCILE_MISMATCH": "counts/PnL disagree with the canonical ledger cohort", "LABEL_CONTRADICTION": "status says OK but content is empty/dead",
    "DEAD_SECTION": "Fly panel returns 200 with no analyzer data", "DRIFT_COLLAPSE": "metric fell below half its rolling baseline",
    "DRIFT_DIMS_DROPPED": "dimension present in the baseline vanished", "CONTRACT_ERROR": "the evaluator crashed on this contract",
    "FILL_MODEL_UNDECLARED": "headline result does not declare fill_model=REALISTIC_V1",
    "FILL_MODEL_OPTIMISTIC_HEADLINE": "an optimistic (touch/ideal/mid) fill number is shown as headline instead of a labelled shadow",
}
DRIFT_WINDOW = 12
ARCHIVE_MAX_FILE_BYTES = 40_000_000
ARCHIVE_SNAPSHOTS = 8
OK_LABELS = {"OK", "CURRENT", "POPULATED", "PASS", "COMPLETE", "CURRENT_GENERATION", "VALID", "CURRENT_MATCH"}


# ------------------------------------------------------------------ registry

def load_registry(path: Path = REGISTRY_FILE) -> dict[str, Any]:
    raw = path.read_bytes()
    reg = json.loads(raw)
    reg["registry_hash"] = "sha256:" + hashlib.sha256(raw).hexdigest()[:16]
    return validate_registry(reg)


def validate_registry(reg: dict[str, Any]) -> dict[str, Any]:
    ids: set[str] = set()
    for spec in reg.get("contracts") or []:
        cid = spec.get("id")
        if not cid or cid in ids:
            raise ValueError(f"contract id missing or duplicated: {cid!r}")
        ids.add(cid)
        for key in ("surface", "title", "source", "tier", "depends_on"):
            if key not in spec:
                raise ValueError(f"contract {cid} lacks {key}")
        if spec["tier"] not in ("light", "heavy"):
            raise ValueError(f"contract {cid} tier must be light|heavy")
        if (spec["source"] or {}).get("kind") not in SOURCE_KINDS:
            raise ValueError(f"contract {cid} source.kind must be one of {SOURCE_KINDS}")
        if spec.get("reconcile") and spec["reconcile"] not in RECONCILERS:
            raise ValueError(f"contract {cid} names unknown reconciler {spec['reconcile']}")
        for t in spec.get("tables") or []:
            if "path" not in t:
                raise ValueError(f"contract {cid} has a table without path")
    reg.setdefault("registry_hash", "sha256:" + hashlib.sha256(json.dumps(reg, sort_keys=True).encode()).hexdigest()[:16])
    return reg


# ------------------------------------------------------------------ paths

def get_path(obj: Any, path: str | None) -> Any:
    """Dotted lookup; ``#a.b`` returns len(a.b); integer segments index lists; ``$`` is the root."""
    if path in (None, "", "$"):
        return obj
    count = path.startswith("#")
    cur = obj
    for seg in (path[1:] if count else path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(seg, MISSING)
        elif isinstance(cur, list) and seg.lstrip("-").isdigit():
            i = int(seg)
            cur = cur[i] if -len(cur) <= i < len(cur) else MISSING
        else:
            return MISSING
        if cur is MISSING:
            return MISSING
    if count:
        return len(cur) if isinstance(cur, (list, dict)) else MISSING
    return cur


def _blank(v: Any) -> bool:
    return v is MISSING or v is None or v == "" or v == [] or v == {}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is MISSING or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ fetch

def _template(s: str, paths: Paths) -> str:
    return (s.replace("{chain}", str(paths.chain)).replace("{exports}", str(paths.exports))
            .replace("{archive}", str(paths.archive)).replace("{home}", str(paths.home)))


def _http(url: str, max_bytes: int, timeout: float = 60.0) -> tuple[Any, dict[str, Any]]:
    t = time.time()
    meta: dict[str, Any] = {"url": url}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "self-aware-contracts/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            meta["code"] = r.status
            body = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        meta["code"] = e.code
        body = e.read(max_bytes + 1)
    except Exception as e:  # noqa: BLE001
        meta.update(error=f"{type(e).__name__}: {str(e)[:160]}", ms=int((time.time() - t) * 1000))
        return MISSING, meta
    meta["ms"] = int((time.time() - t) * 1000)
    meta["bytes"] = len(body)
    if len(body) > max_bytes:
        meta["oversize"] = True
        return MISSING, meta
    try:
        return json.loads(body), meta
    except ValueError:
        meta["not_json"] = True
        return MISSING, meta


def _export_table(paths: Paths, name: str) -> tuple[Any, dict[str, Any]]:
    import pandas as pd  # noqa: PLC0415
    p = Path(paths.exports) / f"{name}.csv"
    meta: dict[str, Any] = {"url": str(p)}
    try:
        frame = pd.read_csv(p, low_memory=False)
    except FileNotFoundError:
        meta["error"] = "FileNotFoundError"
        return MISSING, meta
    except Exception as e:  # noqa: BLE001
        meta["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return MISSING, meta
    meta["code"] = 200
    meta["mtime"] = iso(p.stat().st_mtime)
    rows = json.loads(frame.to_json(orient="records", date_format="iso"))
    return {"rows": rows, "generated_at": meta["mtime"]}, meta


class Fetcher:
    """Fetches contract sources once per run. Fly is public-only, disk-cached and capped per run."""

    def __init__(self, paths: Paths, docs: dict[str, Any] | None = None, *, fly_enabled: bool | None = None) -> None:
        self.paths = paths
        self.docs = docs or {}
        self.memo: dict[str, tuple[Any, dict[str, Any]]] = {}
        self.fly_calls = 0
        self.fly_enabled = (os.environ.get("SELF_AWARE_CONTRACTS_FLY", "1") != "0") if fly_enabled is None else fly_enabled
        self.cache_dir = Path(paths.home) / "contracts-cache"

    def get(self, source: dict[str, Any], max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[Any, dict[str, Any]]:
        key = json.dumps(source, sort_keys=True)
        if key not in self.memo:
            self.memo[key] = self._get(source, max_bytes)
        return self.memo[key]

    def _get(self, source: dict[str, Any], max_bytes: int) -> tuple[Any, dict[str, Any]]:
        kind = source["kind"]
        if kind == "http":
            return _http(source["url"], max_bytes)
        if kind == "file":
            p = Path(_template(source["path"], self.paths))
            meta: dict[str, Any] = {"url": str(p)}
            if not p.exists():
                meta["error"] = "FileNotFoundError"
                return MISSING, meta
            if p.stat().st_size > max_bytes:
                meta.update(oversize=True, bytes=p.stat().st_size)
                return MISSING, meta
            doc = read_json(p)
            meta.update(code=200, bytes=p.stat().st_size, mtime=iso(p.stat().st_mtime))
            if doc is None:
                meta["not_json"] = True
                return MISSING, meta
            return doc, meta
        if kind == "export_table":
            return _export_table(self.paths, source["name"])
        if kind == "self":
            doc = self.docs.get(source["doc"])
            return (doc, {"url": f"self:{source['doc']}", "code": 200}) if doc else (
                MISSING, {"url": f"self:{source['doc']}", "skip": "self-aware document not computed yet (self.engine owns this)"})
        if kind == "fly":
            return self._fly(source["path"], max_bytes)
        raise ValueError(kind)

    def _fly(self, path: str, max_bytes: int) -> tuple[Any, dict[str, Any]]:
        url = FLY_BASE + path
        cache = self.cache_dir / (hashlib.sha1(path.encode()).hexdigest()[:16] + ".json")
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        fresh = cached and time.time() - float(cached.get("fetched_at", 0)) < FLY_CACHE_TTL_SEC
        if fresh or not self.fly_enabled or self.fly_calls >= FLY_MAX_CALLS_PER_RUN:
            if cached:
                return cached["doc"], {**cached["meta"], "cached_at": iso(cached["fetched_at"])}
            return MISSING, {"url": url, "skip": "Fly fetch disabled or per-run cap reached and no cache"}
        self.fly_calls += 1
        doc, meta = _http(url, max_bytes, timeout=30)
        if meta.get("code") == 429 and cached:
            return cached["doc"], {**cached["meta"], "cached_at": iso(cached["fetched_at"]), "rate_limited": True}
        if doc is not MISSING:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"fetched_at": time.time(), "meta": meta, "doc": doc}), encoding="utf-8")
        return doc, meta


# ------------------------------------------------------------------ profiling

def column_profile(rows: list[Any], cap: int = 5000) -> dict[str, dict[str, Any]]:
    cols: dict[str, dict[str, Any]] = {}
    for r in rows[:cap]:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            c = cols.setdefault(k, {"n": 0, "null": 0, "zero": 0, "values": set()})
            c["n"] += 1
            if _blank(v) or (isinstance(v, float) and v != v):
                c["null"] += 1
                continue
            if not isinstance(v, bool) and isinstance(v, (int, float)) and v == 0:
                c["zero"] += 1
            if len(c["values"]) <= 50:
                c["values"].add(json.dumps(v, sort_keys=True, default=str)[:80])
    out = {}
    for k, c in cols.items():
        status = "OK"
        if c["null"] == c["n"]:
            status = "DEAD_NULL"
        elif c["zero"] + c["null"] == c["n"]:
            status = "DEAD_ZERO"
        elif len(c["values"]) == 1 and c["n"] - c["null"] >= 3:
            status = "CONSTANT"
        out[k] = {"n": c["n"], "null_pct": round(100 * c["null"] / c["n"], 1), "distinct": len(c["values"]),
                  "status": status, "constant": next(iter(c["values"])) if status == "CONSTANT" else None}
    return out


def _v(kind: str, sev: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "severity": sev, "detail": detail[:400], **extra}


# ------------------------------------------------------------------ evaluate

def _declared(obj: Any, paths: list[str]) -> str | None:
    for p in paths or []:
        v = get_path(obj, p)
        if not _blank(v) and v is not False:
            return f"{p}={json.dumps(v, default=str)[:120]}"
    return None


def _status_label(obj: Any, spec: dict[str, Any]) -> str | None:
    p = spec.get("status_path")
    v = get_path(obj, p) if p else MISSING
    return str(v) if isinstance(v, (str, int)) and v is not MISSING else None


def evaluate(spec: dict[str, Any], obj: Any, meta: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    now = ctx["now"]
    viol: list[dict[str, Any]] = []
    metrics: dict[str, float] = {}
    dims: dict[str, list[str]] = {}
    tables_out: list[dict[str, Any]] = []
    res = {"id": spec["id"], "surface": spec["surface"], "title": spec["title"], "tier": spec["tier"],
           "page": spec.get("page"), "api": meta.get("url"), "http": meta.get("code"), "bytes": meta.get("bytes"),
           "ms": meta.get("ms"), "evaluated_at": iso(now), "depends_on": spec["depends_on"]}
    if meta.get("skip"):
        res.update(status=SKIP, violations=[_v("SKIPPED", SKIP, meta["skip"])], metrics={}, dims={}, tables=[])
        return res
    allow = {str(k): v for k, v in (spec.get("allow_http") or {}).items()}
    code = meta.get("code")
    if obj is MISSING:
        if meta.get("oversize"):
            viol.append(_v("OVERSIZE", spec.get("oversize_severity", AMBER),
                           f"payload > {spec.get('max_bytes', DEFAULT_MAX_BYTES):,} bytes "
                           f"({meta.get('bytes'):,}+ read); not parsed - section too heavy for its dashboard"))
        elif str(code) in allow:
            viol.append(_v("HTTP_STATUS", allow[str(code)], f"HTTP {code} (declared acceptable; no content to check)"))
        elif meta.get("not_json"):
            viol.append(_v("NOT_JSON", RED, f"HTTP {code} body is not JSON"))
        else:
            viol.append(_v("UNREACHABLE", RED, f"{meta.get('error') or ('HTTP ' + str(code))}"))
        res.update(status=_worst(viol), violations=viol, metrics=metrics, dims=dims, tables=tables_out)
        return res
    if code not in (None, 200):
        sev = allow.get(str(code), RED)
        viol.append(_v("HTTP_STATUS", sev, f"HTTP {code}" + (" (declared acceptable)" if str(code) in allow else "")))
    if meta.get("cached_at"):
        res["cached_at"] = meta["cached_at"]

    # freshness
    fr = spec.get("freshness") or {}
    if fr:
        ts = parse_ts(get_path(obj, fr.get("path", "generated_at"))) if fr.get("path") != "@mtime" else parse_ts(meta.get("mtime"))
        # A cached Fly document is judged as of its fetch, so the cache TTL never reads as producer staleness.
        ref = parse_ts(meta.get("cached_at")) or now
        age = (ref - ts) if ts else None
        res["age_sec"] = round(age, 1) if age is not None else None
        mx = float(fr.get("max_age_sec", 7200))
        if age is None:
            viol.append(_v("NO_TIMESTAMP", AMBER, f"freshness field {fr.get('path')} missing"))
        elif age > 3 * mx:
            viol.append(_v("STALE", RED, f"{age / 3600:.1f} h old (max {mx / 3600:.1f} h, RED beyond 3x)"))
        elif age > mx:
            viol.append(_v("STALE", AMBER, f"{age / 60:.0f} min old (max {mx / 60:.0f} min)"))

    # required fields
    for p in spec.get("required") or []:
        v = get_path(obj, p)
        if v is MISSING:
            viol.append(_v("MISSING_FIELD", RED, f"{p} absent"))
        elif v is None:
            viol.append(_v("NULL_FIELD", AMBER, f"{p} is null"))
    for p in spec.get("live_fields") or []:
        v = get_path(obj, p)
        if _blank(v) or (_num(v) == 0):
            viol.append(_v("DEAD_FIELD", AMBER, f"{p} = {json.dumps(None if v is MISSING else v, default=str)[:60]}"))

    label = _status_label(obj, spec)
    res["status_label"] = label
    empty_or_dead: list[str] = []

    # tables
    for t in spec.get("tables") or []:
        path = t["path"]
        rows = get_path(obj, path)
        name = t.get("name") or path
        if isinstance(rows, dict) and t.get("dict_rows"):
            rows = [{"_key": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in rows.items()]
        if rows is MISSING:
            viol.append(_v("MISSING_TABLE", RED if t.get("required", True) else AMBER, f"{name} absent"))
            continue
        if not isinstance(rows, list):
            viol.append(_v("NOT_A_TABLE", RED, f"{name} is {type(rows).__name__}, expected a list"))
            continue
        n = len(rows)
        metrics[f"rows:{name}"] = n
        prof = column_profile(rows)
        dims[name] = sorted(prof)
        if t.get("key_field"):
            dims[f"{name}:keys"] = sorted({str(r.get(t["key_field"])) for r in rows if isinstance(r, dict)})[:300]
        tinfo = {"name": name, "path": path, "rows": n, "columns": len(prof),
                 "dead": sorted(k for k, c in prof.items() if c["status"] in ("DEAD_NULL", "DEAD_ZERO")),
                 "constant": sorted(k for k, c in prof.items() if c["status"] == "CONSTANT")}
        tables_out.append(tinfo)
        min_rows = int(t.get("min_rows", 1))
        if n < min_rows:
            why = _declared(obj, t.get("declared_empty_paths") or spec.get("declared_empty_paths") or [])
            if why:
                viol.append(_v("EMPTY_DECLARED", t.get("empty_declared_severity", AMBER),
                               f"{name}: {n} rows < {min_rows}; declared reason {why}"))
            else:
                viol.append(_v("EMPTY_SILENT", t.get("empty_silent_severity", RED),
                               f"{name}: {n} rows < {min_rows} with no declared status/blocker explaining it"))
            empty_or_dead.append(name)
            continue
        missing_cols = [c for c in t.get("columns") or [] if c not in prof]
        if missing_cols:
            viol.append(_v("MISSING_COLUMNS", RED, f"{name}: columns absent {missing_cols}"))
        allow_const = set(t.get("allow_constant") or [])
        for c in t.get("live_columns") or []:
            st = (prof.get(c) or {}).get("status")
            if st in ("DEAD_NULL", "DEAD_ZERO"):
                viol.append(_v("DEAD_COLUMN", t.get("dead_severity", AMBER), f"{name}.{c} is {st} across {n} rows"))
                empty_or_dead.append(f"{name}.{c}")
            elif st == "CONSTANT" and c not in allow_const and n >= int(t.get("constant_min_rows", 5)):
                viol.append(_v("CONSTANT_COLUMN", t.get("constant_severity", AMBER),
                               f"{name}.{c} is constant {prof[c]['constant']} across {n} rows"))
        if t.get("flag_all_dead"):
            for c in tinfo["dead"]:
                if c not in (t.get("live_columns") or []) and c not in allow_const:
                    viol.append(_v("DEAD_COLUMN", INFO, f"{name}.{c} dead ({prof[c]['status']})"))
        for c, k in (t.get("min_distinct") or {}).items():
            d = (prof.get(c) or {}).get("distinct", 0)
            if d < int(k):
                viol.append(_v("DIMENSION_COLLAPSE", t.get("collapse_severity", RED),
                               f"{name}.{c} has {d} distinct value(s) < {k}: the section no longer varies by {c}"))
        genes = t.get("genes") or {}
        if genes:
            cols_l = [c.lower() for c in prof]
            missing = [g for g, toks in genes.items() if not any(tok in c for tok in toks for c in cols_l)]
            metrics[f"genes:{name}"] = len(genes) - len(missing)
            if missing:
                viol.append(_v("DIMENSION_COLLAPSE", t.get("genes_severity", RED),
                               f"{name}: {len(missing)}/{len(genes)} policy-genome dimensions absent {missing}"))
        if t.get("roster_field"):
            _roster(viol, name, {r.get(t["roster_field"]) for r in rows if isinstance(r, dict)}, ctx, t)

    # dict-keyed roster (e.g. {lane: {...}})
    for r in spec.get("rosters") or []:
        v = get_path(obj, r["path"])
        if v is MISSING:
            continue
        lanes = set(v) if isinstance(v, dict) else {x.get(r["field"]) if isinstance(x, dict) else x for x in v}
        _roster(viol, r["path"], lanes, ctx, r)

    # counters + invariants
    for c in spec.get("counters") or []:
        v = _num(get_path(obj, c["path"]))
        if v is not None:
            metrics[c["path"]] = v
        if v is None:
            viol.append(_v("MISSING_COUNTER", c.get("severity", AMBER), f"{c['path']} absent/non-numeric"))
        elif "min" in c and v < c["min"]:
            viol.append(_v(c.get("kind", "COUNTER_LOW"), c.get("severity", AMBER),
                           f"{c['path']} = {v:g} < {c['min']}" + (f" ({c['why']})" if c.get("why") else "")))
    for inv in spec.get("invariants") or []:
        a = _num(get_path(obj, inv["if_positive"]))
        b = _num(get_path(obj, inv["then_min"]))
        if a and a > 0 and (b is None or b < inv.get("min", 1)):
            viol.append(_v(inv.get("kind", "INVARIANT"), inv.get("severity", RED),
                           f"{inv['if_positive']}={a:g} but {inv['then_min']}={b if b is not None else 'absent'}: "
                           f"{inv.get('why', '')}"))
    for eq in spec.get("equalities") or []:
        a, b = get_path(obj, eq["a"]), get_path(obj, eq["b"])
        if a is not MISSING and b is not MISSING and a != b:
            viol.append(_v("MISMATCH", eq.get("severity", AMBER), f"{eq['a']}={a} != {eq['b']}={b} {eq.get('why', '')}"))
    for ex in spec.get("expect") or []:
        v = get_path(obj, ex["path"])
        allowed = ex["in"] if "in" in ex else [ex["equals"]]
        if v is MISSING and not ex.get("required", True):
            continue
        if (v if v is not MISSING else None) not in allowed:
            viol.append(_v(ex.get("kind", "UNEXPECTED_VALUE"), ex.get("severity", AMBER),
                           f"{ex['path']}={json.dumps(None if v is MISSING else v, default=str)[:80]} (expected {allowed})"
                           + (f": {ex['why']}" if ex.get("why") else "")))
    for ra in spec.get("ratios") or []:
        a, b = _num(get_path(obj, ra["num"])), _num(get_path(obj, ra["den"]))
        if a is None or not b:
            continue
        r = a / b
        metrics[f"ratio:{ra['num']}/{ra['den']}"] = round(r, 4)
        if r < float(ra["min"]):
            viol.append(_v(ra.get("kind", "LOW_COVERAGE"), ra.get("severity", AMBER),
                           f"{ra['num']}/{ra['den']} = {a:g}/{b:g} = {r:.1%} < {float(ra['min']):.0%}"
                           + (f": {ra['why']}" if ra.get("why") else "")))
    if spec.get("auto_shape"):
        shp = report_shape(obj)
        for lp, n in shp["lists"].items():
            metrics[f"list:{lp}"] = n
        for lp, cols in shp["columns"].items():
            dims[f"shape:{lp}"] = cols

    # label vs data contradiction
    if label and label.upper() in OK_LABELS and empty_or_dead and spec.get("contradiction_check", True):
        viol.append(_v("LABEL_CONTRADICTION", AMBER,
                       f"status {spec.get('status_path')}={label} but {', '.join(empty_or_dead[:4])} empty/dead"))

    # reconciliation
    if spec.get("reconcile"):
        try:
            rv, rm = RECONCILERS[spec["reconcile"]](obj, {**ctx, "spec": spec})
            viol.extend(rv)
            metrics.update(rm)
        except Exception as exc:  # noqa: BLE001
            viol.append(_v("RECONCILE_ERROR", AMBER, f"{spec['reconcile']}: {type(exc).__name__}: {str(exc)[:160]}"))

    res.update(status=_worst(viol), violations=viol, metrics=metrics, dims=dims, tables=tables_out)
    return res


def _roster(viol: list, name: str, lanes: set, ctx: dict[str, Any], t: dict[str, Any]) -> None:
    lanes = {x for x in lanes if x}
    expected = set(ctx.get("roster") or [])
    retired = set(ctx.get("retired") or [])
    mode = t.get("roster", "exact")
    if expected and mode in ("exact", "superset"):
        miss = sorted(expected - lanes)
        if miss:
            viol.append(_v("ROSTER_MISMATCH", t.get("roster_severity", AMBER), f"{name}: active tiles missing {miss}"))
    if expected and mode == "exact":
        extra = sorted(lanes - expected - retired)
        if extra:
            viol.append(_v("ROSTER_MISMATCH", t.get("roster_severity", AMBER), f"{name}: unknown lanes {extra}"))
    if t.get("no_retired") and retired & lanes:
        viol.append(_v("RETIRED_LANE_PRESENT", t.get("retired_severity", AMBER),
                       f"{name}: retired lanes shown as current {sorted(retired & lanes)}"))


def _worst(viol: list[dict[str, Any]]) -> str:
    worst = max((_RANK.get(v["severity"], 0) for v in viol), default=0)
    if worst == 0:
        return GREEN
    if worst == 1 and all(v["severity"] in (SKIP, INFO) for v in viol):
        return SKIP if any(v["severity"] == SKIP for v in viol) else GREEN
    return {3: RED, 2: AMBER, 1: SKIP}[worst]


# ------------------------------------------------------------------ drift

def drift(spec: dict[str, Any], result: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare this evaluation with the last ``DRIFT_WINDOW`` stored ones (newest first)."""
    d = spec.get("drift") or {}
    if d.get("disabled") or result["status"] == SKIP:
        return []
    base = [h for h in history if h.get("id") == spec["id"] and h.get("metrics") is not None][:DRIFT_WINDOW]
    if len(base) < int(d.get("min_history", 2)):
        return []
    out = []
    ratio = float(d.get("collapse_ratio", 0.5))
    floor = float(d.get("min_baseline", 5))
    for k, v in (result.get("metrics") or {}).items():
        prev = [h["metrics"].get(k) for h in base if isinstance(h["metrics"].get(k), (int, float))]
        if len(prev) < 2:
            continue
        med = statistics.median(prev)
        if med >= floor and v <= ratio * med:
            out.append(_v("DRIFT_COLLAPSE", RED if v == 0 or v <= 0.2 * med else AMBER,
                          f"{k} fell to {v:g} from a baseline median {med:g} over {len(prev)} evaluations",
                          baseline=med))
    for name, cols in (result.get("dims") or {}).items():
        seen: dict[str, int] = {}
        nobs = 0
        for h in base:
            hc = (h.get("dims") or {}).get(name)
            if hc:
                nobs += 1
                for c in hc:
                    seen[c] = seen.get(c, 0) + 1
        if nobs < 2 or not cols:
            continue
        dropped = sorted(c for c, k in seen.items() if k >= max(2, nobs // 2) and c not in cols)
        if dropped:
            out.append(_v("DRIFT_DIMS_DROPPED", RED, f"{name}: columns present in {nobs} prior evaluations now absent "
                                                     f"{dropped[:12]}", dropped=dropped))
    return out


# ------------------------------------------------------------------ report-shape drift (archive)

def report_shape(obj: Any, depth: int = 3) -> dict[str, Any]:
    lists: dict[str, int] = {}
    cols: dict[str, list[str]] = {}

    def walk(o: Any, p: str, d: int) -> None:
        if isinstance(o, dict) and d < depth:
            for k, v in o.items():
                walk(v, f"{p}.{k}" if p else str(k), d + 1)
        elif isinstance(o, list):
            lists[p or "$"] = len(o)
            if o and isinstance(o[0], dict):
                keys: set[str] = set()
                for r in o[:200]:
                    if isinstance(r, dict):
                        keys.update(r)
                cols[p or "$"] = sorted(keys)
    walk(obj, "", 0)
    return {"lists": lists, "columns": cols}


def _load_report(p: Path) -> Any:
    raw = p.read_bytes()
    if p.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _report_name(p: Path) -> str:
    n = p.name
    return n[:-3] if n.endswith(".gz") else n


def archive_snapshots(paths: Paths) -> list[tuple[str, Path]]:
    """(sort key, directory of report files) for archived generations and the full report history, oldest first."""
    root = Path(paths.archive)
    snaps: list[tuple[str, Path]] = []
    for g in sorted((root / "generations").glob("*/*")):
        if (g / "reports").is_dir():
            snaps.append((g.name[:16], g / "reports"))
    for h in sorted((root / "report-history").glob("*")):
        if h.is_dir():
            snaps.append((h.name[:16], h))
    return sorted(snaps)


def archive_drift(paths: Paths, cache: dict[str, Any], *, max_snapshots: int = ARCHIVE_SNAPSHOTS) -> dict[str, Any]:
    """Per report: list lengths and column sets across archived snapshots; flag collapse, dropped columns, disappearance."""
    t0 = time.time()
    snaps = archive_snapshots(paths)
    by_report: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    present: dict[str, set[str]] = {}
    skipped = []
    keep: dict[str, Any] = {}
    # Generations and history are separate series (12 vs ~100 reports); compare each report within its own series.
    series = {"generations": [s for s in snaps if "generations" in str(s[1])][-max_snapshots:],
              "report-history": [s for s in snaps if "report-history" in str(s[1])][-max_snapshots:]}
    findings = []
    for sname, ss in series.items():
        by_report.clear()
        present.clear()
        for key, d in ss:
            names = set()
            for p in sorted(d.iterdir()):
                if not p.is_file() or not (p.name.endswith(".json") or p.name.endswith(".json.gz")):
                    continue
                name = _report_name(p)
                names.add(name)
                st = p.stat()
                ck = f"{p}|{st.st_mtime_ns}|{st.st_size}"
                shape = cache.get(ck)
                if shape is None:
                    if st.st_size > ARCHIVE_MAX_FILE_BYTES:
                        skipped.append(f"{p.name} {st.st_size // 1_000_000} MB")
                        continue
                    try:
                        shape = report_shape(_load_report(p))
                    except Exception as exc:  # noqa: BLE001
                        shape = {"error": f"{type(exc).__name__}"}
                keep[ck] = shape
                by_report.setdefault(name, []).append((key, shape))
            present[key] = names
        keys = [k for k, _ in ss]
        if len(keys) < 2:
            continue
        latest = keys[-1]
        for name, obs in sorted(by_report.items()):
            prev_keys = [k for k in keys[:-1] if name in present.get(k, set())]
            if name not in present.get(latest, set()):
                if len(prev_keys) >= 2:
                    findings.append({"series": sname, "report": name, "kind": "REPORT_DISAPPEARED", "severity": AMBER,
                                     "detail": f"present in {len(prev_keys)} prior snapshots, absent from {latest}"})
                continue
            cur = dict(obs).get(latest) or {}
            prev = [s for k, s in obs if k != latest and "lists" in s]
            if not prev or "lists" not in cur:
                continue
            for lp, n in (prev[-1]["lists"]).items():
                hist = [s["lists"].get(lp) for s in prev if isinstance(s["lists"].get(lp), int)]
                med = statistics.median(hist) if hist else 0
                now_n = cur["lists"].get(lp)
                if med >= 5 and (now_n is None or now_n <= 0.5 * med):
                    findings.append({"series": sname, "report": name, "kind": "REPORT_LIST_COLLAPSE",
                                     "severity": RED if not now_n else AMBER, "path": lp,
                                     "detail": f"{lp}: {now_n if now_n is not None else 'absent'} rows vs median {med:g} "
                                               f"over {len(hist)} prior snapshots"})
            for lp, cols in (prev[-1]["columns"]).items():
                now_cols = cur["columns"].get(lp)
                if now_cols is None:
                    continue
                stable = [c for c in cols if all(c in (s["columns"].get(lp) or [c]) for s in prev[-3:])]
                dropped = sorted(set(stable) - set(now_cols))
                if dropped:
                    findings.append({"series": sname, "report": name, "kind": "REPORT_COLUMNS_DROPPED", "severity": RED,
                                     "path": lp, "detail": f"{lp}: columns dropped {dropped[:10]}"})
    cache.clear()
    cache.update(keep)
    return {"generated_at": iso(time.time()), "ms": int((time.time() - t0) * 1000),
            "snapshots": {k: [s[0] for s in v] for k, v in series.items()},
            "reports_watched": len({n for s in present.values() for n in s}), "skipped_large": skipped[:20],
            "findings": findings}


# ------------------------------------------------------------------ coverage

def nav_sections(analyzer_repo: Path) -> list[dict[str, str]]:
    """Every /details nav section and every decision-page link of :9001, parsed (not imported) from source."""
    src = Path(analyzer_repo) / "services" / "btc-conservative-agent" / "research" / "research_dashboard.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    out: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name == "REPORT_NAV_GROUPS":
            for grp in node.value.elts:
                for item in grp.elts[2].elts:
                    sid = item.elts[0].value if isinstance(item.elts[0], ast.Constant) else None
                    label = item.elts[1].value if isinstance(item.elts[1], ast.Constant) else ""
                    if sid:
                        out.append({"section": f"details#{sid}", "label": label})
        elif name == "DECISION_NAV_LINKS":
            for item in node.value.elts:
                label, href = (e.value for e in item.elts)
                out.append({"section": f"page:{href}", "label": label})
    return out


def coverage(analyzer_repo: Path, reg: dict[str, Any]) -> dict[str, Any]:
    covered = {s for c in reg["contracts"] for s in c.get("covers") or []}
    try:
        secs = nav_sections(analyzer_repo)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}", "sections": 0, "uncovered": []}
    unc = [s for s in secs if s["section"] not in covered]
    return {"sections": len(secs), "covered": len(secs) - len(unc), "uncovered": unc}


def retired_lanes(analyzer_repo: Path) -> list[str]:
    src = Path(analyzer_repo) / "services" / "btc-conservative-agent" / "combo_pathway_config.py"
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "RETIRED_TILE_LANES":
            call = node.value
            arg = call.args[0] if isinstance(call, ast.Call) and call.args else call
            try:
                return sorted(ast.literal_eval(arg))
            except ValueError:
                return []
    return []


# ------------------------------------------------------------------ reconcilers
# Each returns (violations, metrics). ``ctx`` carries the fetcher, store, facts and roster.

def _rec_lanes_vs_cohort(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """/details Current Lanes closes and PnL vs the same generation's canonical ledger_reconciliation cohort."""
    summ, _meta = ctx["fetch"].get({"kind": "file", "path": "{exports}/summary.json"})
    cohort = get_path(summ, "ledger_reconciliation.analyzer_cohort") if summ is not MISSING else MISSING
    if not isinstance(cohort, dict) or not cohort:
        return [_v("RECONCILE_SKIPPED", INFO, "export summary has no ledger_reconciliation.analyzer_cohort")], {}
    viol, met = [], {}
    for row in obj.get("lanes") or []:
        lane = row.get("lane")
        c = cohort.get(lane)
        a = row.get("executed_closes")
        if not isinstance(c, dict) or a is None:
            continue
        n, pnl, apnl = int(c.get("n") or 0), _num(c.get("net_pnl_usd")), _num(row.get("pnl"))
        met[f"closes:{lane}:lanes"], met[f"closes:{lane}:cohort"] = a, n
        if n and int(a) != n:
            sev = RED if int(a) < 0.5 * n else AMBER
            viol.append(_v("RECONCILE_MISMATCH", sev, f"{lane}: Current Lanes shows {a} closes / ${apnl} but the canonical "
                                                      f"cohort has {n} closes / ${pnl:.3f} (ledger_reconciliation)"))
        elif pnl is not None and apnl is not None and abs(pnl - apnl) > 0.02:
            viol.append(_v("RECONCILE_MISMATCH", AMBER, f"{lane}: Current Lanes PnL ${apnl} vs cohort ${pnl:.3f}"))
    return viol, met


def _rec_export_ledger(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    lr = get_path(obj, "ledger_reconciliation")
    if lr is MISSING or not isinstance(lr, dict):
        return [_v("RECONCILE_MISSING", AMBER, "export summary has no ledger_reconciliation block")], {}
    lvl = str(lr.get("level") or lr.get("status") or "").upper()
    sev = RED if lvl == "RED" else AMBER if lvl == "AMBER" else None
    return ([_v("RECONCILE_MISMATCH", sev, f"ledger_reconciliation {lvl}: {lr.get('reasons')}")] if sev else []), {}


def _rec_combos_genome(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """Canonical Top-100 must materialise rows once policies were evaluated; the legacy table must not stand in for it."""
    viol = []
    grid = obj.get("policy_grid") or {}
    evaluated = _num(get_path(grid, "search_counts.unique_policies_evaluated")) or 0
    rows = len(grid.get("rows") or [])
    legacy = obj.get("legacy_executed_combos") or {}
    met = {"genome:evaluated": evaluated, "genome:rows": rows, "legacy:rows": len(legacy.get("rows") or [])}
    ldims = legacy.get("dimensions") or []
    if rows == 0 and legacy.get("rows"):
        viol.append(_v("DIMENSION_COLLAPSE", RED,
                       f"Top-100 canonical grid has 0 rows ({evaluated:g} policies evaluated) so the page shows only the legacy "
                       f"executed table over {ldims}: entry offset/chase/SL/trail/thesis-cut/ladder/ATR/MFE/R genes are not compared"))
    gg = ctx.get("genome_grid")
    if gg:
        met["genome_grid:rows"] = gg.get("rows", 0)
    return viol, met


def _rec_fly_roster(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    lanes = obj.get("active_tile_lanes") or [t.get("lane") for t in obj.get("active_tiles") or [] if isinstance(t, dict)]
    man = ctx["fetch"].get({"kind": "http", "url": "http://127.0.0.1:9001/api/manifest"})[0]
    man_lanes = [t.get("lane") for t in (man or {}).get("active_tiles") or []] if man is not MISSING else []
    if lanes and man_lanes and set(lanes) != set(man_lanes):
        return [_v("ROSTER_MISMATCH", AMBER, f"Fly roster {sorted(lanes)} != analyzer manifest {sorted(man_lanes)}")], {}
    return [], {}


def _rec_data_health_vs_selfaware(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """Analyzer data-health streams vs the self-aware stream catalog: every critical stream must appear in both."""
    data = (ctx.get("docs") or {}).get("data") or {}
    crit = sorted(s["stream"] for s in data.get("streams") or [] if s.get("critical"))
    streams = get_path(obj, "report.streams")
    names = set()
    if isinstance(streams, list):
        names = {str(s.get("stream") or s.get("name")) for s in streams if isinstance(s, dict)}
    elif isinstance(streams, dict):
        names = set(streams)
    met = {"streams:analyzer": len(names), "streams:selfaware_critical": len(crit)}
    if not names or not crit:
        return [], met
    norm = {n.lower().replace("-", "_") for n in names}
    miss = [c for c in crit if not any(c.lower() in n or n in c.lower() for n in norm)]
    return ([_v("RECONCILE_MISMATCH", INFO, f"critical self-aware streams not named in analyzer data health: {miss}")]
            if miss else []), met


def _cohort(ctx: dict[str, Any]) -> dict[str, Any]:
    summ, _meta = ctx["fetch"].get({"kind": "file", "path": "{exports}/summary.json"})
    c = get_path(summ, "ledger_reconciliation.analyzer_cohort") if summ is not MISSING else MISSING
    return c if isinstance(c, dict) else {}


def _rec_decision_vs_cohort(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """Decision page trade count ('consistent') vs the canonical cohort closes of the same tiles."""
    cohort = _cohort(ctx)
    tc = obj.get("trade_counts") or {}
    tile_n = tc.get("tile")
    if not cohort or tile_n is None:
        return [], {}
    total = sum(int((v or {}).get("n") or 0) for v in cohort.values())
    met = {"trades:decision": tile_n, "trades:cohort": total}
    if total and int(tile_n) != total:
        return [_v("RECONCILE_MISMATCH", AMBER, f"Decision page reports {tile_n} tile trades (consistent={tc.get('consistent')}) "
                                               f"but the canonical cohort has {total} closes for the same tiles")], met
    return [], met


def _rec_accumulator_vs_cohort(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    cohort = _cohort(ctx)
    viol, met = [], {}
    labelled_usd = obj.get("pnl_unit") == "USD"
    if not labelled_usd:
        viol.append(_v("UNIT_UNLABELLED", AMBER, "accumulator by_lane pnl carries no unit (pre-fix status file)"))
    rec = obj.get("ledger_reconciliation") if isinstance(obj.get("ledger_reconciliation"), dict) else None
    if rec is not None:
        met["accumulator:ledger_reconciliation"] = rec.get("status")
        if rec.get("status") == "MISMATCH":
            for lane in rec.get("mismatched_lanes") or []:
                r = (rec.get("lanes") or {}).get(lane) or {}
                viol.append(_v("RECONCILE_MISMATCH", AMBER,
                               f"{lane}: accumulator n={r.get('accumulator_n')} ${r.get('accumulator_net_pnl_usd')} vs "
                               f"mirror ledger n={r.get('ledger_n')} ${r.get('ledger_net_pnl_usd')}"))
        elif rec.get("status") != "MATCH":
            viol.append(_v("RECONCILE_UNAVAILABLE", AMBER, f"accumulator ledger reconciliation: {rec.get('status')}"))
    for lane, v in (obj.get("by_lane") or {}).items():
        c = cohort.get(lane)
        if not isinstance(c, dict) or not isinstance(v, dict):
            continue
        an, cn = int(v.get("n") or 0), int(c.get("n") or 0)
        ap = _num(v.get("net_pnl_usd") if labelled_usd else v.get("pnl"))
        cp = _num(c.get("net_pnl_usd"))
        met[f"closes:{lane}:accumulator"], met[f"closes:{lane}:cohort"] = an, cn
        # The accumulator holds the mirror-ledger cohort; the analyzer cohort drops quarantined closes.
        if abs(an - cn) > 1 and rec is None:
            viol.append(_v("RECONCILE_MISMATCH", AMBER, f"{lane}: accumulator n={an} vs cohort n={cn}"))
        if ap is not None and cp is not None and ap * cp < 0 and abs(ap) > 0.05 and abs(cp) > 0.05:
            unit = "USD" if labelled_usd else "unit unlabelled"
            viol.append(_v("UNIT_OR_SIGN_MISMATCH", AMBER, f"{lane}: accumulator pnl={ap:+.2f} ({unit}) but cohort "
                                                          f"net_pnl_usd={cp:+.3f}: opposite sign"))
    return viol, met


def _rec_export_tiles_vs_cohort(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    cohort = _cohort(ctx)
    viol, met = [], {}
    for r in obj.get("rows") or []:
        lane, n = r.get("research_lane"), r.get("n")
        c = cohort.get(lane)
        if not isinstance(c, dict) or n is None:
            continue
        met[f"closes:{lane}:tile_stats"] = n
        if int(n) != int(c.get("n") or 0):
            viol.append(_v("RECONCILE_MISMATCH", AMBER, f"{lane}: export tile_stats n={n} vs cohort n={c.get('n')}"))
    return viol, met


def _rec_fly_analyzer_mirror(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    if obj.get("ok") is False or obj.get("mirror_available") is False:
        return [_v("DEAD_SECTION", AMBER, f"Fly {obj.get('endpoint')} panel: ok={obj.get('ok')} "
                                          f"mirror_available={obj.get('mirror_available')} - Fly dashboard shows no analyzer data")], {}
    return [], {}


CHASE_ANALYTICS_OK = ("VERIFIED_RECENT_SNAPSHOT", "NOT_APPLICABLE")


def _rec_fly_chase_buckets(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """Fly 'Chase entry selector' / 'Virtual Chase Candidates' panels: verified data or an explicit NOT_APPLICABLE."""
    ca = obj.get("chase_analytics")
    if not isinstance(ca, dict):
        return [_v("FIELD_NOT_EXPORTED", INFO, "chase_analytics is owner-only (/api/state with token); the runtime snapshot "
                                               "does not carry it yet - post-freeze #317 exports it")], {}
    status = str(ca.get("status") or "")
    if status not in CHASE_ANALYTICS_OK:
        return [_v("DEAD_SECTION", RED, f"Fly chase panels: chase_analytics.status={status or 'null'} "
                                        f"(reason {ca.get('reason')}); expected {list(CHASE_ANALYTICS_OK)}")], {}
    return [], {}


HEADLINE_FILL_MODEL = "REALISTIC_V1"
_OPTIMISTIC_FILL_TOKENS = ("IDEAL", "TOUCH", "OPTIMISTIC", "MID_TO_MID", "BBO_MARKETABLE")


def _declared_fill_model(obj: Any) -> str | None:
    fm = obj.get("fill_model") if isinstance(obj, dict) else None
    if isinstance(fm, dict):
        fm = fm.get("fill_model") or fm.get("version")
    return str(fm) if fm else None


def _optimistic(value: Any) -> bool:
    text = str(value or "").upper()
    return bool(text) and text != HEADLINE_FILL_MODEL and any(t in text for t in _OPTIMISTIC_FILL_TOKENS)


def _optimistic_headlines(obj: Any, path: str = "", depth: int = 0) -> list[str]:
    """Paths where an optimistic fill world is labelled as the headline (shadows must carry a non-headline role)."""
    if depth > 6:
        return []
    hits: list[str] = []
    if isinstance(obj, dict):
        role = str(obj.get("fill_model_role") or obj.get("role") or "").upper()
        if role == "HEADLINE" and _optimistic(obj.get("fill_model") or obj.get("fill_world")):
            hits.append(path or "$")
        for k, v in obj.items():
            if str(k).startswith("headline") and not isinstance(v, (dict, list)) and _optimistic(v):
                hits.append(f"{path}.{k}".lstrip("."))
            elif isinstance(v, (dict, list)):
                hits.extend(_optimistic_headlines(v, f"{path}.{k}".lstrip("."), depth + 1))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            hits.extend(_optimistic_headlines(v, f"{path}[{i}]", depth + 1))
    return hits


def _rec_fill_model_headline(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """Every headline result must declare fill_model=REALISTIC_V1; an optimistic world shown as headline is RED."""
    viol = []
    spec = ctx.get("spec") or {}
    declared = _declared_fill_model(obj)
    headline_world = obj.get("headline_fill_world") if isinstance(obj, dict) else None
    met = {"fill_model:declared": declared or "UNDECLARED", "fill_model:headline_world": headline_world or "-"}
    if declared != HEADLINE_FILL_MODEL:
        sev = RED if declared and _optimistic(declared) else spec.get("fill_model_undeclared_severity", RED)
        viol.append(_v("FILL_MODEL_UNDECLARED", sev,
                       f"headline result declares fill_model={declared or 'none'}; required {HEADLINE_FILL_MODEL}"
                       + (f" ({spec['fill_model_pending']})" if spec.get("fill_model_pending") and sev != RED else "")))
    if headline_world is not None and headline_world != HEADLINE_FILL_MODEL:
        viol.append(_v("FILL_MODEL_OPTIMISTIC_HEADLINE", RED,
                       f"headline_fill_world={headline_world}; optimistic worlds may only appear as labelled comparison shadows"))
    bad = _optimistic_headlines(obj)
    if bad:
        viol.append(_v("FILL_MODEL_OPTIMISTIC_HEADLINE", RED,
                       f"optimistic fill model labelled as headline at {', '.join(bad[:5])}"))
    met["fill_model:optimistic_headlines"] = len(bad)
    return viol, met


def _rec_edges_fill_model(obj: Any, ctx: dict[str, Any]) -> tuple[list, dict]:
    """Edge-tracker hit rates are headline numbers: every published edge row must be REALISTIC_V1."""
    store = ctx.get("store")
    if store is None or not store.table_exists("res_edges"):
        return [_v("RECONCILE_SKIPPED", INFO, "edge tracker has not published res_edges yet")], {}
    cols = {r["column_name"] for r in store.read(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'res_edges'")}
    if "fill_model" not in cols:
        return [_v("FILL_MODEL_UNDECLARED", RED, "res_edges rows carry no fill_model: hit rates are pre-REALISTIC_V1 "
                   "(gross mid-to-mid) numbers shown as headline")], {"edges:fill_model": "UNDECLARED"}
    rows = store.read("SELECT fill_model, count(*) AS n FROM res_edges GROUP BY fill_model")
    met = {f"edges:{r['fill_model'] or 'UNDECLARED'}": int(r["n"]) for r in rows}
    bad = [r for r in rows if r.get("fill_model") != HEADLINE_FILL_MODEL]
    viol = [_v("FILL_MODEL_UNDECLARED", RED, f"{sum(int(r['n']) for r in bad)} edge rows declare "
               f"{sorted({str(r.get('fill_model')) for r in bad})}; required {HEADLINE_FILL_MODEL}")] if bad else []
    return viol, met


RECONCILERS: dict[str, Callable[[Any, dict[str, Any]], tuple[list, dict]]] = {
    "fill_model_headline": _rec_fill_model_headline,
    "edges_fill_model": _rec_edges_fill_model,
    "fly_chase_buckets": _rec_fly_chase_buckets,
    "lanes_vs_cohort": _rec_lanes_vs_cohort,
    "export_ledger": _rec_export_ledger,
    "combos_genome": _rec_combos_genome,
    "fly_roster": _rec_fly_roster,
    "data_health_vs_selfaware": _rec_data_health_vs_selfaware,
    "fly_analyzer_mirror": _rec_fly_analyzer_mirror,
    "decision_vs_cohort": _rec_decision_vs_cohort,
    "accumulator_vs_cohort": _rec_accumulator_vs_cohort,
    "export_tiles_vs_cohort": _rec_export_tiles_vs_cohort,
}


# ------------------------------------------------------------------ run

def _genome_grid(paths: Paths) -> dict[str, Any] | None:
    root = Path(paths.exports).parent / "genome-grid"
    rep = read_json(root / "genome_grid_report.json") if root.exists() else None
    if not isinstance(rep, dict):
        return None
    return {"rows": rep.get("rows_total") or len(rep.get("rows") or []), "generated_at": rep.get("generated_at")}


def analyzer_busy(paths: Paths) -> bool:
    """An analyzer cycle is in flight (any phase, started within the last hour and not finished)."""
    cyc = read_json(Path(paths.chain) / "segment-analyzer-cycle.status.json") or {}
    started = parse_ts(cyc.get("startedAt"))
    return bool(started and not cyc.get("finishedAt") and time.time() - started < 3600)


def run(store, paths: Paths, facts: dict[str, Any], state: dict[str, Any], docs: dict[str, Any], *,
        tier: str = "heavy", registry: dict[str, Any] | None = None, fetcher: Fetcher | None = None,
        now: float | None = None) -> dict[str, Any]:
    t0 = time.time()
    now = now or t0
    reg = registry or load_registry()
    rt = facts.get("runtime") or {}
    ctx: dict[str, Any] = {"now": now, "store": store, "facts": facts, "docs": docs,
                           "roster": rt.get("active_tile_lanes") or [], "retired": retired_lanes(paths.analyzer_repo),
                           "genome_grid": _genome_grid(paths)}
    fetch = fetcher or Fetcher(paths, docs)
    ctx["fetch"] = fetch
    specs = [s for s in reg["contracts"] if tier == "heavy" or s["tier"] == "light"]
    history = store.history(HISTORY_TABLE, limit=4000, kind="CONTRACT") if store is not None else []
    hist_by: dict[str, list] = {}
    for h in history:
        hist_by.setdefault(h.get("id"), []).append(h)
    results = []
    for spec in specs:
        try:
            obj, meta = fetch.get(spec["source"], int(spec.get("max_bytes", DEFAULT_MAX_BYTES)))
            res = evaluate(spec, obj, meta, ctx)
            if tier == "heavy":
                res["violations"].extend(drift(spec, res, hist_by.get(spec["id"], [])))
            else:
                # Drift is judged only by the heavy pass; keep its verdict so light passes do not flap it.
                old = (state.get("contracts_last") or {}).get(spec["id"]) or {}
                res["violations"].extend(v for v in old.get("violations") or [] if str(v.get("kind", "")).startswith("DRIFT_"))
            res["status"] = _worst(res["violations"])
        except Exception as exc:  # noqa: BLE001 - one broken contract never hides the rest
            res = {"id": spec["id"], "surface": spec["surface"], "title": spec["title"], "tier": spec["tier"],
                   "status": AMBER, "violations": [_v("CONTRACT_ERROR", AMBER, f"{type(exc).__name__}: {str(exc)[:200]}")],
                   "metrics": {}, "dims": {}, "tables": [], "evaluated_at": iso(now), "depends_on": spec.get("depends_on")}
        results.append(res)
    prev = {r["id"]: r for r in (state.get("contracts_last") or {}).values()} if isinstance(state.get("contracts_last"), dict) else {}
    merged = {**prev, **{r["id"]: _slim(r) for r in results}}
    merged = {k: v for k, v in merged.items() if k in {s["id"] for s in reg["contracts"]}}
    state["contracts_last"] = merged
    extra: dict[str, Any] = {}
    if tier == "heavy":
        state.pop("contracts_shape_cache", None)
        cache_file = Path(paths.home) / "contracts-cache" / "archive_shapes.json"
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        try:
            extra["archive_drift"] = archive_drift(paths, cache)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, default=str), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            extra["archive_drift"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}", "findings": []}
        extra["coverage"] = coverage(paths.analyzer_repo, reg)
        state["contracts_heavy_extra"] = {k: v for k, v in extra.items()}
        state["contracts_heavy_at"] = iso(now)
        if store is not None:
            store.append(HISTORY_TABLE, [{"at": iso(now), "kind": "CONTRACT", "id": r["id"], "status": r["status"],
                                          "metrics": r.get("metrics"), "dims": r.get("dims"),
                                          "violations": [v["kind"] for v in r["violations"]]} for r in results],
                         sources=["section_contracts.json", "dashboard APIs", "exports", "laptop-chain snapshots"])
    else:
        extra = dict(state.get("contracts_heavy_extra") or {})
    allr = sorted(merged.values(), key=lambda r: (-_RANK.get(r["status"], 0), r["id"]))
    counts = {s: sum(1 for r in allr if r["status"] == s) for s in (RED, AMBER, GREEN, SKIP)}
    surfaces: dict[str, str] = {}
    for r in allr:
        cur = surfaces.get(r["surface"], GREEN)
        surfaces[r["surface"]] = r["status"] if _RANK.get(r["status"], 0) > _RANK.get(cur, 0) else cur
    return {"schema": SCHEMA, "generated_at": iso(now), "tier": tier, "registry_hash": reg["registry_hash"],
            "contracts_total": len(reg["contracts"]), "evaluated": len(results), "counts": counts, "surfaces": surfaces,
            "fly_calls": fetch.fly_calls, "ms": int((time.time() - t0) * 1000),
            "heavy_at": state.get("contracts_heavy_at"),
            "contracts": allr, **extra}


def _slim(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if k != "dims"}


def summary(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    worst = [{"id": r["id"], "status": r["status"], "why": "; ".join(v["detail"] for v in r["violations"]
                                                                       if v["severity"] in (RED, AMBER))[:300]}
             for r in doc["contracts"] if r["status"] in (RED, AMBER)]
    collapse = [{"id": r["id"], "kinds": sorted({v["kind"] for v in r["violations"] if v["kind"] in COLLAPSE_KINDS})}
                for r in doc["contracts"] if any(v["kind"] in COLLAPSE_KINDS and v["severity"] in (RED, AMBER)
                                                 for v in r["violations"])]
    ad = doc.get("archive_drift") or {}
    return {"generated_at": doc["generated_at"], "heavy_at": doc.get("heavy_at"), "tier": doc["tier"],
            "counts": doc["counts"], "surfaces": doc["surfaces"], "contracts_total": doc["contracts_total"],
            "registry_hash": doc["registry_hash"], "offenders": worst, "collapse": collapse,
            "archive_drift_findings": len(ad.get("findings") or []),
            "archive_drift_red": sum(1 for f in ad.get("findings") or [] if f["severity"] == RED),
            "coverage": {k: v for k, v in (doc.get("coverage") or {}).items() if k != "uncovered"},
            "uncovered": [s["section"] for s in (doc.get("coverage") or {}).get("uncovered") or []]}


COLLAPSE_KINDS = {"DIMENSION_COLLAPSE", "DRIFT_COLLAPSE", "DRIFT_DIMS_DROPPED", "EMPTY_SILENT", "LABEL_CONTRADICTION",
                  "DEAD_SECTION"}


def drill(spec: dict[str, Any], fetcher: Fetcher, limit: int = 100) -> dict[str, Any]:
    """Raw rows behind a contract: each table's first ``limit`` rows plus required/counter values, fetched now."""
    obj, meta = fetcher.get(spec["source"], int(spec.get("max_bytes", DEFAULT_MAX_BYTES)))
    if obj is MISSING:
        return {"meta": meta, "tables": {}, "fields": {}}
    tables = {}
    for t in spec.get("tables") or []:
        rows = get_path(obj, t["path"])
        if isinstance(rows, dict) and t.get("dict_rows"):
            rows = [{"_key": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in rows.items()]
        tables[t.get("name") or t["path"]] = {
            "total": len(rows) if isinstance(rows, list) else None,
            "rows": rows[:limit] if isinstance(rows, list) else None,
            "profile": column_profile(rows) if isinstance(rows, list) else None}
    fields = {p: (None if (v := get_path(obj, p)) is MISSING else v)
              for p in (spec.get("required") or []) + [c["path"] for c in spec.get("counters") or []]
              + (spec.get("live_fields") or [])}
    return {"meta": meta, "tables": tables, "fields": fields}
