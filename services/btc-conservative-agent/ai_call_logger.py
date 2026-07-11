"""
ai_call_logger.py — Atomic AI call logging with unique ai_call_id.

Problem: ai_input_log.jsonl and ai_reason_research.jsonl are written independently,
making it impossible to definitively pair input features with AI output/decision.
No shared correlation ID exists across the full call lifecycle.

Solution: Single atomic JSONL write per AI call containing:
  ai_call_id, input_features, raw_ai_response, parsed_decision, confidence, timestamp_ms

Also writes a lightweight ai_call_index.jsonl for O(1) lookup by call_id.
"""
from __future__ import annotations

import json
import os
import time
import uuid
import threading

# ── Paths ─────────────────────────────────────────────────────────────────
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
ATOMIC_LOG_PATH = os.path.join(_SERVICE_DIR, "ai_call_atomic.jsonl")
INDEX_PATH = os.path.join(_SERVICE_DIR, "ai_call_index.jsonl")

# ── Thread safety ─────────────────────────────────────────────────────────
_LOCK = threading.Lock()


def generate_call_id(lane_id: str = "") -> str:
    """Generate unique ai_call_id: {lane_prefix}_{uuid_short_12}_{unix_ms}.

    Example: tbhv1_a3f72b1c9d2e_1720658400000
    """
    prefix = (lane_id or "ai").lower().replace("_", "").replace(" ", "-")[:8]
    short = uuid.uuid4().hex[:12]
    now_ms = int(time.time() * 1000)
    return f"{prefix}_{short}_{now_ms}"


def atomic_log(call_id: str, payload: dict, lane_id: str = "") -> None:
    """Write the full AI call as a single atomic JSONL record.

    payload keys (recommended):
      - call_id: str (redundant but self-contained)
      - lane_id: str
      - timestamp_unix_ms: int
      - input_features: dict — the raw features sent to DeepSeek
      - prompt_fragment: str — how the AI was prompted (what was asked)
      - raw_response: str — raw AI output
      - parsed_decision: str — APPROVE / REJECT / BLOCK
      - confidence: int — 0-100
      - bull_score: int — 0-5
      - bear_score: int — 0-5
      - direction: str — LONG / SHORT / NEUTRAL
      - reason_keywords: list[str] — extracted reasoning tokens
      - ai_error: bool — True if the AI call errored
      - ai_error_msg: str — error detail if any
      - latency_ms: int — round-trip time
    """
    payload["call_id"] = call_id
    payload.setdefault("lane_id", lane_id or payload.get("lane_id", ""))
    payload.setdefault("timestamp_unix_ms", int(time.time() * 1000))

    with _LOCK:
        # Atomic write: write + flush + fsync (best-effort on Windows)
        with open(ATOMIC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass

        # Lightweight index for O(1) lookup
        with open(INDEX_PATH, "a", encoding="utf-8") as f:
            index_entry = {
                "call_id": call_id,
                "lane_id": payload.get("lane_id", ""),
                "decision": payload.get("parsed_decision", "?"),
                "confidence": payload.get("confidence", 0),
                "ts": payload.get("timestamp_unix_ms", 0),
                "error": payload.get("ai_error", False),
            }
            f.write(json.dumps(index_entry, ensure_ascii=False) + "\n")
            f.flush()


# ── Convenience wrappers ─────────────────────────────────────────────────

def log_ai_call_start(lane_id: str, input_features: dict) -> str:
    """Called BEFORE the AI call. Returns the call_id to carry forward."""
    call_id = generate_call_id(lane_id)
    payload = {
        "call_id": call_id,
        "lane_id": lane_id,
        "stage": "input",
        "input_features": input_features,
    }
    atomic_log(call_id, payload, lane_id)
    return call_id


def log_ai_call_complete(call_id: str, lane_id: str, response: dict) -> None:
    """Called AFTER the AI call. Complements the start record with output."""
    payload = {
        "call_id": call_id,
        "lane_id": lane_id,
        "stage": "output",
        "parsed_decision": response.get("decision") or response.get("parsed_decision", "?"),
        "confidence": response.get("confidence") or response.get("ai_probability", 0),
        "bull_score": response.get("bull_score", 0),
        "bear_score": response.get("bear_score", 0),
        "direction": response.get("direction", "NEUTRAL"),
        "raw_response": response.get("raw") or response.get("raw_response", ""),
        "reason_keywords": response.get("reasons") or response.get("reason_keywords", []),
        "latency_ms": response.get("latency_ms", 0),
        "ai_error": response.get("error", False),
        "ai_error_msg": response.get("error_msg", ""),
    }
    atomic_log(call_id, payload, lane_id)


# ── Migration note ────────────────────────────────────────────────────────
# After deploying ai_call_logger, add to bot.py signal pipeline:
#
#  from ai_call_logger import log_ai_call_start, log_ai_call_complete
#
#  # Before AI call:
#  call_id = log_ai_call_start(lane_id, input_features)
#
#  # After AI call:
#  log_ai_call_complete(call_id, lane_id, response_dict)
#
# This replaces the scattered writes to ai_input_log.jsonl and
# ai_reason_research.jsonl, keeping them for backward compatibility
# while introducing the atomic record in parallel.
