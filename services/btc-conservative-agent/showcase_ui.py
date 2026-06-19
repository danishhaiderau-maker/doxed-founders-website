#!/usr/bin/env python3
"""Execution-mirror dashboard for doxxedcrypto.digital — no research UI."""
from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Response, jsonify, render_template_string

# Research endpoints blocked on the public execution mirror.
BLOCKED_ROUTES = frozenset({
    "/api/toggle_fresh_collection",
    "/api/export_csv",
    "/api/export_debug",
    "/api/download_debug_config",
    "/api/toggle_continuous_ai_research",
    "/api/toggle_research_lane",
    "/api/toggle_continuous_ai_direct",
    "/api/reset",
})

SHOWCASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>BTC Conservative Agent — Execution Mirror</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #0d1117; color: #e6edf3; }
    .wrap { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
    h1 { font-size: 1.35rem; margin: 0 0 4px; }
    .sub { color: #8b949e; margin: 0 0 20px; font-size: 0.9rem; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75rem;
      background: #238636; color: #fff; margin-left: 8px; vertical-align: middle; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; }
    .card h2 { margin: 0 0 8px; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .04em; color: #8b949e; }
    .val { font-size: 1.1rem; font-weight: 600; word-break: break-word; }
    .muted { color: #8b949e; font-size: 0.85rem; margin-top: 16px; }
    a { color: #58a6ff; }
    .err { color: #f85149; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>BTC Conservative Agent <span class="badge" id="statusBadge">…</span></h1>
    <p class="sub">Execution mirror for <a href="https://doxxedcrypto.digital/agent-hub/conservative-btc">doxxedcrypto.digital</a> — signal engine only, no research tooling.</p>
    <div class="grid">
      <div class="card"><h2>Signal engine</h2><div class="val" id="engineVersion">—</div></div>
      <div class="card"><h2>Runtime mode</h2><div class="val" id="runtimeMode">EXECUTION_MIRROR</div></div>
      <div class="card"><h2>Price</h2><div class="val" id="price">—</div></div>
      <div class="card"><h2>Regime</h2><div class="val" id="regime">—</div></div>
      <div class="card"><h2>Edge</h2><div class="val" id="edge">—</div></div>
      <div class="card"><h2>AI decision</h2><div class="val" id="aiDecision">—</div></div>
      <div class="card"><h2>Execution</h2><div class="val" id="execution">—</div></div>
      <div class="card"><h2>Benchmark lane</h2><div class="val" id="benchmarkLane">COMBO_65_SP5_CHASE_3PLUS</div></div>
    </div>
    <p class="muted" id="syncLine">Sync: —</p>
    <p class="muted" id="err" class="err"></p>
  </div>
  <script>
    async function refresh() {
      try {
        const r = await fetch('/api/state');
        const s = await r.json();
        document.getElementById('engineVersion').textContent = s.bot_version || '—';
        document.getElementById('runtimeMode').textContent = s.runtime_mode || 'EXECUTION_MIRROR';
        document.getElementById('price').textContent = s.price != null ? '$' + Number(s.price).toLocaleString() : '—';
        document.getElementById('regime').textContent = s.regime || '—';
        const edge = s.last_edge ?? s.debug_state?.last_edge_score;
        const req = s.edge_threshold ?? s.effective_threshold;
        document.getElementById('edge').textContent = (edge != null && req != null) ? edge + ' / ' + req : '—';
        const ai = s.last_ai || {};
        document.getElementById('aiDecision').textContent = (ai.decision || ai.final_direction || 'WAIT') +
          (ai.win_prob != null ? ' (' + ai.win_prob + '%)' : '');
        document.getElementById('execution').textContent = (s.execution_paused ? 'PAUSED' : 'ACTIVE') +
          (s.execution_reason ? ' — ' + s.execution_reason : '');
        document.getElementById('statusBadge').textContent = s.execution_paused ? 'PAUSED' : 'LIVE';
        document.getElementById('syncLine').textContent =
          'showcase_execution_only=' + s.showcase_execution_only + ' · research_collection=' + s.research_data_collection +
          ' · ws=' + (s.diag?.ws_status || s.data_source || '—');
        document.getElementById('err').textContent = '';
      } catch (e) {
        document.getElementById('err').textContent = 'State fetch failed: ' + e;
      }
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>"""


def _blocked_handler():
    return jsonify({"error": "Not available on execution mirror", "runtime_mode": "EXECUTION_MIRROR"}), 404


def register_showcase_ui(app) -> None:
    """Replace research dashboard and block research-only API routes."""

    def showcase_dashboard():
        return render_template_string(SHOWCASE_HTML)

    app.view_functions["dashboard"] = showcase_dashboard

    for rule in list(app.url_map.iter_rules()):
        if rule.rule in BLOCKED_ROUTES:
            app.view_functions[rule.endpoint] = _blocked_handler


def load_manifest_versions() -> dict:
    """Load signal engine manifest for /api/state enrichment."""
    manifest_path = Path(__file__).resolve().parent.parent / "btc-signal-engine" / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        with manifest_path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
