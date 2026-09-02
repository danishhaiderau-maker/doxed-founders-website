# Fly.io image for the doxxedcrypto.digital showcase BTC bot.
# Canonical in-repo bot — see .cursor/rules/no-blunt-bot-sync.mdc (DO NOT sync from bybit_bot.py).
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
RUN python -c "import platform_relay_streaming, research.analysis_eligibility, research.platform_relay_evidence, research.source_market_evidence, research.immutable_archive, research.counterfactual_normalization, research.epoch_quarantine"
# V3 dual-write is on the Fly bot's critical evidence path. Fail the image
# build if the whitelist omits any collector dependency.
RUN python -c "import collector_v22, collector_v22_provisional, research_v3_contract, research_v3_store, research_v3_bridge"
# The exact lifecycle-tail recovery command is invoked only by its guarded
# workflow, but must still be importable in the production image.
RUN python -m py_compile lifecycle_tail_repair.py lifecycle_tail_repair_verify.py \
    && python -c "import lifecycle_tail_repair, lifecycle_tail_repair_verify"
# Quantity-aware conservative paper evidence is part of both the runtime
# collector and analyzer path. Fail the image build if the whitelist omits it.
RUN python -c "import research.conservative_fill_cohort, research.conservative_limit_fill, research.quantity_execution, research.venue_quantity_constraints"

# Persistent volume mount point (created at runtime; fly.toml mounts bot_data here).
RUN mkdir -p /app/data

EXPOSE 7002

# Entrypoint: wire writable dirs to the volume, start analyzer :9001 in background, then bot :7002.
# sed strips any CRLF (file may be authored on Windows) so the Bash shebang stays valid.
COPY fly-entrypoint.sh /fly-entrypoint.sh
RUN sed -i 's/\r$//' /fly-entrypoint.sh && chmod +x /fly-entrypoint.sh

CMD ["/fly-entrypoint.sh"]
