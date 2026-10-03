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

# Sealed research-segment shipper (shadow mode, pruning not implemented).
# Separate niced process: no trade lock, no HTTP, no bot import. Default OFF.
if [ "${RESEARCH_SEGMENTS_ENABLED:-0}" = "1" ]; then
  SEGMENT_LOG="$DATA_DIR/segment-shipper.log"
  if [ -f "$SEGMENT_LOG" ] && [ "$(wc -c < "$SEGMENT_LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    : > "$SEGMENT_LOG"
  fi
  echo "[fly-entrypoint] starting research segment shipper (shadow, niced)..."
  nice -n 10 python /app/research_segment_shipper.py >> "$SEGMENT_LOG" 2>&1 &
else
  echo "[fly-entrypoint] RESEARCH_SEGMENTS_ENABLED!=1 -> segment shipper disabled."
fi

# Shadow-only cross-venue price tape (public WebSockets, no keys, no orders).
# Separate niced process with its own restart loop; runs in the runtime dir so
# its minute tape ships with the other JSONL evidence. Default ON.
if [ "${CROSS_VENUE_COLLECTOR_ENABLED:-1}" = "1" ]; then
  CV_LOG="$DATA_DIR/cross-venue-collector.log"
  if [ -f "$CV_LOG" ] && [ "$(wc -c < "$CV_LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    : > "$CV_LOG"
  fi
  echo "[fly-entrypoint] starting cross-venue collector (shadow, niced)..."
  ( while true; do
      nice -n 10 python /app/cross_venue_collector.py >> "$CV_LOG" 2>&1
      echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] cross-venue collector exited rc=$? -> restarting in 10s" >> "$CV_LOG"
      sleep 10
    done ) &
else
  echo "[fly-entrypoint] CROSS_VENUE_COLLECTOR_ENABLED!=1 -> cross-venue collector disabled."
fi

# Watch-only market context (Coinbase premium, liquidations, funding/OI/basis,
# session flags). Public keyless feeds, no orders. Separate niced process with
# its own restart loop in the runtime dir so its JSONL ships with the segments.
if [ "${MARKET_CONTEXT_COLLECTOR_ENABLED:-1}" = "1" ]; then
  MC_LOG="$DATA_DIR/market-context-collector.log"
  if [ -f "$MC_LOG" ] && [ "$(wc -c < "$MC_LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    : > "$MC_LOG"
  fi
  echo "[fly-entrypoint] starting market-context collector (watch-only, niced)..."
  ( while true; do
      nice -n 10 python /app/market_context_collector.py >> "$MC_LOG" 2>&1
      echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] market-context collector exited rc=$? -> restarting in 10s" >> "$MC_LOG"
      sleep 10
    done ) &
else
  echo "[fly-entrypoint] MARKET_CONTEXT_COLLECTOR_ENABLED!=1 -> market-context collector disabled."
fi

# Live Indicator Edge engine: pure observation. Reads the tape/minute files the
# collectors write and appends one indicator_bars_v1 row per closed 3-minute
# bar. No network, no orders, no bot import. Separate niced process with its
# own restart loop in the runtime dir so its JSONL ships with the segments.
if [ "${INDICATOR_ENGINE_ENABLED:-1}" = "1" ]; then
  IE_LOG="$DATA_DIR/indicator-engine.log"
  if [ -f "$IE_LOG" ] && [ "$(wc -c < "$IE_LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    : > "$IE_LOG"
  fi
  echo "[fly-entrypoint] starting indicator engine (observation-only, niced)..."
  ( while true; do
      OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 python /app/indicator_engine.py >> "$IE_LOG" 2>&1
      echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] indicator engine exited rc=$? -> restarting in 10s" >> "$IE_LOG"
      sleep 10
    done ) &
else
  echo "[fly-entrypoint] INDICATOR_ENGINE_ENABLED!=1 -> indicator engine disabled."
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
