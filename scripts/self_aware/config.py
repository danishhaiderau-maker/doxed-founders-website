"""Paths, cadences and thresholds. Every path can be overridden by environment variable."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "self_aware_v1"
SERVER_PORT = 9021
RUNBOOK = "docs/SELF_AWARE_RUNBOOK.md"
RUNBOOK_BASE = "https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/"
ALARM_PREFIX = "selfaware."

CADENCE_SEC = {
    "views": 300,
    "diagnose": 120,
    "uptime": 300,
    "tiles": 600,
    "ai": 900,
    "edges": 1800,
    "digest": 3600,
    "data": 1800,
    "sections": 7200,
    "contracts": 7200,
    "contracts_light": 300,
    "compat": 1800,
    "fees": 1800,
}

THRESHOLDS = {
    "mirror_ai_lag_amber_sec": 30 * 60,
    "mirror_ai_lag_red_sec": 3 * 3600,
    "tape_lag_amber_sec": 30 * 60,
    "tape_lag_red_sec": 3 * 3600,
    "segment_ack_lag_seq": 6,
    "segment_ack_lag_sec": 20 * 60,
    "parity_max_age_sec": 3 * 3600,
    "ai_call_unanswered_grace_sec": 15 * 60,
    "ai_calls_per_hour_min": 8,
    "fly_ai_stale_sec": 15 * 60,
    "deploy_pause_amber_sec": 45 * 60,
    "orders_quiet_amber_sec": 6 * 3600,
    # Per-tile quiet window: gap_mult x the tile's own mean order-eligible gap over 48 h, floored at
    # orders_quiet_amber_sec (rare-trigger CVD/NO_TRADE/regime tiles: orders_quiet_rare_sec) and capped.
    "orders_quiet_gap_mult": 3.0,
    "orders_quiet_rare_sec": 12 * 3600,
    "orders_quiet_cap_sec": 48 * 3600,
    "fill_close_max_open_sec": 8 * 3600,
    "analyzer_success_amber_sec": 45 * 60,
    "analyzer_success_red_sec": 3 * 3600,
    "analyzer_cycle_slow_sec": 25 * 60,
    "snapshot_max_age_sec": 15 * 60,
    "venue_stale_sec": 120,
    # Bitfinex WS liveness: the heartbeat (every ~15 s per channel) is the transport
    # clock. Data-tick age is not: a quiet tape routinely leaves it at 25-50 s.
    "bfx_ws_heartbeat_amber_sec": 60,
    "bfx_ws_tick_fallback_amber_sec": 60,
    "bfx_ws_reconnect_storm_count": 3,
    "bfx_ws_reconnect_storm_window_sec": 15 * 60,
    "rate_limit_persistent_sec": 15 * 60,
    "evaluation_age_cpu_sec": 90,
    "evidence_keep_files": 500,
    "tape_fill_amber_pct": 98.0,
    "tape_gap_amber_sec": 300,
    "minute_fill_amber_pct": 95.0,
    "laptop_days_to_cap_amber": 7.0,
    "laptop_days_to_cap_red": 2.0,
    "fly_hours_to_full_amber": 72.0,
    "fly_hours_to_full_red": 24.0,
    "laptop_disk_days_amber": 14.0,
    "laptop_disk_days_red": 5.0,
    "duplicate_copies_amber_gb": 1.0,
    "data_doc_max_age_sec": 2 * 3600,
    "sections_doc_max_age_sec": 3 * 3600,
    "contracts_heavy_max_age_sec": 5 * 3600,
    "contracts_heavy_defer_max_sec": 45 * 60,
    "compat_doc_max_age_sec": 2 * 3600,
    "fees_doc_max_age_sec": 2 * 3600,
}


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or default)


def assert_not_onedrive(path: Path) -> None:
    if "onedrive" in str(path).lower():
        raise SystemExit(f"REFUSED: {path} is under OneDrive (AGENTS.md: never use OneDrive)")


@dataclass
class Paths:
    home: Path = field(default_factory=lambda: _env_path("SELF_AWARE_HOME", r"C:\DoxxedCrypto\self-aware"))
    chain: Path = field(default_factory=lambda: _env_path("DOXXED_LAPTOP_CHAIN_STATE", r"C:\DoxxedCrypto\laptop-chain"))
    mirror: Path = field(default_factory=lambda: _env_path("SELF_AWARE_MIRROR", r"C:\DoxxedCrypto\fly-mirror-segments\tree"))
    mirror_archive: Path = field(default_factory=lambda: _env_path(
        "SELF_AWARE_MIRROR_ARCHIVE", r"C:\DoxxedCrypto\archive\fly-mirror-segments-v1-archive-20260930\tree"))
    puller: Path = field(default_factory=lambda: _env_path("SELF_AWARE_PULLER", r"C:\DoxxedCrypto\fly-mirror-segments\.puller"))
    exports: Path = field(default_factory=lambda: _env_path("SELF_AWARE_EXPORTS", r"C:\DoxxedCrypto\analyzer-exports\latest"))
    archive: Path = field(default_factory=lambda: _env_path("SELF_AWARE_ANALYSIS_ARCHIVE", r"C:\DoxxedCrypto\analysis-archive"))
    diagnostics: Path = field(default_factory=lambda: _env_path(
        "SELF_AWARE_DIAGNOSTICS", r"C:\DoxxedCrypto\btc-v31-current\diagnostics"))
    analyzer_repo: Path = field(default_factory=lambda: _env_path("SELF_AWARE_ANALYZER_REPO", r"C:\DoxxedCrypto\v2c"))
    retention: Path = field(default_factory=lambda: _env_path("SELF_AWARE_RETENTION", r"C:\DoxxedCrypto\bot-data-retention"))
    segment_manifests: Path = field(default_factory=lambda: _env_path(
        "SELF_AWARE_SEGMENT_MANIFESTS", r"C:\DoxxedCrypto\fly-segments\v2\man"))
    laptop_root: Path = field(default_factory=lambda: _env_path("SELF_AWARE_LAPTOP_ROOT", r"C:\DoxxedCrypto"))

    @property
    def store(self) -> Path:
        return self.home / "selfaware.duckdb"

    @property
    def state(self) -> Path:
        return self.home / "state.json"

    @property
    def journal(self) -> Path:
        return self.home / "repair-journal.jsonl"

    @property
    def evidence(self) -> Path:
        return self.home / "evidence"

    @property
    def health(self) -> Path:
        return self.chain / "health"

    def check(self) -> None:
        for p in (self.home, self.chain, self.mirror, self.exports, self.diagnostics):
            assert_not_onedrive(p)


def code_revision() -> str:
    """Revision of the checkout this package runs from (provenance on every result)."""
    root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rev = out.stdout.strip()
        if out.returncode == 0 and rev:
            return rev
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"
