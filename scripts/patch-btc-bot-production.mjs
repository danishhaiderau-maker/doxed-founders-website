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

if (changed) {
  writeFileSync(TARGET, src, 'utf8');
  console.log('Applied production patches to bot.py');
} else {
  console.log('bot.py production patches already applied');
}
