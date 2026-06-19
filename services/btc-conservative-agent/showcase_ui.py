#!/usr/bin/env python3
"""Execution-mirror dashboard for doxxedcrypto.digital — operator visibility, no research UI."""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Response, jsonify, render_template_string, request

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
    .wrap { max-width: 1100px; margin: 0 auto; padding: 20px 16px 48px; }
    h1 { font-size: 1.35rem; margin: 0 0 4px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
    .sub { color: #8b949e; margin: 0 0 16px; font-size: 0.88rem; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.72rem;
      background: #238636; color: #fff; font-weight: 600; }
    .badge.paused { background: #9e6a03; }
    .badge.err { background: #da3633; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; align-items: center; }
    button.btn { background: #21262d; border: 1px solid #30363d; color: #e6edf3; border-radius: 6px;
      padding: 8px 14px; cursor: pointer; font-size: 0.85rem; }
    button.btn:hover { background: #30363d; }
    button.btn.danger { border-color: #f8514966; color: #f85149; }
    button.btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 12px; }
    .grid.wide { grid-template-columns: 1fr; }
    .section { margin-bottom: 20px; }
    .section-title { font-size: 0.72rem; text-transform: uppercase; letter-spacing: .06em;
      color: #8b949e; margin: 0 0 8px; font-weight: 600; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; }
    .card h3 { margin: 0 0 10px; font-size: 0.72rem; text-transform: uppercase; letter-spacing: .04em; color: #8b949e; }
    .row { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; font-size: 0.88rem; }
    .row .k { color: #8b949e; }
    .row .v { text-align: right; font-weight: 500; word-break: break-word; max-width: 62%; }
    .val-lg { font-size: 1.25rem; font-weight: 700; }
    .pos { color: #3fb950; }
    .neg { color: #f85149; }
    .muted { color: #8b949e; font-size: 0.82rem; }
    .pipeline { list-style: none; margin: 0; padding: 0; }
    .pipeline li { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #21262d; font-size: 0.88rem; }
    .pipeline li:last-child { border-bottom: none; font-weight: 600; margin-top: 4px; }
    .ok { color: #3fb950; }
    .fail { color: #f85149; }
    .wait { color: #8b949e; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
    th { color: #8b949e; font-weight: 500; }
    a { color: #58a6ff; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .err-banner { color: #f85149; margin-top: 8px; font-size: 0.85rem; }
    .ai-summary { margin-top: 8px; padding: 10px; background: #0d1117; border-radius: 6px; font-size: 0.85rem; line-height: 1.45; color: #c9d1d9; }
    .sync-ok { color: #3fb950; }
    .sync-warn { color: #d29922; }
    .hero-order { border: 2px solid #388bfd; background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); }
    .hero-order h2 { margin: 0 0 12px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .08em; color: #58a6ff; }
    .hero-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
    .hero-grid .lbl { font-size: 0.72rem; color: #8b949e; text-transform: uppercase; }
    .hero-grid .num { font-size: 1.15rem; font-weight: 700; margin-top: 4px; }
    .reject-box { margin-top: 10px; padding: 10px; border-radius: 6px; background: #1c1214; border: 1px solid #f8514944; }
    .reject-box h4 { margin: 0 0 8px; font-size: 0.72rem; color: #f85149; text-transform: uppercase; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>BTC Conservative Agent <span class="badge" id="statusBadge">…</span></h1>
    <p class="sub">Execution mirror for <a href="https://doxxedcrypto.digital/agent-hub/conservative-btc">doxxedcrypto.digital</a> — live decision visibility, no research telemetry.</p>

    <div class="toolbar">
      <button class="btn danger" id="freshStartBtn" onclick="freshStart()">Fresh start ($500)</button>
      <span class="muted" id="lastReset">—</span>
      <span class="muted" id="refreshTs">—</span>
    </div>

    <div class="section">
      <div class="section-title">Pending limit order</div>
      <div class="card hero-order" id="pendingHero"><span class="muted">No pending limit — waiting for signal</span></div>
    </div>

    <div class="section">
      <div class="section-title">Account summary</div>
      <div class="grid">
        <div class="card"><h3>Balance</h3><div class="val-lg" id="balance">—</div></div>
        <div class="card"><h3>Equity</h3><div class="val-lg" id="equity">—</div></div>
        <div class="card"><h3>Daily PnL</h3><div class="val-lg" id="dailyPnl">—</div></div>
        <div class="card"><h3>Open PnL</h3><div class="val-lg" id="openPnl">—</div></div>
        <div class="card"><h3>Closed (session)</h3><div class="val-lg" id="closedPnl">—</div></div>
        <div class="card"><h3>Price</h3><div class="val-lg" id="price">—</div><div class="muted" id="priceMeta">—</div></div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Active strategy</div>
      <div class="grid">
        <div class="card" id="strategyCard">—</div>
        <div class="card" id="syncCard">—</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Execution state</div>
      <div class="grid">
        <div class="card" id="execCard">—</div>
        <div class="card">
          <h3>AI snapshot</h3>
          <div id="aiSnapshot">—</div>
          <div class="ai-summary" id="aiSummary"></div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Signal pipeline</div>
      <div class="card"><ul class="pipeline" id="pipeline">—</ul></div>
    </div>

    <div class="section">
      <div class="section-title">Open orders</div>
      <div class="card"><div id="ordersTable"><span class="muted">No pending limits</span></div></div>
    </div>

    <div class="section">
      <div class="section-title">Open positions</div>
      <div class="card"><div id="positionsTable"><span class="muted">Flat</span></div></div>
    </div>

    <div class="section">
      <div class="section-title">AI history (last 5)</div>
      <div class="card"><div id="aiHistory">—</div></div>
    </div>

    <p class="err-banner" id="err"></p>
  </div>
  <script>
    const LIMIT_TTL_SEC = 30 * 60;

    function fmtUsd(n) {
      if (n == null || isNaN(n)) return '—';
      const s = Number(n).toFixed(2);
      return (n >= 0 ? '$' : '-$') + Math.abs(Number(n)).toFixed(2);
    }
    function fmtPnl(n) {
      if (n == null || isNaN(n)) return '—';
      const el = document.createElement('span');
      el.textContent = fmtUsd(n);
      el.className = n >= 0 ? 'pos' : 'neg';
      return el.outerHTML;
    }
    function fmtAge(sec) {
      if (sec == null || sec < 0) return '—';
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return m + 'm ' + s + 's';
    }
    function esc(s) {
      if (s == null) return '';
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function rows(obj) {
      return Object.entries(obj).map(([k,v]) =>
        '<div class="row"><span class="k">' + esc(k) + '</span><span class="v">' + v + '</span></div>'
      ).join('');
    }

    function findBenchmarkLane(specs) {
      const lanes = (specs && specs.lanes) || [];
      const bench = specs && specs.benchmark_lane;
      return lanes.find(l => l.lane === bench || l.is_benchmark) || lanes[0] || null;
    }

    function buildPipeline(s) {
      const dbg = s.debug_state || {};
      const la = s.last_ai || {};
      const gs = s.golden_stack_config || {};
      const disp = s.display_skip_block || {};
      const edgeComp = dbg.edge_components || {};
      const spread = edgeComp.directional_spread ?? dbg.directional_spread ?? la.spread;
      const spreadMin = gs.spread_min ?? 5;

      const priceOk = s.price != null && (s.price_age == null || s.price_age < 180);
      const regimeOk = s.regime && s.regime !== 'UNKNOWN';
      const spreadOk = spread != null && Number(spread) >= Number(spreadMin);
      const aiCalled = (dbg.ai_gate || {}).called;
      const aiDec = la.decision || (s.display_ai || {}).status;
      const aiPass = ['APPROVE','STRONG_APPROVE','SOFT_APPROVE'].includes(aiDec);
      const lane = la.research_lane || (s.display_ai || {}).research_lane;
      const comboOk = lane && String(lane).startsWith('COMBO_');

      let final = 'WAIT';
      if (aiDec === 'REJECT') final = 'REJECT';
      else if (aiPass && comboOk) final = 'ACCEPT';
      else if (disp.block && disp.block !== '-') final = 'REJECT';
      else if (!priceOk) final = 'WAIT';
      else if (!regimeOk) final = 'WAIT';

      const mark = (ok, pending) => ok ? '<span class="ok">✓</span>' : (pending ? '<span class="wait">…</span>' : '<span class="fail">✗</span>');

      return [
        ['Market data', mark(priceOk, !priceOk && s.price == null)],
        ['Regime check', mark(regimeOk, s.regime === 'UNKNOWN')],
        ['Spread check', mark(spreadOk, spread == null) + (spread != null ? ' (' + spread + ' / ' + spreadMin + '+)' : '')],
        ['AI check', mark(aiPass, !aiCalled) + (aiDec ? ' ' + aiDec + (la.win_prob != null ? ' ' + la.win_prob + '%' : '') : '')],
        ['Combo match', mark(comboOk, !comboOk && aiPass) + (lane ? ' ' + lane : '')],
        ['Final', '<span class="' + (final === 'ACCEPT' ? 'ok' : final === 'REJECT' ? 'fail' : 'wait') + '">' + final + '</span>'],
      ];
    }

    function pickPending(s) {
      const orders = s.orders || [];
      if (orders.length) return orders[0];
      const sigs = (s.signal_info && s.signal_info.signals) || [];
      return sigs.find(x => x.status === 'ORDERED' || x.status === 'PENDING') || sigs[0] || null;
    }

    function renderPendingHero(s) {
      const p = pickPending(s);
      if (!p) return '<span class="muted">No pending limit — waiting for signal</span>';
      const side = (p.side || p.dir || p.final_direction || '—').toString().toUpperCase();
      const ageSec = p.ttl_remaining != null
        ? null
        : ((p.age_min || p.age / 60 || 0) * 60);
      const ttlLeft = p.ttl_remaining != null
        ? Math.max(0, Number(p.ttl_remaining))
        : Math.max(0, LIMIT_TTL_SEC - (ageSec || 0));
      const chase = p.limit_chase_count ?? p.chase_3plus_virtual_chase_count ?? 0;
      const qty = p.qty != null ? Number(p.qty).toFixed(4) : '—';
      const price = p.limit_price != null ? '$' + Number(p.limit_price).toLocaleString() : '—';
      const ageLabel = p.age_min != null ? fmtAge(p.age_min * 60) : (p.age != null ? fmtAge(p.age) : '—');
      return '<h2>Pending limit order</h2><div class="hero-grid">' +
        '<div><div class="lbl">Side</div><div class="num">' + esc(side) + '</div></div>' +
        '<div><div class="lbl">Price</div><div class="num">' + price + '</div></div>' +
        '<div><div class="lbl">Size</div><div class="num">' + qty + ' BTC</div></div>' +
        '<div><div class="lbl">Age</div><div class="num">' + ageLabel + '</div></div>' +
        '<div><div class="lbl">Chase</div><div class="num">' + chase + '</div></div>' +
        '<div><div class="lbl">TTL remaining</div><div class="num">' + fmtAge(ttlLeft) + '</div></div>' +
        '</div><p class="muted" style="margin-top:10px">Lane: ' + esc(p.research_lane || '—') + '</p>';
    }

    function buildAiRejectDetail(s) {
      const la = s.last_ai || {};
      const dbg = s.debug_state || {};
      const thr = s.ai_threshold ?? 65;
      if (la.decision !== 'REJECT' && la.decision !== 'AI_REJECT') return '';
      const prob = la.win_prob != null ? la.win_prob + '%' : '—';
      const struct = dbg.edge_components?.structure_score ?? dbg.last_edge_score ?? '—';
      const spread = dbg.edge_components?.directional_spread ?? dbg.directional_spread ?? '—';
      const parts = [
        'Confidence: ' + prob,
        'Required: ' + thr + '%',
        'Structure edge: ' + struct,
        'Spread: ' + spread,
      ];
      const ctx = la.comment || la.reason || (s.display_ai && s.display_ai.note) || '';
      return '<div class="reject-box"><h4>AI rejected because</h4><ul style="margin:0;padding-left:18px;line-height:1.6">' +
        parts.map(x => '<li>' + esc(x) + '</li>').join('') +
        (ctx ? '<li>' + esc(String(ctx).slice(0, 280)) + '</li>' : '') +
        '</ul></div>';
    }

    function rejectionReason(s) {
      const la = s.last_ai || {};
      const disp = s.display_skip_block || {};
      const dbg = s.debug_state || {};
      if (la.decision === 'REJECT' && la.win_prob != null) {
        const thr = s.ai_threshold ?? 65;
        return 'AI ' + la.win_prob + '% &lt; ' + thr + '%';
      }
      if (disp.block && disp.block !== '-') return esc(disp.block);
      if (dbg.last_block_reason) return esc(dbg.last_block_reason);
      if (dbg.skip_reason) return esc(dbg.skip_reason);
      if ((s.display_ai || {}).note) return esc((s.display_ai.note).slice(0, 200));
      return '—';
    }

    function renderOrders(orders) {
      if (!orders || !orders.length) return '<span class="muted">No pending limits</span>';
      let html = '<table><thead><tr><th>Side</th><th>Limit</th><th>Qty</th><th>Age</th><th>TTL left</th><th>Lane</th></tr></thead><tbody>';
      for (const o of orders) {
        const ageSec = (o.age_min || 0) * 60;
        const ttlLeft = o.ttl_remaining != null
          ? Math.max(0, Number(o.ttl_remaining))
          : Math.max(0, LIMIT_TTL_SEC - ageSec);
        html += '<tr><td>' + esc(o.side || o.dir || '—') + '</td><td>' +
          (o.limit_price != null ? '$' + Number(o.limit_price).toLocaleString() : '—') + '</td><td>' +
          (o.qty != null ? Number(o.qty).toFixed(4) : '—') + '</td><td>' + fmtAge(ageSec) + '</td><td>' +
          fmtAge(ttlLeft) + '</td><td>' + esc(o.research_lane || '—') + '</td></tr>';
      }
      return html + '</tbody></table>';
    }

    function renderPositions(positions) {
      if (!positions || !positions.length) return '<span class="muted">Flat</span>';
      let html = '<table><thead><tr><th>Side</th><th>Entry</th><th>Mark</th><th>PnL</th><th>Age</th></tr></thead><tbody>';
      for (const p of positions) {
        const age = p.open_ts ? fmtAge(Date.now()/1000 - p.open_ts) : '—';
        html += '<tr><td>' + esc(p.side || p.dir) + '</td><td>$' + Number(p.entry).toLocaleString() + '</td><td>$' +
          Number(p.current_price || p.entry).toLocaleString() + '</td><td>' + fmtPnl(p.unreal_usd) + '</td><td>' + age + '</td></tr>';
      }
      return html + '</tbody></table>';
    }

    function renderAiHistory(hist) {
      const rows = (hist || []).slice(0, 5);
      if (!rows.length) return '<span class="muted">No AI calls yet</span>';
      let html = '<table><thead><tr><th>Time</th><th>Dir</th><th>Prob</th><th>Decision</th></tr></thead><tbody>';
      for (const h of rows) {
        const ts = h.ts ? h.ts.slice(11, 16) : '—';
        html += '<tr><td>' + ts + '</td><td>' + esc(h.direction) + '</td><td>' +
          (h.win_prob != null ? h.win_prob + '%' : '—') + '</td><td>' + esc(h.decision) + '</td></tr>';
      }
      return html + '</tbody></table>';
    }

    function closedSessionPnl(trades) {
      if (!trades || !trades.length) return 0;
      return trades.reduce((sum, t) => sum + Number(t.net_pnl_usd || t.pnl_usd || 0), 0);
    }

    async function refresh() {
      try {
        const [stateRes, syncRes] = await Promise.all([
          fetch('/api/state'),
          fetch('/api/sync_status'),
        ]);
        const s = await stateRes.json();
        const sync = await syncRes.json();

        const balance = s.account_balance;
        const equity = s.equity;
        const openPnl = (balance != null && equity != null) ? equity - balance : null;
        const closed = closedSessionPnl(s.trades);

        document.getElementById('balance').textContent = fmtUsd(balance);
        document.getElementById('equity').textContent = fmtUsd(equity);
        document.getElementById('dailyPnl').innerHTML = fmtPnl(s.daily_pnl_usd);
        document.getElementById('openPnl').innerHTML = fmtPnl(openPnl);
        document.getElementById('closedPnl').innerHTML = fmtPnl(closed);
        document.getElementById('price').textContent = s.price != null ? '$' + Number(s.price).toLocaleString() : '—';
        document.getElementById('priceMeta').textContent =
          (s.regime || '—') + ' · ws ' + (s.diag && s.diag.ws_status ? s.diag.ws_status : (s.data_source || '—'));

        document.getElementById('pendingHero').innerHTML = renderPendingHero(s);

        const laneSpec = findBenchmarkLane(s.pathway_lane_specs);
        const entry = laneSpec && laneSpec.entry ? laneSpec.entry : {};
        const exit = laneSpec && laneSpec.exit ? laneSpec.exit : {};
        document.getElementById('strategyCard').innerHTML = rows({
          'Lane': esc(laneSpec ? laneSpec.lane : (s.pathway_lane_specs && s.pathway_lane_specs.benchmark_lane) || 'COMBO_65_SP5_CHASE_3PLUS'),
          'AI threshold': entry.filters ? entry.filters.ai_probability_bucket || '65+' : '65+',
          'Spread threshold': entry.filters ? entry.filters.directional_spread_bucket || '5+' : '5+',
          'Entry mode': laneSpec ? (laneSpec.entry_mode_label || 'Chase 3+') : 'Chase 3+',
          'Exit policy': exit.profile ? exit.profile + ' v4' : 'Scenario C v4',
          'Signal engine': esc(s.bot_version || '—'),
        });

        const parityCls = sync.parity_ok ? 'sync-ok' : 'sync-warn';
        document.getElementById('syncCard').innerHTML = rows({
          'Research source': esc(sync.research_source || '—'),
          'Showcase version': esc(sync.showcase_version || s.bot_version || '—'),
          'Signal hash': esc(sync.signal_hash || '—'),
          'Parity': '<span class="' + parityCls + '">' + esc(sync.parity_status || '—') + '</span>',
          'Runtime mode': esc(s.runtime_mode || 'EXECUTION_MIRROR'),
        });

        const la = s.last_ai || {};
        const dir = la.direction || s.direction || s.signal_direction || 'FLAT';
        let sigStatus = 'WAIT';
        if (la.decision === 'REJECT') sigStatus = 'REJECT';
        else if (['APPROVE','STRONG_APPROVE','SOFT_APPROVE'].includes(la.decision)) sigStatus = 'ACCEPT';
        else if (s.execution_paused) sigStatus = 'PAUSED';

        const pending = (s.orders && s.orders.length) ? s.orders[0] : null;
        let pendingLine = 'NONE';
        if (pending) {
          pendingLine = (pending.side || pending.dir || '') + ' ' + (pending.qty != null ? Number(pending.qty).toFixed(4) : '') +
            ' BTC @ ' + (pending.limit_price != null ? '$' + Number(pending.limit_price).toLocaleString() : '—') +
            '<br><span class="muted">Age: ' + fmtAge((pending.age_min || 0) * 60) + '</span>';
        }

        document.getElementById('execCard').innerHTML = rows({
          'Current direction': esc(dir),
          'Signal status': '<span class="' + (sigStatus === 'ACCEPT' ? 'ok' : sigStatus === 'REJECT' ? 'fail' : 'wait') + '">' + sigStatus + '</span>',
          'Reason': rejectionReason(s),
          'Pending limit': pendingLine,
          'Execution': (s.execution_paused ? 'PAUSED' : 'ACTIVE') + (s.execution_reason ? ' — ' + esc(s.execution_reason) : ''),
        });

        document.getElementById('aiSnapshot').innerHTML = rows({
          'AI probability': la.win_prob != null ? la.win_prob + '%' : '—',
          'AI direction': esc(la.direction || '—'),
          'Decision': esc(la.decision || (s.display_ai && s.display_ai.status) || '—'),
        });
        const summary = la.comment || la.reason || (s.display_ai && s.display_ai.note) || '';
        document.getElementById('aiSummary').innerHTML = buildAiRejectDetail(s) +
          (summary && la.decision !== 'REJECT' ? '<div class="ai-summary">' + esc(summary.slice(0, 400)) + '</div>' : '');

        const pipe = buildPipeline(s);
        document.getElementById('pipeline').innerHTML = pipe.map(([k,v]) =>
          '<li><span>' + esc(k) + '</span><span>' + v + '</span></li>'
        ).join('');

        document.getElementById('ordersTable').innerHTML = renderOrders(s.orders);
        document.getElementById('positionsTable').innerHTML = renderPositions(s.positions);
        document.getElementById('aiHistory').innerHTML = renderAiHistory(s.ai_history);

        const badge = document.getElementById('statusBadge');
        badge.textContent = s.execution_paused ? 'PAUSED' : 'LIVE';
        badge.className = 'badge' + (s.execution_paused ? ' paused' : '');

        if (s.last_fresh_reset_ts) {
          document.getElementById('lastReset').textContent = 'Last fresh start: ' + new Date(s.last_fresh_reset_ts * 1000).toLocaleString();
        }
        document.getElementById('refreshTs').textContent = 'Updated ' + new Date().toLocaleTimeString();
        document.getElementById('err').textContent = '';
      } catch (e) {
        document.getElementById('err').textContent = 'State fetch failed: ' + e;
        document.getElementById('statusBadge').textContent = 'ERROR';
        document.getElementById('statusBadge').className = 'badge err';
      }
    }

    async function freshStart() {
      if (!confirm('Fresh start: reset balance to $500, clear trades/orders/PnL. Continue?')) return;
      const btn = document.getElementById('freshStartBtn');
      btn.disabled = true;
      try {
        const r = await fetch('/api/fresh_start', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'Reset failed');
        await refresh();
      } catch (e) {
        document.getElementById('err').textContent = 'Fresh start failed: ' + e;
      } finally {
        btn.disabled = false;
      }
    }

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>"""


def _blocked_handler():
    return jsonify({"error": "Not available on execution mirror", "runtime_mode": "EXECUTION_MIRROR"}), 404


def load_manifest_versions() -> dict:
    """Load signal engine manifest for sync status."""
    local = Path(__file__).resolve().parent / "manifest.json"
    manifest_path = local if local.is_file() else Path(__file__).resolve().parent.parent / "btc-signal-engine" / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        with manifest_path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_sync_status(bot_module=None) -> dict:
    manifest = load_manifest_versions()
    showcase_version = None
    if bot_module is not None:
        showcase_version = getattr(bot_module, "EXECUTION_FIX_VERSION", None)

    signal_hash = manifest.get("signal_hash")
    parity_ok = True
    parity_status = "PASS (manifest)"

    if bot_module is not None:
        try:
            from hashlib import sha256
            bot_path = Path(bot_module.__file__).resolve()
            bot_text = bot_path.read_text(encoding="utf-8")
            live_hash = sha256(bot_text.encode("utf-8")).hexdigest()[:12]
            engine_path = Path(__file__).resolve().parent.parent / "btc-signal-engine" / "engine.py"
            if engine_path.is_file():
                engine_hash = sha256(engine_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:12]
                parity_ok = live_hash == engine_hash == (signal_hash or live_hash)
                parity_status = "PASS" if parity_ok else f"DRIFT bot={live_hash} engine={engine_hash}"
        except Exception as exc:
            parity_status = f"UNKNOWN ({exc})"
            parity_ok = False

    return {
        "research_source": manifest.get("source", "bybit-15m-research-bot/bybit_bot.py"),
        "research_version": manifest.get("engine_version"),
        "showcase_version": showcase_version or manifest.get("engine_version"),
        "signal_hash": signal_hash,
        "parity_ok": parity_ok,
        "parity_status": parity_status,
        "manifest_updated_at": manifest.get("updated_at"),
    }


def perform_showcase_fresh_start(bot_module) -> dict:
    """Lightweight operator reset — memory + balance only, no research CSV archive."""
    bot_module.reset_runtime_state()
    bot_module.reset_session_risk_state()
    bot_module.bot_start_time = time.time()
    with bot_module.state_lock:
        bot_module.state["last_fresh_reset_ts"] = time.time()
        bot_module.state["last_fresh_reset_summary"] = "showcase fresh start (no research archive)"
        bot_module.state["bot_start_time"] = bot_module.bot_start_time
    if hasattr(bot_module, "_write_research_session"):
        bot_module._write_research_session(bot_module.bot_start_time)
    if hasattr(bot_module, "save_persistent_config"):
        bot_module.save_persistent_config()
    return {
        "ok": True,
        "account_balance": bot_module.STARTING_BALANCE,
        "ts": bot_module.utc_iso() if hasattr(bot_module, "utc_iso") else None,
    }


def register_showcase_ui(app, bot_module=None) -> None:
    """Replace research dashboard and block research-only API routes."""

    def showcase_dashboard():
        return render_template_string(SHOWCASE_HTML)

    app.view_functions["dashboard"] = showcase_dashboard

    for rule in list(app.url_map.iter_rules()):
        if rule.rule in BLOCKED_ROUTES:
            app.view_functions[rule.endpoint] = _blocked_handler

    @app.route("/api/sync_status")
    def api_sync_status():
        return jsonify(build_sync_status(bot_module))

    @app.route("/api/fresh_start", methods=["POST"])
    def api_fresh_start():
        if bot_module is None:
            return jsonify({"ok": False, "error": "bot module unavailable"}), 500
        try:
            result = perform_showcase_fresh_start(bot_module)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)[:300]}), 500
