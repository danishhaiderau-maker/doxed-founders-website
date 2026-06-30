#!/bin/sh
# Fly.io entrypoint for the showcase BTC bot.
# 1. Point writable research/runtime dirs at the persistent volume (bot_data -> /app/data).
# 2. Start the analyzer research engine (:9001) in the background.
# 3. Start the bot (:7002) in the foreground.
set -e

DATA_DIR="${BOT_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"

# Migrate writable dirs to the volume on first boot, then symlink so the bot's
# relative-path writes land on the persistent volume (survives redeploy).
for d in research research_accumulator research_archive; do
  if [ -d "$d" ] && [ ! -L "$d" ]; then
    if [ ! -d "$DATA_DIR/$d" ]; then
      cp -a "$d" "$DATA_DIR/$d" 2>/dev/null || true
    fi
    rm -rf "$d"
    ln -s "$DATA_DIR/$d" "$d"
  elif [ ! -e "$d" ]; then
    mkdir -p "$DATA_DIR/$d"
    ln -s "$DATA_DIR/$d" "$d"
  fi
done

# Analyzer :9001 is OFF on Fly by default — Fly is a pure trading host (100% uptime,
# no heavy data collection/analysis). The local bot owns data collection + Genome analysis.
# Set ANALYZER_ENABLED=true to run the analyzer here too (heavier; needs more memory).
if [ "${ANALYZER_ENABLED:-false}" = "true" ]; then
  echo "[fly-entrypoint] starting analyzer_research_engine_v62.py on :9001 (background)..."
  python analyzer_research_engine_v62.py > /tmp/analyzer.log 2>&1 &
else
  echo "[fly-entrypoint] ANALYZER_ENABLED!=true -> trading-only mode (no analyzer)."
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
  python btc_conservative_agent.py 2>&1 | tee -a "$BOT_LOG"
  rc=${PIPESTATUS[0]}
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [fly-entrypoint] bot exited rc=$rc -> restarting in 3s" | tee -a "$BOT_LOG"
  sleep 3
done
