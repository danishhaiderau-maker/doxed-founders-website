# Fly.io image for the doxxedcrypto.digital showcase BTC bot.
# Canonical in-repo bot — see .cursor/rules/no-blunt-bot-sync.mdc (DO NOT sync from bybit_bot.py).
FROM python:3.12-slim
# 3.12 (not 3.11): the analyzer module has f-strings with backslashes in the
# expression part (e.g. analyzer_research_engine_v62.py:9101). Python 3.11 rejects
# those with SyntaxError -> "SYSTEM_NOT_READY: cannot import analyzer" -> bot exit
# loop every ~7-10min. 3.12 lifted that restriction (matches the local bot's Python).

ENV PYTHONUNBUFFERED=1 \
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
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole agent dir (bot.py, btc_conservative_agent.py, analyzer_research_engine_v62.py,
# research_dashboard.py, showcase_ui.py, early_boot.py, pathway_*.py, etc.).
COPY . /app

# Persistent volume mount point (created at runtime; fly.toml mounts bot_data here).
RUN mkdir -p /app/data

EXPOSE 7002

# Entrypoint: wire writable dirs to the volume, start analyzer :9001 in background, then bot :7002.
# sed strips any CRLF (file may be authored on Windows) so the #!/bin/sh shebang stays valid.
COPY fly-entrypoint.sh /fly-entrypoint.sh
RUN sed -i 's/\r$//' /fly-entrypoint.sh && chmod +x /fly-entrypoint.sh

CMD ["/fly-entrypoint.sh"]
