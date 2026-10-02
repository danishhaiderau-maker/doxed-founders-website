# Fly.io image for the doxxedcrypto.digital showcase BTC bot.
# Canonical in-repo bot â€” see .cursor/rules/no-blunt-bot-sync.mdc (DO NOT sync from bybit_bot.py).
FROM python:3.12-slim
# 3.12 (not 3.11): the analyzer module has f-strings with backslashes in the
# expression part (e.g. analyzer_research_engine_v62.py:9101). Python 3.11 rejects
# those with SyntaxError -> "SYSTEM_NOT_READY: cannot import analyzer" -> bot exit
# loop every ~7-10min. 3.12 lifted that restriction (matches the local bot's Python).

ARG SOURCE_GIT_REV=unknown
ENV SOURCE_GIT_REV=${SOURCE_GIT_REV} \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7002 \
    DASHBOARD_BIND_HOST=0.0.0.0 \
    DASHBOARD_PUBLIC_HOST=0.0.0.0 \
    RESEARCH_DASHBOARD_PORT=9001 \
    RESEARCH_DASHBOARD_BIND_HOST=0.0.0.0 \
    BOT_DATA_DIR=/app/data

WORKDIR /app

# System deps: gcc for any wheels (numpy/pandas), curl for health probes, tzdata for AEST logs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash build-essential curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole agent dir (bot.py, btc_conservative_agent.py, analyzer_research_engine_v62.py,
# research_dashboard.py, showcase_ui.py, early_boot.py, pathway_*.py, etc.).
COPY . /app

# Fail the image build if the whitelist-style .dockerignore drops a module that
# bot.py or the canonical analyzer imports at runtime.
RUN python -c "import platform_relay_streaming, research.analysis_eligibility, research.platform_relay_evidence, research.source_market_evidence, research.immutable_archive, research.counterfactual_normalization, research.epoch_quarantine, research.mirror_generation_lease"
# V3 dual-write is on the Fly bot's critical evidence path. Fail the image
# build if the whitelist omits any collector dependency.
RUN python -c "import collector_v22, collector_v22_provisional, research_v3_contract, research_v3_store, research_v3_bridge"
# The exact lifecycle-tail recovery command is invoked only by its guarded
# workflow, but must still be importable in the production image.
RUN python -m py_compile lifecycle_tail_repair.py lifecycle_tail_repair_verify.py lifecycle_cursor_rebind.py lifecycle_cursor_rebind_verify.py execution_tail_repair.py execution_tail_repair_verify.py \
    && python -c "import lifecycle_tail_repair, lifecycle_tail_repair_verify, lifecycle_cursor_rebind, lifecycle_cursor_rebind_verify, execution_tail_repair, execution_tail_repair_verify"
# Quantity-aware conservative paper evidence is part of both the runtime
# collector and analyzer path. Fail the image build if the whitelist omits it.
RUN python -c "import research.conservative_fill_cohort, research.conservative_limit_fill, research.quantity_execution, research.venue_quantity_constraints, research.venue_quantity_observation, research.bitfinex_pair_constraints, research.fill_model"
# This import executes before every AI API call, not just analyzer startup.
RUN python -c "import research.runtime_baseline_declaration"
RUN python -c "import research.close_origin"
RUN python -c "import research.scan_counterfactual_unavailable, research.reset_writer_barrier"
RUN python -c "import future_path_attempt_receipt, research_v3_future_paths_worker"
# Optional shadow segment shipper (RESEARCH_SEGMENTS_ENABLED=1) must be importable.
RUN python -c "import research_segment_format, research_segment_selection, research_segment_store, research_segment_shipper, research_segment_server, research_segment_prune, research_fresh_start_wipe, data_retention_policy"
# Bitfinex fee profile, fill markouts and every registry tile policy load on the order path.
RUN python -c "import bitfinex_cost_profile, execution_markouts, adaptive_regime_entry, scenario_c_config, taker_time_exit_binding, paper_policy_family_trend_fade_60, paper_policy_family_trend_fade_60_committed, cross_venue_lead, cross_venue_premium, paper_policy_family_xvenue_lead, paper_policy_family_xvenue_premium, paper_policy_family_continuous_aug_original, tile_paired_comparison"
# Shadow cross-venue collector process and its shared tape contract.
RUN python -c "import cross_venue_tape, cross_venue_collector"
RUN python -c "import ai_shadow_challengers, ai_regime_shadow, decision_feature_snapshots, research.decision_model_report"
# Watch-only market-context collector and its tape/session/regime helpers.
RUN python -c "import market_session_calendar, trailing_regime, market_context_tape, market_context_collector"
# System-health banner and Alerts section (dashboard routes import both).
RUN python -c "import system_health_banner, system_health_alerts, runtime_uptime"
# Thread heartbeats, relay stale-owner delivery guard, AI call cost and shadow chase buckets load at bot import.
RUN python -c "import thread_health, relay_delivery_guard, ai_call_cost, shadow_chase_buckets"

# Persistent volume mount point (created at runtime; fly.toml mounts bot_data here).
RUN mkdir -p /app/data

EXPOSE 7002

# Entrypoint: wire writable dirs to the volume, start analyzer :9001 in background, then bot :7002.
# sed strips any CRLF (file may be authored on Windows) so the Bash shebang stays valid.
COPY fly-entrypoint.sh /fly-entrypoint.sh
RUN sed -i 's/\r$//' /fly-entrypoint.sh && chmod +x /fly-entrypoint.sh

CMD ["/fly-entrypoint.sh"]
