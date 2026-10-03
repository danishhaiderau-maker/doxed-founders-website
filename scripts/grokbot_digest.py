"""Redacted, read-only status digest for an external read-only monitor (Grokbot).

Builds one bounded JSON document from the laptop-only services (:9011 watcher,
:9021 self-aware, :9001 analyzer) using GET requests to 127.0.0.1 only. Every
field is copied through an explicit allowlist; strings are truncated and
scrubbed of filesystem paths and credential-looking material.

It never calls Fly, Railway or Bitfinex, never uses a token, binds no port and
writes nothing except the optional ``--out`` file. Serving the digest publicly
is a separate, post-freeze step (a Fly GET route behind a dedicated read-only
monitor token, fed by the watcher push), see
``diagnostics/GROKBOT-PROMPT-20261003.md`` in the canonical workspace.

  python scripts/grokbot_digest.py                 print the digest
  python scripts/grokbot_digest.py --out <file>    also write it atomically
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "grokbot_digest_v1"
WATCHER = "http://127.0.0.1:9011"
SELFAWARE = "http://127.0.0.1:9021"
ANALYZER = "http://127.0.0.1:9001"
MAX_STR = 240
MAX_ITEMS = 40

SOURCES: dict[str, str] = {
    "watcher": f"{WATCHER}/api/system-health",
    "selfaware": f"{SELFAWARE}/api/selfaware/health",
    "selfaware_uptime": f"{SELFAWARE}/api/selfaware/uptime",
    "selfaware_tiles": f"{SELFAWARE}/api/selfaware/tiles?window=24h",
    "selfaware_fees": f"{SELFAWARE}/api/selfaware/fees",
    "selfaware_capacity": f"{SELFAWARE}/api/selfaware/data/capacity",
    "analyzer_status": f"{ANALYZER}/api/status",
    "analyzer_sections": f"{ANALYZER}/api/sections/health",
    "analyzer_insights": f"{ANALYZER}/api/insights",
    "analyzer_readiness": f"{ANALYZER}/api/decision-readiness",
}

_PATH_RE = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\)[^\s\"',;)]*")
_SECRET_RE = re.compile(
    r"(?i)\b(?:(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie)\s*[=:]\s*"
    r"(?:bearer\s+)?\S+|bearer\s+\S+)"
)
_LONG_OPAQUE_RE = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_QUERY_RE = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*")


def scrub(value: Any) -> Any:
    """Truncate and redact one scalar; containers are handled by the allowlist builders."""
    if not isinstance(value, str):
        return value if isinstance(value, (int, float, bool)) or value is None else scrub(str(value))
    text = _SECRET_RE.sub("<redacted>", value)
    text = _QUERY_RE.sub(r"\1?<redacted>", text)
    text = _PATH_RE.sub("<path>", text)
    text = _LONG_OPAQUE_RE.sub(lambda m: m.group(0)[:12] + "…", text)
    return text if len(text) <= MAX_STR else text[: MAX_STR - 1] + "…"


def pick(src: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    src = src if isinstance(src, Mapping) else {}
    return {k: scrub(src.get(k)) for k in keys if k in src and not isinstance(src.get(k), (Mapping, list))}


def _list(value: Any) -> list:
    return value[:MAX_ITEMS] if isinstance(value, list) else []


def http_json(url: str, timeout: float = 20.0) -> tuple[Any, str | None]:
    if not url.startswith("http://127.0.0.1:"):
        return None, "NON_LOCAL_URL_REFUSED"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "grokbot-digest/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8")), f"HTTP_{exc.code}"
        except ValueError:
            return None, f"HTTP_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "UNREACHABLE"
    except ValueError:
        return None, "INVALID_JSON"


def _watcher(w: Any) -> dict[str, Any]:
    out = pick(w, ("verdict", "generated_at", "age_sec", "stale"))
    out["counts"] = pick((w or {}).get("counts"), ("GREEN", "AMBER", "RED", "SKIP"))
    out["failing"] = [pick(c, ("id", "status", "observed", "threshold", "last_good_at"))
                      for c in _list((w or {}).get("failing"))]
    out["open_alarms"] = len((w or {}).get("open_alarms") or [])
    return out


def _selfaware(h: Any) -> dict[str, Any]:
    out = pick(h, ("verdict", "generated_at"))
    out["counts"] = pick((h or {}).get("counts"), ("GREEN", "AMBER", "RED", "SKIP"))
    out["findings"] = [pick(f, ("id", "severity", "observed", "expected", "runbook"))
                       for f in _list((h or {}).get("findings")) if f.get("severity") in ("RED", "AMBER")]
    jobs = ((h or {}).get("engine") or {}).get("jobs") or {}
    out["jobs_last_ok"] = {scrub(name): scrub((job or {}).get("last_ok")) for name, job in list(jobs.items())[:MAX_ITEMS]}
    return out


def _analyzer(status: Any, sections: Any) -> dict[str, Any]:
    out = pick(status, ("ok", "ready", "stale", "required_reports_ok", "generated_at", "source_revision_parity",
                        "analyzer_sync_match", "generation_revision"))
    fresh = (status or {}).get("generation_freshness") or {}
    out["generation_current"] = scrub(fresh.get("current"))
    out["epoch_parity"] = scrub(fresh.get("epoch_parity"))
    out["required_report_failures"] = [
        scrub(f.get("report") if isinstance(f, Mapping) else f) for f in _list((status or {}).get("required_report_failures"))
    ]
    out["sections_counts"] = pick((sections or {}).get("counts"), ("GREEN", "AMBER", "RED", "INFO"))
    out["sections_not_green"] = [pick(s, ("id", "label", "severity")) for s in _list((sections or {}).get("sections"))
                                 if s.get("severity") not in ("GREEN", "INFO")]
    return out


def _tile_verdicts(insights: Any) -> list[dict[str, Any]]:
    data = (((insights or {}).get("components") or {}).get("analyzer_export") or {}).get("data") or {}
    keys = ("key", "n", "mean_usd", "ci_lo_usd", "ci_hi_usd", "p_holm", "q_bh", "corrected_verdict", "days_raw")
    return [pick(r, keys) for r in _list(data.get("tile_pool"))]


def _readiness(r: Any) -> dict[str, Any]:
    out = pick(r, ("status", "qualification", "live_policy_change_allowed", "real_bitfinex_trading_allowed", "stale"))
    out["failed_gates"] = [scrub(g.get("gate")) for g in _list((r or {}).get("qualification_gate_details"))
                           if isinstance(g, Mapping) and g.get("status") == "FAIL"]
    return out


def _capacity(c: Any) -> dict[str, Any]:
    laptop = pick((c or {}).get("laptop"), ("bot_data_gb", "cap_gb", "usage_pct", "growth_gb_per_day",
                                             "days_to_90pct", "retention_cap_status", "retention_last_run"))
    fly = pick((c or {}).get("fly"), ("free_gb", "hours_to_full", "ingest_gb_per_day"))
    return {"laptop": laptop, "fly": fly}


def build(fetch: Callable[[str], tuple[Any, str | None]] = http_json, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    raw: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for name, url in SOURCES.items():
        payload, err = fetch(url)
        raw[name] = payload if isinstance(payload, Mapping) else None
        sources[name] = {"ok": err is None and raw[name] is not None, "error": scrub(err)}
    fees = raw["selfaware_fees"] or {}
    return {
        "schema": SCHEMA,
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "sources": sources,
        "watcher": _watcher(raw["watcher"]),
        "selfaware": _selfaware(raw["selfaware"]),
        "uptime": pick(raw["selfaware_uptime"], ("running_uninterrupted_sec", "currently_interrupted",
                                                 "interruptions_24h", "generated_at")),
        "tiles_24h": [pick(t, ("lane", "closes", "wins", "win_rate", "net_usd", "worst_usd", "last_close",
                               "in_roster", "enabled")) for t in _list((raw["selfaware_tiles"] or {}).get("tiles"))],
        "tile_verdicts": _tile_verdicts(raw["analyzer_insights"]),
        "analyzer": _analyzer(raw["analyzer_status"], raw["analyzer_sections"]),
        "decision_readiness": _readiness(raw["analyzer_readiness"]),
        "fees": {**pick(fees, ("status", "source", "stale", "age_sec", "matches_cost_profile", "matches_everywhere")),
                 "mismatch_count": len(fees.get("mismatches") or [])},
        "capacity": _capacity(raw["selfaware_capacity"]),
    }


def write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=None)
    opts = p.parse_args(argv)
    digest = build()
    if opts.out:
        write_atomic(opts.out, digest)
    json.dump(digest, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
