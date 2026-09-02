"""collector_v2.2 — locked schema constants and research horizon policy."""
from __future__ import annotations

import hashlib
import json

COLLECTOR_VERSION = "collector_v2.2"
POLICY_ID = "CONTROL_V1"
POLICY_IDENTITY_SCHEMA = "policy_identity_v1"
PATH_WINDOW_POLICY_ID = "PATH_V1_60M_ENTRY_120M_HOLD"
DECISION_TREE_SCHEMA = "decision_tree_v2.2"
EVENT_SCHEMA = "research_event_v2.2"
PRE_SIGNAL_CONTEXT_SCHEMA = "pre_signal_context_v1"
RESEARCH_EVENTS_FILE = "research_events_v22.jsonl"
EVENT_INDEX_FILE = "research_events_v22.index.json"
EVENT_SQLITE_INDEX_FILE = "research_events_v22.index.sqlite3"
STORAGE_STATE_FILE = "collector_storage_state.json"
EPISODE_SCHEMA = "event_episode_v1"
EPISODE_FALLBACK_WINDOW_SEC = 300.0

CONTROL_TTL_SEC = 1800.0
MAX_ENTRY_WINDOW_SEC = 3600.0
MAX_HOLD_PERIOD_SEC = 7200.0
POST_TTL_LOOKAHEAD_SEC = 1800.0

PRE_SIGNAL_HORIZONS = {
    "1m": {"bars": 1440, "seconds": 86400.0, "label": "24h"},
    "5m": {"bars": 2016, "seconds": 604800.0, "label": "7d"},
    "15m": {"bars": 2880, "seconds": 2592000.0, "label": "30d"},
    "1h": {"bars": 2160, "seconds": 7776000.0, "label": "90d"},
}

PRIMARY_ACCEPTED_FILLED = "ACCEPTED_FILLED"
PRIMARY_ACCEPTED_UNFILLED = "ACCEPTED_UNFILLED"
PRIMARY_REJECTED = "REJECTED"
PRIMARY_OUTCOMES = (
    PRIMARY_ACCEPTED_FILLED,
    PRIMARY_ACCEPTED_UNFILLED,
    PRIMARY_REJECTED,
)

OBS_COMPLETE = "COMPLETE"
OBS_PATH_COMPLETE = "PATH_COMPLETE"
OBS_FUNNEL_COMPLETE = "FUNNEL_COMPLETE"
OBS_WAITING_120M = "WAITING_120M"
OBS_WAITING_ENTRY_WINDOW = "WAITING_ENTRY_WINDOW"
OBS_INSUFFICIENT_PATH = "INSUFFICIENT_PATH"
OBS_UNSUPPORTED_HORIZON = "UNSUPPORTED_RESEARCH_HORIZON"
OBS_DATA_ERROR = "DATA_ERROR"
OBS_STORAGE_PRESSURE = "STORAGE_PRESSURE"
OBS_PENDING = "PENDING"

LIFECYCLE_EVENT_OPENED = "EVENT_OPENED"
LIFECYCLE_OBSERVATION_APPEND = "OBSERVATION_APPEND"
LIFECYCLE_EVENT_FINALIZED = "EVENT_FINALIZED"
LEGACY_PREMATURE_FINALIZATION = "LEGACY_V22_PREMATURE_FINALIZATION"
REPLAY_INELIGIBLE = "REPLAY_INELIGIBLE"

PATH_ORIGIN_SIGNAL = "SIGNAL"
PATH_ORIGIN_ACTUAL_FILL = "ACTUAL_FILL"
PATH_ORIGIN_HYPOTHETICAL_FILL = "HYPOTHETICAL_FILL"

EVAL_PASS = "PASS"
EVAL_FAIL = "FAIL"
EVAL_NOT_EVALUATED = "NOT_EVALUATED"
EVAL_NOT_APPLICABLE = "NOT_APPLICABLE"
EVAL_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

GATE_STRATEGY = "STRATEGY"
GATE_RISK = "RISK"
GATE_EXECUTION = "EXECUTION"
GATE_ADMIN = "ADMIN"
GATE_STATE = "STATE"

STORAGE_PRESSURE_THRESHOLD = 0.85
TICKS_1S_MAX_SEC = 600.0

RESEARCH_HORIZON_V1 = {
    "schema": "research_horizon_v1",
    "control_ttl_sec": CONTROL_TTL_SEC,
    "max_entry_window_sec": MAX_ENTRY_WINDOW_SEC,
    "max_hold_period_sec": MAX_HOLD_PERIOD_SEC,
    "path_window_policy_id": PATH_WINDOW_POLICY_ID,
}


def build_policy_identity(*, epoch_id: str, control_cell: dict, invert_on: bool) -> dict:
    """Canonical identity for the policy that generated an observation.

    Collection epochs describe storage/reset boundaries. Policy epochs describe
    homogeneous decision treatment and therefore split whenever invert or any
    strategy parameter changes.
    """
    policy_spec = {
        "base_policy_id": POLICY_ID,
        "invert_on": bool(invert_on),
        "orig_offset_pct": control_cell.get("orig_offset_pct"),
        "thesis_cut": control_cell.get("thesis_cut"),
        "ladder": control_cell.get("ladder"),
        "hard_stop_pct": control_cell.get("hard_stop_pct"),
        "path_window_policy_id": PATH_WINDOW_POLICY_ID,
    }
    material = json.dumps(policy_spec, sort_keys=True, separators=(",", ":"))
    signature = "policy-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    epoch_material = f"{epoch_id}|{signature}"
    policy_epoch_id = "policy-epoch-" + hashlib.sha256(epoch_material.encode("utf-8")).hexdigest()[:24]
    return {
        "schema": POLICY_IDENTITY_SCHEMA,
        "base_policy_id": POLICY_ID,
        "policy_signature": signature,
        "policy_epoch_id": policy_epoch_id,
        "collection_epoch_id": str(epoch_id),
        "policy_spec": policy_spec,
    }
