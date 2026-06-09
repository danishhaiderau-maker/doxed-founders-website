#!/usr/bin/env node
/**
 * Production patches for btc-conservative-agent/bot.py after research-repo sync.
 * - /api/pause endpoint (Nest admin calls this; research repo only had /api/resume)
 * - ADMIN_MANUAL pause that health-check auto-recovery will not clear
 * - Railway PORT binding + early /health before slow preload (fixes healthcheck failure)
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const TARGET = join(dirname(fileURLToPath(import.meta.url)), '..', 'services/btc-conservative-agent/bot.py');

let src = readFileSync(TARGET, 'utf8');
let changed = false;

if (!src.includes('"ADMIN_MANUAL"')) {
  src = src.replace(
    'PAUSE_PRIORITIES = {"STALE_DATA_HARD_STOP": 50, "THREAD_CRASH": 1, "QUEUE_OVERFLOW": 60, "": 0, "CSV_FAILURE": 100, "PRELOAD_FAILED": 100}',
    'PAUSE_PRIORITIES = {"STALE_DATA_HARD_STOP": 50, "THREAD_CRASH": 1, "QUEUE_OVERFLOW": 60, "": 0, "CSV_FAILURE": 100, "PRELOAD_FAILED": 100, "ADMIN_MANUAL": 200}',
  );
  changed = true;
}

if (!src.includes("@app.route('/api/pause'")) {
  src = src.replace(
    `@app.route('/api/resume', methods=['POST'])
def api_resume():
    set_execution_paused("")
    return jsonify({"status": "resumed"})`,
    `@app.route('/api/pause', methods=['POST'])
def api_pause():
    with state_lock:
        state["manual_admin_pause"] = True
        save_persistent_config()
    set_execution_paused("ADMIN_MANUAL")
    logger.warning("[ADMIN] Manual pause via /api/pause [PIPELINE ENFORCEMENT]")
    return jsonify({"status": "paused", "execution_paused": True, "execution_reason": "ADMIN_MANUAL"})

@app.route('/api/resume', methods=['POST'])
def api_resume():
    with state_lock:
        state["manual_admin_pause"] = False
        save_persistent_config()
    set_execution_paused("")
    logger.info("[ADMIN] Manual resume via /api/resume [PIPELINE ENFORCEMENT]")
    return jsonify({"status": "resumed", "execution_paused": False})`,
  );
  changed = true;
}

if (!src.includes('not state.get("manual_admin_pause")')) {
  src = src.replace(
    `                if state.get("execution_paused"):
                    set_execution_paused("")`,
    `                if state.get("execution_paused") and not state.get("manual_admin_pause"):
                    set_execution_paused("")`,
  );
  changed = true;
}

if (!src.includes('os.getenv("PORT"')) {
  src = src.replace(
    'DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "7800"))',
    'DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT", "7800"))',
  );
  changed = true;
}

if (!src.includes('[RAILWAY] Early health server')) {
  src = src.replace(
    `    logger.info(f"[STARTUP] bot_start_time locked at {bot_start_time} - old data blocked")
    _write_research_session(bot_start_time)
    research_mode = state.get("strategy_mode") == "RESEARCH"`,
    `    logger.info(f"[STARTUP] bot_start_time locked at {bot_start_time} - old data blocked")
    _write_research_session(bot_start_time)
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1)
    logger.info(f"[RAILWAY] Early health server on :{DASHBOARD_PORT}/health [PIPELINE ENFORCEMENT]")
    research_mode = state.get("strategy_mode") == "RESEARCH"`,
  );
  src = src.replace(
    `    logger.info(f"Bot start time locked at {bot_start_time} - old trades blocked")
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1)
    fetch_ohlcv()`,
    `    logger.info(f"Bot start time locked at {bot_start_time} - old trades blocked")
    fetch_ohlcv()`,
  );
  changed = true;
}

if (!src.includes('sys.platform != "win32"')) {
  src = src.replace(
    `    if not _port_is_open("127.0.0.1", port):
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],`,
    `    if not _port_is_open("127.0.0.1", port):
        return
    import sys
    if sys.platform != "win32":
        logger.warning(f"[PORT] {port} appears in use on Linux — continuing (Railway) [PIPELINE ENFORCEMENT]")
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],`,
  );
  changed = true;
}

if (!src.includes('manual_admin_pause') || !src.includes('should_run_pipeline() -> bool:\n    if state.get("manual_admin_pause")')) {
  if (!src.includes('if state.get("manual_admin_pause")')) {
    src = src.replace(
      'def should_run_pipeline() -> bool:\n    if len(latest_candles) < MIN_CANDLES:',
      'def should_run_pipeline() -> bool:\n    if state.get("manual_admin_pause") or (\n        state.get("execution_paused") and state.get("execution_reason") == "ADMIN_MANUAL"\n    ):\n        return False\n    if len(latest_candles) < MIN_CANDLES:',
    );
    changed = true;
  }

  if (src.includes('"status": "alive"') && src.includes('def health():')) {
    src = src.replace(
      `def health():
    with state_lock:
        hb = state.get("last_heartbeat", last_heartbeat)
    return jsonify({
        "status": "alive",
        "last_heartbeat": hb,
        "time_since_heartbeat": time.time() - hb,
        "execution_paused": state.get("execution_paused", False),
        "execution_reason": state.get("execution_reason", "")
    })`,
      `def health():
    with state_lock:
        hb = state.get("last_heartbeat", last_heartbeat)
        paused = bool(state.get("execution_paused", False))
        reason = state.get("execution_reason", "")
        manual = bool(state.get("manual_admin_pause", False))
    status = "paused" if paused else "alive"
    return jsonify({
        "status": status,
        "last_heartbeat": hb,
        "time_since_heartbeat": time.time() - hb,
        "execution_paused": paused,
        "execution_reason": reason,
        "manual_admin_pause": manual,
    })`,
    );
    changed = true;
  }

  if (src.includes('state["manual_admin_pause"] = True\n        save_persistent_config()') && !src.includes('state["live_armed"] = False')) {
    src = src.replace(
      `        state["manual_admin_pause"] = True
        save_persistent_config()`,
      `        state["manual_admin_pause"] = True
        state["live_armed"] = False
        save_persistent_config()`,
    );
    changed = true;
  }
}

if (src.includes('DASHBOARD_PUBLIC_HOST = os.getenv("DASHBOARD_PUBLIC_HOST", "10.0.0.102")')) {
  src = src.replace(
    'DASHBOARD_PUBLIC_HOST = os.getenv("DASHBOARD_PUBLIC_HOST", "10.0.0.102")',
    'DASHBOARD_PUBLIC_HOST = os.getenv("DASHBOARD_PUBLIC_HOST", "127.0.0.1")',
  );
  changed = true;
}

// ─── Clean slate: wipe config/policy, never restore sim balance, fix equity display ───
if (!src.includes('CONFIG_FILE, POLICY_FILE')) {
  src = src.replace(
    `"near_edge.log", "signal_persist.log", "crash_dump.json", POSITIONS_FILE,
        _AGENT_DEBUG_LOG, _AGENT_DEBUG_LOG_ALT,`,
    `"near_edge.log", "signal_persist.log", "crash_dump.json", POSITIONS_FILE,
        CONFIG_FILE, POLICY_FILE, RESEARCH_SESSION_FILE,
        _AGENT_DEBUG_LOG, _AGENT_DEBUG_LOG_ALT,`,
  );
  changed = true;
}

if (src.includes('"live_armed", "account_balance",')) {
  src = src.replace(
    '"live_armed", "account_balance",',
    '"live_armed",',
  );
  changed = true;
}

if (!src.includes('def enforce_clean_research_session()')) {
  src = src.replace(
    'def load_persistent_config():',
    `def enforce_clean_research_session():
    """Research sim always starts at STARTING_BALANCE with no carry-over trades."""
    global bot_start_time
    with trade_lock:
        trades.clear()
        pending_orders.clear()
        expired_orders.clear()
        open_positions.clear()
        trades_map.clear()
        recent_trades.clear()
    with replay_lock:
        replay_buffers.clear()
    with state_lock:
        state["account_balance"] = STARTING_BALANCE
        state["daily_pnl_usd"] = 0.0
        state["consecutive_losses"] = 0
        state["loss_pause_until"] = 0.0
        state["fresh_collection_mode"] = True
        if not state.get("live_armed", False):
            state["fresh_collection_mode"] = True
    bot_start_time = time.time()
    with state_lock:
        state["bot_start_time"] = bot_start_time
    logger.warning(
        f"[STARTUP] Clean research session — balance={STARTING_BALANCE} fresh_collection=ON "
        f"version={EXECUTION_FIX_VERSION} [PIPELINE ENFORCEMENT]"
    )

def _session_trades_only(trades_list):
    """Only expose trades opened after this process started."""
    start = bot_start_time or 0.0
    if start <= 0:
        return list(trades_list or [])
    kept = []
    for t in trades_list or []:
        if not isinstance(t, dict):
            continue
        ts = t.get("created_ts_ts") or t.get("entry_ts") or 0.0
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
        if float(ts or 0) >= start - 1.0:
            kept.append(t)
    return kept

def load_persistent_config():`,
  );
  changed = true;
}

if (!src.includes('enforce_clean_research_session()')) {
  const mainInject = `    load_persistent_config()
    if state.get("strategy_mode") == "RESEARCH" and not state.get("live_armed", False):
        enforce_clean_research_session()
    startup_hard_fix_ai_threshold()`;
  if (src.includes(mainInject)) {
    // already patched
  } else if (src.includes('    load_persistent_config()\n    startup_hard_fix_ai_threshold()')) {
    src = src.replace(
      '    load_persistent_config()\n    startup_hard_fix_ai_threshold()',
      mainInject,
    );
    changed = true;
  }
}

if (!src.includes('snapshot["trade_count_session"]')) {
  const snapInject = `        snapshot["account_balance"] = get_display_balance()
        snapshot["equity"] = snapshot["account_balance"] + total_unreal
        session_trades = _session_trades_only(trades_copy)
        snapshot["trades"] = session_trades
        snapshot["trade_count_session"] = len(session_trades)
        snapshot["bot_start_time"] = bot_start_time
        snapshot["fresh_collection_mode"] = bool(state.get("fresh_collection_mode", False))
        snapshot["ai_input"] = LAST_AI_PAYLOAD`;
  if (src.includes('        snapshot["account_balance"] = get_display_balance()\n        snapshot["ai_input"] = LAST_AI_PAYLOAD')) {
    src = src.replace(
      '        snapshot["account_balance"] = get_display_balance()\n        snapshot["ai_input"] = LAST_AI_PAYLOAD',
      snapInject,
    );
    changed = true;
  }
}

if (!src.includes("@app.route('/api/reset'")) {
  src = src.replace(
    `@app.route('/api/toggle_fresh_collection', methods=['POST'])
def toggle_fresh_collection():`,
    `@app.route('/api/reset', methods=['POST'])
def api_reset_showcase():
    """Admin/platform: wipe all research artifacts and restart session at $500."""
    result = perform_fresh_collection_reset()
    enforce_clean_research_session()
    return jsonify({"ok": True, "reset": result, "account_balance": STARTING_BALANCE})

@app.route('/api/toggle_fresh_collection', methods=['POST'])
def toggle_fresh_collection():`,
  );
  changed = true;
}

if (changed) {
  writeFileSync(TARGET, src, 'utf8');
  console.log('Applied production patches to bot.py');
}

// ─── Showcase sync fixes: real RESEARCH balance + session trades in /api/state ───
let syncFix = readFileSync(TARGET, 'utf8');
let syncChanged = false;

if (
  syncFix.includes('def get_display_balance():') &&
  !syncFix.includes('RESEARCH balance showcase [PIPELINE ENFORCEMENT]')
) {
  syncFix = syncFix.replace(
    `def get_display_balance():
    with state_lock:
        if not state.get("live_armed", False):
            return STARTING_BALANCE
        return state.get("account_balance", STARTING_BALANCE)`,
    `def get_display_balance():
    with state_lock:
        # RESEARCH balance showcase [PIPELINE ENFORCEMENT]
        if state.get("strategy_mode") == "RESEARCH":
            return round(float(state.get("account_balance", STARTING_BALANCE)), 4)
        if not state.get("live_armed", False):
            return STARTING_BALANCE
        return state.get("account_balance", STARTING_BALANCE)`,
  );
  syncChanged = true;
}

if (syncFix.includes('def _session_trades_only(trades_list):') && !syncFix.includes('raw_ts = t.get("ts")')) {
  syncFix = syncFix.replace(
    `        ts = t.get("created_ts_ts") or t.get("entry_ts") or 0.0
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
        if float(ts or 0) >= start - 1.0:
            kept.append(t)`,
    `        ts = t.get("created_ts_ts") or t.get("entry_ts") or 0.0
        if not ts:
            raw_ts = t.get("ts")
            if isinstance(raw_ts, str):
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0.0
            elif isinstance(raw_ts, (int, float)):
                ts = float(raw_ts)
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
        if float(ts or 0) >= start - 1.0:
            kept.append(t)`,
  );
  syncChanged = true;
}

if (syncChanged) {
  writeFileSync(TARGET, syncFix, 'utf8');
  console.log('Applied showcase sync fixes to bot.py');
} else {
  console.log('bot.py production patches already applied');
}
