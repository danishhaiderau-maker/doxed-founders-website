"""File-selection rules for the research segment shipper.

Pure constants: no bot.py, Flask or I/O dependency. They decide which runtime
files are research evidence and therefore shippable.
"""

from __future__ import annotations

EXTENSIONS = frozenset({
    ".csv", ".json", ".jsonl", ".log", ".db", ".sqlite", ".sqlite3", ".txt",
})

EXCLUDED_NAMES = frozenset({
    "manifest.json", "genome_cluster_library.json",
    # Retired inventory snapshot; left on older volumes, never evidence.
    "sync_inventory_current.json",
    # Mutable crash-recovery state belongs only to the Fly collector. It can
    # change between reads and is not research/analyzer evidence.
    "research_events_v22.provisional.json",
    # High-frequency operational storage telemetry is exposed through the
    # status API; it is not research evidence.
    "collector_storage_state.json",
    # Ephemeral in-memory paper-position projection; the canonical record is
    # paper_lifecycle_v1.json.
    "open_positions.json",
    # Non-research operational logs. Numbered rotations inherit the exclusion.
    "bot_runtime.log",
    "bot_stdout.log",
    "bot_stderr.log",
    "bot_restart.log",
    "bot_supervisor.log",
    "analyzer_run_latest.log",
    "near_edge.log",
    "signal_persist.log",
    "bot.log",
    "relay-state-pusher.log",
    "relay-state-pusher-stdlib.log",
    # Ops handoff journal only; sealed cancellation evidence lives in ledgers.
    "cancellation_evidence_handoffs.jsonl",
})

# Fly-local validation caches rewritten on every append to their ledger; they
# race every snapshot attempt and are not evidence.
EXCLUDED_SUFFIXES = (".jsonl.validation.json",)

# Runtime-relative patterns (fnmatch, case-sensitive). The lifecycle pipeline
# worker's request/result files are transient IPC handoffs deleted within
# seconds; the evidence they produce is written to the v3 ledgers.
EXCLUDED_PATH_GLOBS = (
    "v3/lifecycle_worker/pipeline-request-*.json",
    "v3/lifecycle_worker/pipeline-result-*.json",
)

EXCLUDED_DIR_NAMES = frozenset({
    # Per-object writer locks are transient coordination state.
    ".locks",
    ".data-sync-snapshots",
    "research_epoch_quarantine",
    "research_archive",
    "research_session_archives",
    "archive-v2",
    "object-store",
    "object_store",
    # Ops reset trees and rebuildable caches, not live research evidence.
    "research_reset_receipts",
    "signal_snapshots_v1",
    "lifecycle_transfer_bundles",
    "analyzer_generations",
    "epoch_quarantine",
})
