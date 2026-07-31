#!/bin/bash
# Fly.io entrypoint for the showcase BTC bot.
# 1. Run from a volume-backed working directory so all relative runtime files persist.
# 2. Keep source code read-only in /app and import it through PYTHONPATH.
# 3. Optionally start the analyzer, then run the bot (:7002) in the foreground.
set -e

DATA_DIR="${BOT_DATA_DIR:-/app/data}"
RUNTIME_DIR="$DATA_DIR/runtime"
mkdir -p "$DATA_DIR" "$RUNTIME_DIR" "$DATA_DIR/locks"

# Preserve the existing volume directories while exposing them below the new
# persistent runtime working directory.
for d in research research_accumulator research_archive; do
  mkdir -p "$DATA_DIR/$d"
  if [ ! -e "$RUNTIME_DIR/$d" ]; then
    ln -s "$DATA_DIR/$d" "$RUNTIME_DIR/$d"
  fi
done

# Version-controlled assets are refreshed from the image on every deploy.
# Mutable state is never copied from the image into the persistent runtime.
for source_asset in manifest.json genome_cluster_library.json; do
  if [ -f "/app/$source_asset" ]; then
    cp -f "/app/$source_asset" "$RUNTIME_DIR/$source_asset"
  fi
done

export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
export BOT_SINGLETON_DIR="$DATA_DIR/locks"
cd "$RUNTIME_DIR"

# Analyzer :9001 is OFF on Fly by default: Fly owns the single strategy/trading
# process and persists its raw evidence; the desktop incrementally mirrors that
# evidence and runs the heavy analyzer without starting a second bot.
# Set ANALYZER_ENABLED=true only for an explicit reviewed recovery deployment.
if [ "${ANALYZER_ENABLED:-false}" = "true" ]; then
  echo "[fly-entrypoint] starting analyzer_research_engine_v62.py on :9001 (background)..."
  python /app/analyzer_research_engine_v62.py > /tmp/analyzer.log 2>&1 &
else
  echo "[fly-entrypoint] ANALYZER_ENABLED!=true -> trading-only mode (no analyzer)."
fi

# Publish the bounded canonical relay state directly from Fly. This replaces
# the Windows-only PowerShell pusher and keeps Agent Hub/relay execution on the
# same bot identity even when the research PC is off.
if [ -n "${BOT_CONTROL_SECRET:-}" ]; then
  SNAPSHOT_LOG="$DATA_DIR/relay-state-pusher.log"
  echo "[fly-entrypoint] starting authenticated relay-state publisher..."
  python /app/fly_relay_state_pusher.py >> "$SNAPSHOT_LOG" 2>&1 &
else
  echo "[fly-entrypoint] BOT_CONTROL_SECRET missing -> relay-state publisher disabled."
fi

echo "[fly-entrypoint] starting btc_conservative_agent.py on :7002 (foreground, auto-restart loop)..."
export PYTHONUNBUFFERED=1
BOT_LOG="$DATA_DIR/bot.log"
# Trading bot must stay up 24/7. Run it in an auto-restart loop so a code-level
# crash/exit (unhandled exception, missing-credential bail, exchange hiccup) does NOT
# stop the Fly machine — PID 1 (this loop) keeps running and relaunches the bot within
# seconds. Every run's stdout/stderr (incl. Python tracebacks) is tee'd to the persistent
# volume so the crash cause is readable after the fact via `fly ssh console`.
set +e
while true; do
  # Cap the log so a crash loop can't fill the volume.
  if [ -f "$BOT_LOG" ] && [ "$(wc -c < "$BOT_LOG" 2>/dev/null || echo 0)" -gt 52428800 ]; then
    : > "$BOT_LOG"
  fi
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [fly-entrypoint] bot starting -> $BOT_LOG" | tee -a "$BOT_LOG"
  python /app/btc_conservative_agent.py 2>&1 | tee -a "$BOT_LOG"
  rc=${PIPESTATUS[0]}
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [fly-entrypoint] bot exited rc=$rc -> restarting in 3s" | tee -a "$BOT_LOG"
  sleep 3
done
