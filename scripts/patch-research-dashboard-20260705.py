#!/usr/bin/env python3
"""Patch research_dashboard.py — analyzer audit 2026-07-05."""
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent" / "research" / "research_dashboard.py"
text = TARGET.read_text(encoding="utf-8")

if '("typeb", "Type B Research"' not in text:
    text = text.replace(
        '        ("ai", "AI Lab", "ai_calibration_report.json"),\n    )),',
        '        ("ai", "AI Lab", "ai_calibration_report.json"),\n        ("typeb", "Type B Research", "type_b_predictor_report.json"),\n    )),',
        1,
    )

if 'id="lanes-metrics-note"' not in text:
    text = text.replace(
        '    <table><thead><tr><th>Lane</th><th>Appr</th><th>Sess Fills</th><th>Shadow</th>',
        '    <p class="note" id="lanes-metrics-note">Sess Fills = live/paper closes only. V2 lanes show checker-pass sims and reject counterfactuals separately.</p>\n    <table><thead><tr><th>Lane</th><th>Appr</th><th>Sess Fills</th><th>Chk Pass</th><th>Reject Sim</th><th>Shadow</th>',
        1,
    )

if 'id="sec-typeb"' not in text:
    text = text.replace(
        '  <section id="sec-lanes-def">',
        '''  <section id="sec-typeb">
    <h2>Type B Research</h2>
    <p class="note" id="typeb-note">Pre-entry feature separators for TYPE_B runners (MFE≥15%). TYPE_B is post-trade classification — not an entry gate.</p>
    <div class="kpis" id="typeb-kpis"></div>
    <table><thead><tr><th>Cohort</th><th>Trades</th><th>WR%</th><th>Avg MFE%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="typeb-cohort-body"></tbody></table>
    <h3>Top separators (TYPE_B vs TYPE_A)</h3>
    <table><thead><tr><th>Feature</th><th>TYPE_A mean</th><th>TYPE_B mean</th><th>|Δ|</th></tr></thead><tbody id="typeb-sep-body"></tbody></table>
  </section>
  <section id="sec-lanes-def">''',
        1,
    )

text = text.replace(
    'Exit reason × AI × spread × peak MFE × time-in-trade × TYPE × lane.',
    'Exit reason × AI × spread × peak MFE × time-in-trade × lane (TYPE_B excluded — not predictable).',
    1,
)

if 'id="ladder-sim-disclaimer"' not in text:
    text = text.replace(
        '    <p class="note" id="ladder-sim-note">Tick replay of executed trades',
        '    <p class="note" id="ladder-sim-note">Counterfactual tick replay on executed trades',
        1,
    )
    text = text.replace(
        '    <div class="kpis" id="ladder-sim-kpis"></div>\n    <table><thead><tr><th>Profile</th>',
        '    <p class="note amber" id="ladder-sim-disclaimer"></p>\n    <div class="kpis" id="ladder-sim-kpis"></div>\n    <table><thead><tr><th>Profile</th>',
        1,
    )

if 'exit-reason-recs' not in text:
    text = text.replace(
        '    <table><thead><tr><th>Exit reason</th><th>N</th><th>Left $</th>',
        '    <table><thead><tr><th>Exit reason</th><th>N</th><th>Left $</th>',
        1,
    )
    text = text.replace(
        '<tbody id="exit-reason-body"></tbody></table>\n  </section>\n  <section id="sec-ladder-sim">',
        '<tbody id="exit-reason-body"></tbody></table>\n    <h3>Recommended actions</h3>\n    <ul id="exit-reason-recs"></ul>\n  </section>\n  <section id="sec-ladder-sim">',
        1,
    )

if '"v2_checker_pass_sims"' not in text:
    text = text.replace(
        '        rows.append({\n            "lane": lane,\n            "trades": fills,',
        '        rows.append({\n            "lane": lane,\n            "trades": fills,\n            "v2_checker_pass_sims": int(m.get("v2_checker_pass_sims") or 0),\n            "v2_reject_counterfactual_sims": int(m.get("v2_reject_counterfactual_sims") or 0),\n            "v2_metrics_note": m.get("v2_metrics_note") or "",\n            "coordinator_note": m.get("coordinator_note") or "",',
        1,
    )

if '"recommendations": rep.get("recommendations")' not in text:
    text = text.replace(
        '        "reasons": rep.get("reasons") or [],\n    }',
        '        "reasons": rep.get("reasons") or [],\n        "recommendations": rep.get("recommendations") or [],\n    }',
        1,
    )

if 'def _ok(row):' not in text:
    old_exit = '''def _exit_combos_payload():
    rep = _read_report("exit_combinations_report.json")
    return {
        "generated_at": rep.get("generated_at"),
        "benchmark_lane": rep.get("benchmark_lane"),
        "overall_left_on_table_usd": rep.get("overall_left_on_table_usd"),
        "total_combos": rep.get("total_combos", 0),
        "top": (rep.get("top") or [])[:50],
        "worst_leakage": (rep.get("worst_leakage") or [])[:30],
    }'''
    new_exit = '''def _exit_combos_payload():
    rep = _read_report("exit_combinations_report.json")

    def _ok(row):
        return "TYPE_B" not in str((row or {}).get("type") or "").upper()

    top = [r for r in (rep.get("top") or []) if _ok(r)]
    worst = [r for r in (rep.get("worst_leakage") or []) if _ok(r)]
    return {
        "generated_at": rep.get("generated_at"),
        "benchmark_lane": rep.get("benchmark_lane"),
        "overall_left_on_table_usd": rep.get("overall_left_on_table_usd"),
        "total_combos": len(top),
        "filter_note": rep.get("filter_note") or "TYPE_B excluded from exit combos.",
        "top": top[:50],
        "worst_leakage": worst[:30],
    }'''
    if old_exit in text:
        text = text.replace(old_exit, new_exit, 1)

if '"disclaimer": rep.get("disclaimer")' not in text:
    text = text.replace(
        '        "replays_available": rep.get("replays_available"),\n        "best_profile_id": rep.get("best_profile_id"),',
        '        "replays_available": rep.get("replays_available"),\n        "replays_matched_executed": rep.get("replays_matched_executed"),\n        "disclaimer": rep.get("disclaimer"),\n        "best_profile_id": rep.get("best_profile_id"),',
        1,
    )

# loadLanes — replace inner return line via marker
marker = "document.getElementById('lane-body').innerHTML = (d.lanes||[]).map(row => {"
if 'v2_checker_pass_sims' not in text.split(marker, 1)[1].split('async function loadChase', 1)[0]:
    start = text.index(marker)
    end = text.index('async function loadChase()', start)
    block = text[start:end]
    new_block = '''document.getElementById('lane-body').innerHTML = (d.lanes||[]).map(row => {
    let cls = '';
    if (row.pathway_status === 'SHADOW_COLLECTING') cls = 'amber';
    else if (row.retired || (row.pathway_status || '').includes('RETIRED')) cls = 'amber';
    else if (row.status === 'UNDERPERFORMING') cls = 'red';
    else if (row.status === 'BEATS BENCHMARK' || row.status === 'PRIMARY_PRODUCTION') cls = 'green';
    let role = row.pathway_status || (row.retired ? 'RETIRED' : row.status);
    if (row.lane === 'AI_SCAN' && row.coordinator_note) role = row.coordinator_note;
    if (row.v2_metrics_note && row.lane && row.lane.includes('A160')) role = row.v2_metrics_note;
    const atF = row.all_time_fills || 0;
    const atP = row.all_time_pnl || 0;
    const sh = row.shadow_filled || 0;
    const shPnl = row.shadow_pnl || 0;
    const chk = row.v2_checker_pass_sims || 0;
    const rej = row.v2_reject_counterfactual_sims || 0;
    return `<tr class="${cls}"><td>${row.lane}</td><td>${row.approves ?? 0}</td><td>${row.trades}</td><td>${chk || '\\u2014'}</td><td>${rej || '\\u2014'}</td><td>${sh}${row.shadow_fill_pct ? ' ('+row.shadow_fill_pct+'%)' : ''}</td><td>$${fmtUsd(row.pnl)}</td><td class="${shPnl>=0?'green':'red'}">$${fmtUsd(shPnl)}</td><td>$${fmtUsd(row.ev)}</td><td>${atF || '\\u2014'}</td><td>${atF ? '$'+fmtUsd(atP) : '\\u2014'}</td><td title="${role}">${role.length > 48 ? role.slice(0,45)+'\\u2026' : role}</td></tr>`;
  }).join('') || '<tr><td colspan="12">Run analyzer: python analyzer_research_engine_v62.py</td></tr>';

'''
    text = text[:start] + new_block + text[end:]

if 'async function loadTypeB()' not in text:
    text = text.replace(
        'async function loadGenome()',
        '''async function loadTypeB() {
  const r = await fetch('/api/typeb');
  const d = await r.json();
  const note = document.getElementById('typeb-note');
  if (note && d.classification) note.textContent = d.classification + ' — advisory research only.';
  document.getElementById('typeb-kpis').innerHTML = [
    ['Cohorts', (d.cohorts||[]).length],
    ['Separators', (d.separators||[]).length],
    ['Rules', (d.rules||[]).length],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('typeb-cohort-body').innerHTML = (d.cohorts||[]).map(c =>
    `<tr><td>${c.cohort}</td><td>${c.trades||0}</td><td>${c.wr_pct ?? 'n/a'}%</td><td>${c.avg_mfe_pct ?? 'n/a'}</td><td>$${fmtUsd(c.pnl_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td></tr>`
  ).join('') || '<tr><td colspan="6">Run analyzer — type_b_predictor_report.json</td></tr>';
  document.getElementById('typeb-sep-body').innerHTML = (d.separators||[]).map(s =>
    `<tr><td>${s.feature||s.name||''}</td><td>${s.type_a_mean ?? 'n/a'}</td><td>${s.type_b_mean ?? 'n/a'}</td><td>${s.abs_delta ?? s.delta ?? 'n/a'}</td></tr>`
  ).join('') || '<tr><td colspan="4">No separators yet.</td></tr>';
}

async function loadGenome()''',
        1,
    )

if 'loadTypeB();' not in text:
    text = text.replace(
        '  await loadAI();\n  await loadGenome();',
        '  await loadAI();\n  await loadTypeB();\n  await loadGenome();',
        1,
    )

# loadExitReasonLeak — patch KPIs and add recs
if "exit-reason-recs" in text and "recEl" not in text:
    start = text.index('async function loadExitReasonLeak()')
    end = text.index('async function loadLadderSim()', start)
    new_fn = '''async function loadExitReasonLeak() {
  const r = await fetch('/api/exit-reason-leak');
  const d = await r.json();
  document.getElementById('exit-reason-kpis').innerHTML = [
    ['Total left', '$' + fmtUsd(d.overall_left_usd)],
    ['Booked', '$' + fmtUsd(d.overall_booked_usd)],
    ['Peak', '$' + fmtUsd(d.overall_peak_usd)],
    ['Exit reasons', (d.reasons||[]).length],
    ['Actions', (d.recommendations||[]).length],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('exit-reason-body').innerHTML = (d.reasons||[]).map(r =>
    `<tr><td>${r.exit_reason||''}</td><td>${r.trades||0}</td><td class="red">$${fmtUsd(r.left_on_table_usd)}</td><td>$${fmtUsd(r.avg_left_usd)}</td><td>${r.avg_mfe_margin_pct??'n/a'}%</td><td>${r.avg_realized_margin_pct??'n/a'}%</td><td class="red">${r.avg_leakage_margin_pct??'n/a'}%</td><td>${r.capture_ratio_pct??'n/a'}%</td></tr>`
  ).join('') || '<tr><td colspan="8">Run analyzer for exit reason leakage.</td></tr>';
  const recEl = document.getElementById('exit-reason-recs');
  if (recEl) {
    recEl.innerHTML = (d.recommendations||[]).map(rec =>
      `<li><b>${rec.exit_reason}</b> (${rec.priority}) — ${rec.action} <code>${rec.script_hint||''}</code></li>`
    ).join('') || '<li>Run analyzer to generate action items.</li>';
  }
}

'''
    text = text[:start] + new_fn + text[end:]

if 'ladder-sim-disclaimer' in text and "disc.textContent" not in text:
    start = text.index('async function loadLadderSim()')
    end = text.index('async function loadPathwayAudit()', start)
    new_fn = '''async function loadLadderSim() {
  const r = await fetch('/api/ladder-sim');
  const d = await r.json();
  const disc = document.getElementById('ladder-sim-disclaimer');
  if (disc) disc.textContent = d.disclaimer || '';
  document.getElementById('ladder-sim-kpis').innerHTML = [
    ['Actual PnL', '$' + fmtUsd(d.actual_realized_usd)],
    ['Executed trades', d.actual_trades ?? 0],
    ['Matched replays', d.replays_matched_executed ?? 0],
    ['Replays on disk', d.replays_available ?? 0],
    ['Best profile', d.best_profile_id || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('ladder-sim-body').innerHTML = (d.profiles||[]).map(p => {
    const delta = p.delta_vs_actual_usd;
    const cls = delta != null && delta > 50 ? 'amber' : '';
    return `<tr class="${cls}"><td>${p.profile_id||''}</td><td>${(p.ladder||[]).map(r=>r.join('\\u2192')).join(' · ')||p.label||''}</td><td>${p.trades_simulated||0}</td><td>$${fmtUsd(p.sum_pnl_usd)}</td><td>$${fmtUsd(p.avg_pnl_usd)}</td><td>${p.wr_pct??'n/a'}%</td><td>${p.ladder_exit_pct??'n/a'}%</td><td>${delta!=null?'$'+fmtUsd(delta):'n/a'}</td></tr>`;
  }).join('') || '<tr><td colspan="8">No ladder sim data — need executed-trade tick replays.</td></tr>';
}

'''
    text = text[:start] + new_fn + text[end:]

TARGET.write_text(text, encoding="utf-8")
print(f"Patched {TARGET}")
