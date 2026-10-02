"""Agent-facing client for the self-aware layer (127.0.0.1:9021). Read-only.

    import self_aware_client as sa
    h = sa.health()                     # verdict + findings (causes, runbook, drill SQL)
    for f in sa.problems(h): print(f["severity"], f["id"], f["observed"])
    sa.digest()["latest"]["headline"]   # hourly digest
    sa.ai_scorecard(window="7d", horizon="60m")["rows"]
    sa.ai_calls(limit=20, model="deepseek-flash")["calls"]
    sa.edges(status="CANDIDATE,HINT")["edges"]
    sa.query("SELECT strategy, n, net_bp FROM res_ai_scorecard WHERE \\"window\\"='all' AND horizon='5m' AND slice_dim='overall'")

``python scripts/self_aware_client.py`` prints a one-screen summary; ``--json`` prints health.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:9021"


class SelfAwareError(RuntimeError):
    pass


def get(path: str, timeout: float = 30.0, **params: Any) -> dict[str, Any]:
    q = {k: v for k, v in params.items() if v is not None}
    url = BASE + path + (("?" + urllib.parse.urlencode(q)) if q else "")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SelfAwareError(f"{exc.code} {path}: {body[:300]}") from exc
    except OSError as exc:
        raise SelfAwareError(f"self-aware daemon not reachable at {BASE} ({exc}); see docs/SELF_AWARE_RUNBOOK.md") from exc


def health() -> dict[str, Any]:
    return get("/api/selfaware/health")


def problems(report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [f for f in (report or health()).get("findings") or [] if f.get("severity") in ("RED", "AMBER")]


def uptime() -> dict[str, Any]:
    return get("/api/selfaware/uptime")


def digest(history: int = 0) -> dict[str, Any]:
    return get("/api/selfaware/digest", history=history or None)


def ai_calls(**filters: Any) -> dict[str, Any]:
    return get("/api/selfaware/ai/calls", **filters)


def ai_call(call_id: str) -> dict[str, Any]:
    return get("/api/selfaware/ai/calls/" + urllib.parse.quote(call_id, safe=""))


def ai_scorecard(**filters: Any) -> dict[str, Any]:
    return get("/api/selfaware/ai/scorecard", **filters)


def edges(status: str | None = None) -> dict[str, Any]:
    return get("/api/selfaware/edges", status=status)


def playbook() -> dict[str, Any]:
    return get("/api/selfaware/edges/playbook")


def tiles(window: str = "all") -> dict[str, Any]:
    return get("/api/selfaware/tiles", window=window)


def receipts() -> dict[str, Any]:
    return get("/api/selfaware/receipts")


def tables() -> dict[str, Any]:
    return get("/api/selfaware/tables")


def query(sql: str, max_rows: int = 1000) -> dict[str, Any]:
    return get("/api/selfaware/query", timeout=60.0, sql=sql, max_rows=max_rows)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    h = health()
    if "--json" in argv:
        print(json.dumps(h, indent=2))
        return 0
    print(f"{h.get('verdict')} at {h.get('generated_at')} (counts {h.get('counts')})")
    for f in problems(h):
        cause = "; ".join(c.get("text", "") for c in (f.get("causes") or [])[:2])
        print(f"  {f['severity']:<5} {f['id']}: {f['observed']}\n        -> {cause}  [{f.get('runbook')}]")
    try:
        print("digest:", (digest().get("latest") or {}).get("headline"))
    except SelfAwareError as exc:
        print("digest unavailable:", exc)
    return {"GREEN": 0, "AMBER": 1}.get(h.get("verdict"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
