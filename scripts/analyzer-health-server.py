#!/usr/bin/env python3
"""Research analyzer dashboard on :9001 — KPIs, bot mirror, health probe."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("ANALYZER_HEALTH_PORT", "9001"))
BIND_HOST = os.environ.get("ANALYZER_BIND_HOST", "0.0.0.0")
BOT_DASHBOARD = os.environ.get("HOME_BOT_DASHBOARD", "http://127.0.0.1:7800").rstrip("/")
AGENT_DIR = Path(os.environ.get("BTC_AGENT_DIR", "")).resolve() if os.environ.get("BTC_AGENT_DIR") else None
if AGENT_DIR is None or not AGENT_DIR.is_dir():
    repo_root = Path(__file__).resolve().parent.parent
    AGENT_DIR = repo_root / "services" / "btc-conservative-agent"

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Doxed Research Analyzer · :9001</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #0a0a0c; color: #e4e4e7; }
    header { padding: 1rem 1.25rem; border-bottom: 1px solid #27272a; background: #111114; }
    h1 { margin: 0; font-size: 1.1rem; }
    .sub { color: #71717a; font-size: 0.8rem; margin-top: 0.35rem; }
    main { padding: 1rem 1.25rem 2rem; display: grid; gap: 1rem; }
    .grid { display: grid; gap: 0.75rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .card { background: #141417; border: 1px solid #27272a; border-radius: 10px; padding: 0.85rem 1rem; }
    .card h2 { margin: 0 0 0.5rem; font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; color: #a78bfa; }
    .val { font-size: 1.35rem; font-weight: 700; }
    .muted { color: #71717a; font-size: 0.78rem; }
    .ok { color: #34d399; }
    .bad { color: #f87171; }
    .warn { color: #fbbf24; }
    table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
    th, td { text-align: left; padding: 0.35rem 0.4rem; border-bottom: 1px solid #27272a; }
    a { color: #a78bfa; }
    pre { white-space: pre-wrap; word-break: break-word; font-size: 0.72rem; color: #a1a1aa; max-height: 180px; overflow: auto; }
    .pill { display: inline-block; padding: 0.15rem 0.45rem; border-radius: 999px; font-size: 0.68rem; border: 1px solid #3f3f46; }
  </style>
</head>
<body>
  <header>
    <h1>Doxed BTC Research Analyzer</h1>
    <div class="sub">Local dashboard · polls bot <a id="bot-link" href="#">:7800</a> · research loop logs in PowerShell window</div>
  </header>
  <main>
    <div class="grid" id="stats"></div>
    <div class="card"><h2>False reject & shadow scorecard</h2><div id="kpis" class="muted">Loading…</div></div>
    <div class="card"><h2>Live bot snapshot</h2><pre id="bot-state">Loading…</pre></div>
    <div class="card"><h2>Research files</h2><table><thead><tr><th>File</th><th>Size</th><th>Modified</th></tr></thead><tbody id="files"></tbody></table></div>
  </main>
  <script>
    const botLink = document.getElementById('bot-link');
    botLink.href = '__BOT_URL__';
    botLink.textContent = '__BOT_URL__';
    function fmtUsd(n) {
      if (n == null || Number.isNaN(Number(n))) return '—';
      const v = Number(n);
      return (v >= 0 ? '$' : '-$') + Math.abs(v).toFixed(2);
    }
    function pill(ok, on, off) {
      return `<span class="pill ${ok ? 'ok' : 'bad'}">${ok ? on : off}</span>`;
    }
    async function refresh() {
      try {
        const res = await fetch('/api/dashboard', { cache: 'no-store' });
        const data = await res.json();
        const bot = data.bot || {};
        const kpis = data.research_kpis || {};
        const fr = kpis.false_reject || {};
        const sc = kpis.ai_shadow_scorecard || {};
        document.getElementById('stats').innerHTML = `
          <div class="card"><h2>Bot</h2><div class="val">${pill(data.bot_online, 'ONLINE', 'OFFLINE')}</div>
            <div class="muted">${bot.price != null ? 'BTC $' + Number(bot.price).toLocaleString() : 'Start bot on :7800'}</div></div>
          <div class="card"><h2>Balance</h2><div class="val">${fmtUsd(bot.account_balance ?? bot.equity)}</div>
            <div class="muted">Session PnL ${fmtUsd(bot.daily_pnl_usd)}</div></div>
          <div class="card"><h2>Execution</h2><div class="val ${bot.execution_paused ? 'warn' : 'ok'}">${bot.execution_paused ? 'PAUSED' : 'ACTIVE'}</div>
            <div class="muted">${bot.execution_reason || bot.strategy_mode || '—'}</div></div>
          <div class="card"><h2>False reject rate</h2><div class="val">${fr.false_reject_rate_pct ?? '—'}%</div>
            <div class="muted">Missed ${fmtUsd(fr.missed_profit_usd)}</div></div>
          <div class="card"><h2>Positions</h2><div class="val">${(bot.positions || []).length}</div>
            <div class="muted">Orders ${(bot.orders || []).length}</div></div>
          <div class="card"><h2>Updated</h2><div class="val" style="font-size:0.85rem">${data.ts || '—'}</div></div>`;
        document.getElementById('kpis').innerHTML = `
          AI rejects: ${sc.ai_rejects_tracked ?? 0} · Missed winners: ${sc.missed_winners ?? 0} · Good blocks: ${sc.good_reject_blocks ?? 0}`;
        document.getElementById('bot-state').textContent = JSON.stringify(bot, null, 2);
        document.getElementById('files').innerHTML = (data.files || []).map(f =>
          `<tr><td>${f.name}</td><td>${f.size_kb} KB</td><td>${f.modified || '—'}</td></tr>`).join('');
      } catch (e) {
        document.getElementById('bot-state').textContent = 'Dashboard fetch failed: ' + e;
      }
    }
    refresh();
    setInterval(refresh, 8000);
  </script>
</body>
</html>"""


def _fetch_json(url: str, timeout: float = 4.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read(2_000_000).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def _slim_bot(raw: dict | None, health: dict | None) -> dict:
    raw = raw or {}
    health = health or {}
    return {
        "price": raw.get("price"),
        "account_balance": raw.get("account_balance"),
        "equity": raw.get("equity"),
        "daily_pnl_usd": raw.get("daily_pnl_usd"),
        "execution_paused": raw.get("execution_paused", health.get("execution_paused")),
        "execution_reason": raw.get("execution_reason", health.get("execution_reason")),
        "strategy_mode": raw.get("strategy_mode"),
        "trade_count_session": raw.get("trade_count_session"),
        "positions": raw.get("positions") or [],
        "orders": raw.get("orders") or [],
        "last_ai": raw.get("last_ai"),
        "debug_state": raw.get("debug_state"),
        "status": health.get("status"),
    }


def _research_files() -> list[dict]:
    names = [
        "trades_3factor.csv",
        "blocked_signals_3factor.csv",
        "decisions_3factor.csv",
        "trade_lifecycle.jsonl",
        "shadow_outcome.jsonl",
        "ai_confidence_calibration.jsonl",
    ]
    rows = []
    for name in names:
        path = AGENT_DIR / name
        if not path.is_file():
            continue
        stat = path.stat()
        rows.append(
            {
                "name": name,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            }
        )
    return rows


def _research_kpis() -> dict:
    cwd = str(AGENT_DIR)
    prev = os.getcwd()
    try:
        os.chdir(cwd)
        if str(AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(AGENT_DIR))
        from research_kpi_engine import build_ai_shadow_scorecard, build_false_reject_kpi

        return {
            "false_reject": build_false_reject_kpi(cwd),
            "ai_shadow_scorecard": build_ai_shadow_scorecard(cwd),
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}
    finally:
        os.chdir(prev)


_cache_lock = threading.Lock()
_cache: dict = {
    "bot": {},
    "bot_online": False,
    "kpis": {},
    "files": [],
    "updated_at": None,
}


def _refresh_loop() -> None:
    while True:
        try:
            health = _fetch_json(f"{BOT_DASHBOARD}/health", timeout=3.0) or {}
            bot_online = bool(health)
            full = _fetch_json(f"{BOT_DASHBOARD}/api/state", timeout=30.0) if bot_online else None
            payload = {
                "bot": _slim_bot(full, health),
                "bot_online": bot_online or bool(full),
                "kpis": _research_kpis(),
                "files": _research_files(),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            with _cache_lock:
                _cache.update(payload)
        except Exception:
            pass
        time.sleep(20)


def build_dashboard_payload() -> dict:
    with _cache_lock:
        return {
            "ok": True,
            "service": "btc-research-analyzer",
            "bot_dashboard": BOT_DASHBOARD,
            "bot_online": _cache["bot_online"],
            "analyzer_loop": os.environ.get("ANALYZER_LOOP_RUNNING") == "1",
            "agent_dir": str(AGENT_DIR),
            "bot": dict(_cache["bot"]),
            "research_kpis": dict(_cache["kpis"]),
            "files": list(_cache["files"]),
            "ts": _cache["updated_at"],
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/status"):
            payload = {
                "ok": True,
                "service": "btc-research-analyzer",
                "dashboard": f"http://127.0.0.1:{PORT}/",
                "bot_dashboard": BOT_DASHBOARD,
            }
            self._send(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        if path == "/api/dashboard":
            self._send(json.dumps(build_dashboard_payload()).encode("utf-8"), "application/json; charset=utf-8")
            return
        if path in ("/", "/index.html"):
            html = HTML_PAGE.replace("__BOT_URL__", BOT_DASHBOARD)
            self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            return
        self._send(json.dumps({"ok": False, "error": "not found"}).encode("utf-8"), "application/json", 404)


def main() -> None:
    threading.Thread(target=_refresh_loop, daemon=True).start()
    server = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    lan = f"http://10.0.0.102:{PORT}/" if BIND_HOST in ("0.0.0.0", "::") else f"http://127.0.0.1:{PORT}/"
    print(
        f"Analyzer dashboard http://127.0.0.1:{PORT}/  LAN: {lan}  (health: /health, data dir: {AGENT_DIR})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
    sys.exit(0)
