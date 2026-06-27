"""Last-run bug report — surfaces errors from the most recent bot/analyzer run.

Goal: after an update or a crash, the AI (and the operator) can read ONE report
that says what went wrong in the last run, instead of guessing. This scans the
live logs + crash dump + port states and extracts the error signal (Traceback,
CRITICAL, ERROR, Exception, CRASH, FATAL) with surrounding context.

Read-only. Never touches execution. Stdlib-only (no psutil dependency).
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]

# (label, relative-to-agent-root path)
LOG_TARGETS = [
    ("bot_runtime", "bot_runtime.log"),
    ("analyzer_run", "analyzer_run.log"),
    ("near_edge", "near_edge.log"),
    ("signal_persist", "signal_persist.log"),
    ("supervisor", "../../.home-stack-supervisor.log"),
    ("relay_pusher", "../../.home-relay-pusher.log"),
    ("start_all", "../../.home-start-all.log"),
    ("cmd_worker_err", "../../.home-cmd-worker.err.log"),
    ("wire", "../../.home-wire.log"),
]

PORTS = [
    ("bot", 7002),
    ("bridge", 7810),
    ("analyzer", 9500),
]

ERROR_RE = re.compile(
    r"(Traceback|CRITICAL|FATAL|CRASH|Exception|Error:|ERROR |"
    r"Failed to|ImportError|ModuleNotFoundError|AttributeError|"
    r"KeyError|ValueError|RuntimeError|OSError|PermissionError|"
    r"WinError|exit code)",
    re.IGNORECASE,
)

TAIL_LINES = 25
ERROR_CONTEXT = 2  # lines of context around each error hit
MAX_ERROR_HITS = 40


def _rel_age(mtime: float) -> str:
    delta = datetime.now(timezone.utc).timestamp() - mtime
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta/60)}m ago"
    if delta < 86400:
        return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"


def _scan_log(label: str, rel_path: str) -> Dict[str, Any]:
    path = (AGENT_ROOT / rel_path).resolve()
    info: Dict[str, Any] = {
        "label": label,
        "path": str(path),
        "exists": path.is_file(),
    }
    if not path.is_file():
        info["status"] = "MISSING"
        return info
    st = path.stat()
    info["size_bytes"] = st.st_size
    info["last_written"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    info["last_written_age"] = _rel_age(st.st_mtime)
    # staleness flag: >10 min since last write means the process likely died
    info["stale"] = (datetime.now(timezone.utc).timestamp() - st.st_mtime) > 600

    try:
        # read all lines but cap memory for huge logs (bot_runtime is ~45MB)
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        info["status"] = "READ_ERROR"
        info["error"] = str(exc)
        return info

    info["line_count"] = len(lines)
    info["tail"] = "".join(lines[-TAIL_LINES:]).rstrip()

    # extract error hits with context
    hits: List[Dict[str, Any]] = []
    for i, line in enumerate(lines):
        if ERROR_RE.search(line):
            start = max(0, i - ERROR_CONTEXT)
            end = min(len(lines), i + ERROR_CONTEXT + 1)
            hits.append({
                "line_no": i + 1,
                "snippet": "".join(lines[start:end]).rstrip(),
            })
            if len(hits) >= MAX_ERROR_HITS:
                break
    info["error_hits"] = hits
    info["error_hit_count_total"] = sum(1 for _ in lines if ERROR_RE.search(_)) if len(lines) < 200000 else "too_many"
    info["status"] = "OK"
    return info


def _read_crash_dump() -> Dict[str, Any]:
    path = AGENT_ROOT / "crash_dump.json"
    if not path.is_file():
        return {"exists": False}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            content = fh.read().strip()
        if not content:
            return {"exists": True, "empty": True}
        # crash_dump.json may be JSONL (one state snapshot per line) or a single JSON
        if content[-1] == "}":
            last = content.splitlines()[-1]
            snap = json.loads(last)
            mtime = path.stat().st_mtime
            return {
                "exists": True,
                "format": "jsonl",
                "latest_snapshot": snap,
                "last_written": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "last_written_age": _rel_age(mtime),
            }
        return {"exists": True, "format": "unknown", "raw_head": content[:500]}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def _port_state(name: str, port: int) -> Dict[str, Any]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        return {"name": name, "port": port, "listening": True}
    except Exception:
        return {"name": name, "port": port, "listening": False}
    finally:
        s.close()


def _http_probe(port: int) -> Dict[str, Any]:
    import urllib.request
    for path in ("/api/ping", "/health", "/api/status"):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return {"port": port, "path": path, "http_status": resp.status, "ok": True}
        except Exception:
            continue
    return {"port": port, "ok": False}


def _git_state() -> Dict[str, Any]:
    def _run(args: List[str]) -> str:
        try:
            out = subprocess.check_output(args, cwd=str(REPO_ROOT), stderr=subprocess.DEVNULL, text=True, timeout=5)
            return out.strip()
        except Exception:
            return ""
    return {
        "head": _run(["git", "rev-parse", "--short", "HEAD"]) or "unknown",
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "unknown",
        "dirty_files": [f for f in _run(["git", "status", "--short"]).splitlines() if f][:40],
    }


def build_report() -> Dict[str, Any]:
    logs = [_scan_log(lbl, rel) for lbl, rel in LOG_TARGETS]
    ports = [_port_state(n, p) for n, p in PORTS]
    # http probe the listening ones
    for p in ports:
        if p["listening"]:
            p.update(_http_probe(p["port"]))

    # top-level verdict
    stale_logs = [l["label"] for l in logs if l.get("stale")]
    down_ports = [p["name"] for p in ports if not p["listening"]]
    error_total = sum(len(l.get("error_hits", [])) for l in logs)
    verdict_lines: List[str] = []
    if down_ports:
        verdict_lines.append(f"DOWN: {', '.join(down_ports)} not listening.")
    if stale_logs:
        verdict_lines.append(f"STALE (likely crashed): {', '.join(stale_logs)} stopped writing >10m ago.")
    if error_total:
        verdict_lines.append(f"ERRORS: {error_total} error lines captured across logs.")
    if not verdict_lines:
        verdict_lines.append("HEALTHY: all ports listening, no stale logs, no error hits.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "python": sys.version.split()[0],
        "verdict": " | ".join(verdict_lines),
        "down_ports": down_ports,
        "stale_logs": stale_logs,
        "ports": ports,
        "crash_dump": _read_crash_dump(),
        "logs": logs,
        "git": _git_state(),
        "how_to_use": (
            "Read `verdict` first, then each log's `error_hits` and `tail`. "
            "A stale log (stopped writing >10m ago) means that process died — "
            "read its tail to see the last lines before death. Feed this report "
            "to the AI so it knows the exact errors from the last run."
        ),
    }


def build_html() -> str:
    rep = build_report()
    css = "body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:20px}"
    css += "h1,h2{color:#58a6ff}pre{background:#161b22;border:1px solid #30363d;padding:10px;overflow:auto;white-space:pre-wrap;border-radius:6px}"
    css += ".ok{color:#3fb950}.bad{color:#f85149}.warn{color:#d29922}.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin:12px 0}"
    css += "table{border-collapse:collapse;width:100%}td,th{border:1px solid #30363d;padding:6px 8px;text-align:left;font-size:13px}"
    css += ".err{background:#2a1215;border-left:3px solid #f85149;margin:6px 0;padding:8px}"
    parts = [f"<html><head><meta charset='utf-8'><title>Last-Run Bug Report</title><style>{css}</style></head><body>"]
    parts.append(f"<h1>Last-Run Bug Report</h1>")
    parts.append(f"<p>Generated {rep['generated_at']} · host <code>{rep['host']}</code> · python {rep['python']}</p>")
    vcls = "bad" if (rep["down_ports"] or rep["stale_logs"]) else ("warn" if any(l.get("error_hits") for l in rep["logs"]) else "ok")
    parts.append(f"<div class='card'><h2>Verdict</h2><p class='{vcls}'><strong>{rep['verdict']}</strong></p>")
    parts.append(f"<p class='note'>{rep['how_to_use']}</p></div>")

    parts.append("<div class='card'><h2>Ports</h2><table><tr><th>Name</th><th>Port</th><th>Listening</th><th>HTTP</th></tr>")
    for p in rep["ports"]:
        cls = "ok" if p["listening"] else "bad"
        parts.append(f"<tr><td>{p['name']}</td><td>{p['port']}</td><td class='{cls}'>{p['listening']}</td><td>{p.get('http_status','—')}</td></tr>")
    parts.append("</table></div>")

    cd = rep["crash_dump"]
    parts.append("<div class='card'><h2>Crash Dump (latest state snapshot)</h2>")
    if cd.get("exists") and cd.get("latest_snapshot"):
        parts.append(f"<p>last written {cd.get('last_written_age','?')} · execution_paused={cd['latest_snapshot'].get('execution_paused')} · reason={cd['latest_snapshot'].get('execution_reason')}</p>")
        parts.append(f"<pre>{json.dumps(cd['latest_snapshot'], indent=2)}</pre>")
    else:
        parts.append(f"<pre>{json.dumps(cd, indent=2)}</pre>")
    parts.append("</div>")

    for log in rep["logs"]:
        cls = "bad" if log.get("stale") else ("warn" if log.get("error_hits") else "ok")
        parts.append(f"<div class='card'><h2 class='{cls}'>{log['label']}</h2>")
        if log.get("status") != "OK":
            parts.append(f"<p class='bad'>status: {log.get('status')} {log.get('error','')}</p></div>")
            continue
        parts.append(f"<p>{log.get('line_count')} lines · {log.get('size_bytes')} bytes · last written <strong>{log.get('last_written_age')}</strong>{' · <span class=bad>STALE (likely crashed)</span>' if log.get('stale') else ''}</p>")
        hits = log.get("error_hits", [])
        if hits:
            parts.append(f"<p class='warn'>{len(hits)} error hits captured (showing latest):</p>")
            for h in hits[-12:]:
                parts.append(f"<div class='err'><div class='note'>line {h['line_no']}</div><pre>{h['snippet']}</pre></div>")
        parts.append(f"<p><strong>Tail:</strong></p><pre>{log.get('tail','')}</pre></div>")

    parts.append("<div class='card'><h2>Git</h2><pre>" + json.dumps(rep["git"], indent=2) + "</pre></div>")
    parts.append("<div class='card'><h2>Raw JSON (for AI)</h2><pre>" + json.dumps(rep, indent=2).replace("<", "&lt;") + "</pre></div>")
    parts.append("</body></html>")
    return "\n".join(parts)


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(build_report(), indent=2))
