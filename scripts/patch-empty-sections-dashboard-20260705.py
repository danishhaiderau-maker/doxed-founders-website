#!/usr/bin/env python3
"""Dashboard banners for empty Ladder / Isolation / Recovery sections."""
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent" / "research" / "research_dashboard.py"
text = TARGET.read_text(encoding="utf-8")

# chase-iso payload
old_iso = '''    return {
        "generated_at": rep.get("generated_at"),
        "verdict": rep.get("verdict"),
        "notes": rep.get("isolation_notes") or [],
        "continuous": direct,
        "urgent": chase,
        "primary_pair": primary,
        "pairs": rep.get("pairs") or [],
        "direct_lane": primary.get("direct_lane"),
        "chase_lane": primary.get("chase_lane"),
        "direct_label": primary.get("direct_label") or "Direct",
        "chase_label": primary.get("chase_label") or "Chase 3+",
        "global_fill_model": rep.get("global_fill_model") or {},
    }'''
new_iso = '''    return {
        "generated_at": rep.get("generated_at"),
        "verdict": rep.get("verdict"),
        "notes": rep.get("isolation_notes") or [],
        "continuous": direct,
        "urgent": chase,
        "primary_pair": primary,
        "primary_inactive": rep.get("primary_inactive"),
        "active_lanes": rep.get("active_lanes") or [],
        "pairs": rep.get("pairs") or [],
        "direct_lane": primary.get("direct_lane"),
        "chase_lane": primary.get("chase_lane"),
        "direct_label": primary.get("direct_label") or "Direct",
        "chase_label": primary.get("chase_label") or "Chase 3+",
        "global_fill_model": rep.get("global_fill_model") or {},
    }'''
if old_iso in text:
    text = text.replace(old_iso, new_iso, 1)

# horizon payload coverage_reason
if '"coverage_reason"' not in text.split("_horizon_payload")[1][:800]:
    text = text.replace(
        '        "note": rep.get("note"),\n    }',
        '        "note": rep.get("note"),\n        "coverage_reason": rep.get("coverage_reason"),\n    }',
        1,
    )

# ladder-sim payload fields
if '"data_status"' not in text.split("ladder-sim")[1][:1200]:
    text = text.replace(
        '        "disclaimer": rep.get("disclaimer"),\n        "best_profile_id": rep.get("best_profile_id"),',
        '        "disclaimer": rep.get("disclaimer"),\n        "data_status": rep.get("data_status"),\n        "empty_reason": rep.get("empty_reason"),\n        "best_profile_id": rep.get("best_profile_id"),',
        1,
    )

# loadChaseIso
old_chase_fn = '''async function loadChaseIso() {
  const r = await fetch('/api/chase-iso');
  const d = await r.json();
  const note = document.getElementById('chase-iso-note');
  if (note) note.textContent = `Verdict: ${d.verdict || 'n/a'} — COMBO Direct vs Chase 3+ per tile pair.`;
  const cont = d.continuous || {};
  const urg = d.urgent || {};
  const directH = document.getElementById('chase-iso-direct-h');
  const chaseH = document.getElementById('chase-iso-chase-h');
  if (directH) directH.textContent = d.direct_label || d.direct_lane || 'Direct';
  if (chaseH) chaseH.textContent = d.chase_label || d.chase_lane || 'Chase 3+';
  document.getElementById('chase-iso-kpis').innerHTML = [
    ['Verdict', d.verdict || 'n/a'],
    ['Direct EV', '$' + fmtUsd(cont.ev_usd)],
    ['Chase EV', '$' + fmtUsd(urg.ev_usd)],
    ['Global fill model', JSON.stringify(d.global_fill_model || {})],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('chase-iso-notes').innerHTML = (d.notes||[]).map(n => `<li>${n}</li>`).join('') || '<li>No isolation notes.</li>';
  const metrics = [
    ['Trades', cont.trades, urg.trades],
    ['Win rate %', cont.wr_pct, urg.wr_pct],
    ['PnL', '$' + fmtUsd(cont.pnl_usd), '$' + fmtUsd(urg.pnl_usd)],
    ['EV/trade', '$' + fmtUsd(cont.ev_usd), '$' + fmtUsd(urg.ev_usd)],
    ['Avg chase count', cont.avg_chase_count, urg.avg_chase_count],
    ['Avg signal age (s)', cont.avg_signal_age_sec, urg.avg_signal_age_sec],
    ['Chase policy', cont.chase_policy || '', urg.chase_policy || ''],
  ];
  document.getElementById('chase-iso-body').innerHTML = metrics.map(([m, c, u]) =>
    `<tr><td>${m}</td><td>${c ?? 'n/a'}</td><td>${u ?? 'n/a'}</td></tr>`).join('');
}'''

new_chase_fn = '''async function loadChaseIso() {
  const r = await fetch('/api/chase-iso');
  const d = await r.json();
  const note = document.getElementById('chase-iso-note');
  const cont = d.continuous || {};
  const urg = d.urgent || {};
  const inactive = d.primary_inactive || ((cont.trades||0) === 0 && (urg.trades||0) === 0);
  if (note) {
    note.textContent = inactive
      ? `COMBO tiles inactive this session — primary: ${d.direct_label || 'CONTINUOUS'} vs ${d.chase_label || 'AI60 SP3'}. Global fill_model counts all lanes.`
      : `Verdict: ${d.verdict || 'n/a'} — ${d.direct_label || 'Direct'} vs ${d.chase_label || 'Chase 3+'}.`;
    note.style.color = inactive ? 'var(--amber)' : '';
  }
  const directH = document.getElementById('chase-iso-direct-h');
  const chaseH = document.getElementById('chase-iso-chase-h');
  if (directH) directH.textContent = d.direct_label || d.direct_lane || 'Direct';
  if (chaseH) chaseH.textContent = d.chase_label || d.chase_lane || 'Chase 3+';
  document.getElementById('chase-iso-kpis').innerHTML = [
    ['Verdict', d.verdict || 'n/a'],
    ['Direct EV', '$' + fmtUsd(cont.ev_usd)],
    ['Chase EV', '$' + fmtUsd(urg.ev_usd)],
    ['Direct trades', cont.trades ?? 0],
    ['Chase trades', urg.trades ?? 0],
    ['Global fill model', JSON.stringify(d.global_fill_model || {})],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('chase-iso-notes').innerHTML = (d.notes||[]).map(n => `<li>${n}</li>`).join('') || '<li>No isolation notes.</li>';
  const metrics = [
    ['Trades', cont.trades, urg.trades],
    ['Win rate %', cont.wr_pct, urg.wr_pct],
    ['PnL', '$' + fmtUsd(cont.pnl_usd), '$' + fmtUsd(urg.pnl_usd)],
    ['EV/trade', '$' + fmtUsd(cont.ev_usd), '$' + fmtUsd(urg.ev_usd)],
    ['Avg chase count', cont.avg_chase_count, urg.avg_chase_count],
    ['Avg signal age (s)', cont.avg_signal_age_sec, urg.avg_signal_age_sec],
    ['Chase policy', cont.chase_policy || '', urg.chase_policy || ''],
  ];
  document.getElementById('chase-iso-body').innerHTML = metrics.map(([m, c, u]) =>
    `<tr><td>${m}</td><td>${c ?? 'n/a'}</td><td>${u ?? 'n/a'}</td></tr>`).join('');
}'''

if old_chase_fn in text:
    text = text.replace(old_chase_fn, new_chase_fn, 1)

# loadLadderSim — banner when no overlap
marker = "async function loadLadderSim()"
if marker in text and "data_status === 'NO_EXECUTED_REPLAY_OVERLAP'" not in text:
    start = text.index(marker)
    end = text.index("async function loadPathwayAudit()", start)
    new_ladder = '''async function loadLadderSim() {
  const r = await fetch('/api/ladder-sim');
  const d = await r.json();
  const disc = document.getElementById('ladder-sim-disclaimer');
  const noSim = !((d.profiles||[]).some(p => (p.trades_simulated||0) > 0));
  const overlapZero = d.data_status === 'NO_EXECUTED_REPLAY_OVERLAP' || ((d.replays_matched_executed ?? 0) === 0 && (d.actual_trades ?? 0) > 0);
  if (disc) {
    disc.textContent = d.empty_reason || d.disclaimer || '';
    disc.style.display = (overlapZero || d.disclaimer) ? '' : 'none';
  }
  document.getElementById('ladder-sim-kpis').innerHTML = [
    ['Actual PnL', '$' + fmtUsd(d.actual_realized_usd)],
    ['Executed trades', d.actual_trades ?? 0],
    ['Matched replays', d.replays_matched_executed ?? 0],
    ['Replays on disk', d.replays_available ?? 0],
    ['Best profile', d.best_profile_id || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  if (noSim && overlapZero) {
    document.getElementById('ladder-sim-body').innerHTML =
      `<tr class="amber"><td colspan="8">No executed-trade replay overlap — ladder sim requires bot v1.1.41+ post-exit tick collection on session cont-*/vc603-* fills. ${d.replays_available ?? 0} replays on disk are mostly prior-session shadow/scan paths.</td></tr>`;
    return;
  }
  document.getElementById('ladder-sim-body').innerHTML = (d.profiles||[]).map(p => {
    const delta = p.delta_vs_actual_usd;
    const cls = p.unrealistic_vs_actual ? 'red' : (delta != null && delta > 50 ? 'amber' : '');
    const unreal = p.unrealistic_vs_actual ? ' UNREALISTIC' : '';
    return `<tr class="${cls}"><td>${p.profile_id||''}${unreal}</td><td>${(p.ladder||[]).map(r=>r.join('\\u2192')).join(' · ')||p.label||''}</td><td>${p.trades_simulated||0}</td><td>$${fmtUsd(p.sum_pnl_usd)}</td><td>$${fmtUsd(p.avg_pnl_usd)}</td><td>${p.wr_pct??'n/a'}%</td><td>${p.ladder_exit_pct??'n/a'}%</td><td>${delta!=null?'$'+fmtUsd(delta):'n/a'}</td></tr>`;
  }).join('') || '<tr><td colspan="8">No ladder sim data — need executed-trade tick replays.</td></tr>';
}

'''
    text = text[:start] + new_ladder + text[end:]

# loadHorizon — coverage_reason
if "coverage_reason" not in text.split("async function loadHorizon")[1][:600]:
    old_hor = '''  if (note) {
    note.textContent = d.conclusions_allowed
      ? (d.note || 'Coverage sufficient for recovery conclusions.')
      : `⚠ Coverage ${d.max_horizon_coverage_pct ?? 0}% — recovery rates hidden until ≥${d.min_coverage_pct_for_conclusions ?? 80}%. ${d.note || ''}`;
    note.style.color = d.conclusions_allowed ? '' : 'var(--amber)';
  }'''
    new_hor = '''  if (note) {
    const reason = d.coverage_reason ? ` ${d.coverage_reason}` : '';
    note.textContent = d.conclusions_allowed
      ? (d.note || 'Coverage sufficient for recovery conclusions.')
      : `⚠ Coverage ${d.max_horizon_coverage_pct ?? 0}% — recovery rates hidden until ≥${d.min_coverage_pct_for_conclusions ?? 80}%. ${d.note || ''}${reason}`;
    note.style.color = d.conclusions_allowed ? '' : 'var(--amber)';
  }'''
    if old_hor in text:
        text = text.replace(old_hor, new_hor, 1)

TARGET.write_text(text, encoding="utf-8")
print(f"Patched {TARGET}")
