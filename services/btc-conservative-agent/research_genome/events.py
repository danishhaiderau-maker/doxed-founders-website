"""Versioned research event definitions."""
from __future__ import annotations

EVENT_VERSIONS = {
    "AI_SCAN_COMPLETE": 1,
    "AI_APPROVED": 1,
    "AI_REJECTED": 1,
    "LIMIT_CREATED": 1,
    "LIMIT_CHASED": 1,
    "ORDER_FILLED": 1,
    "ORDER_CANCELLED": 1,
    "ORDER_EXPIRED": 1,
    "POSITION_OPENED": 1,
    "POSITION_UPDATED": 1,
    "POSITION_CLOSED": 1,
    "MFE_UPDATED": 1,
    "MAE_UPDATED": 1,
    "LADDER_STEP_ARMED": 1,
    "LADDER_STEP_HIT": 1,
    "STOP_UPDATED": 1,
    "THESIS_CHANGED": 1,
    "EXIT_TRIGGERED": 1,
}

SCHEMA_VERSION = "1.0.0"
