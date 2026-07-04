"""
A160 Context Chase Exit V2 — independent research candidate.

Own DeepSeek prompt + deterministic checker + independent paper limits
(same virtual-chase order path as AI60_SP3_VIRTUAL_CHASE).

Benchmark safety (enforced by callers in bot.py):
- Never modifies AI_PROMPT_TEMPLATE / RESEARCH_AI_PROMPT_ADDENDUM / CONTINUOUS prompt
- Never updates last_ai_call_ts (uses v2_last_ai_call_ts only)
- Never inherits AI_SCAN decisions into CONTINUOUS
- Never routes to Bitfinex live / live_armed path (paper/sim only in RESEARCH)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from combo_pathway_config import RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2

# --- Constants ----------------------------------------------------------------

V2_LANE = RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2
V2_PROMPT_ID = "A160_CONTEXT_CHASE_EXIT_V2_PROMPT_20260704"
V2_PROMPT_SOURCE_FILE = "tmp_ai_prompt_balanced_direction_v1.md"

V2_AI_INPUT_LOG_FILE = "v2_ai_input_log.jsonl"
V2_AI_DECISION_LOG_FILE = "v2_ai_decision_log.jsonl"
V2_CHECKER_LOG_FILE = "v2_checker_log.jsonl"
V2_SHADOW_OUTCOME_FILE = "v2_shadow_outcome.jsonl"  # legacy shadow outcomes (pre-orders)

V2_MIN_WIN_PROB = 60
V2_MIN_SPREAD = 3
V2_CHASE_TARGET_MIN = 3
V2_CHASE_TARGET_MAX = 5
V2_MIN_SIGNAL_AGE_SEC = 180

# Phase-shifted AI: main AI_SCAN ~every 180s; V2 fires ≥90s after last_ai_call_ts,
# then every ~180s on its own clock. No hourly budget — only anti-spam separation.
V2_RESEARCH_AI_ENABLED_DEFAULT = "1"
V2_AI_OFFSET_FROM_MAIN_SEC = int(os.getenv("V2_AI_OFFSET_FROM_MAIN_SEC", "90"))
V2_RESEARCH_AI_COOLDOWN_SEC = int(os.getenv("V2_RESEARCH_AI_COOLDOWN_SEC", "180"))

_CONTEXT_TOKEN = "{{CONTEXT}}"
_prompt_cache: Optional[str] = None


def v2_research_ai_enabled_env() -> bool:
    raw = os.getenv("V2_RESEARCH_AI_ENABLED", V2_RESEARCH_AI_ENABLED_DEFAULT)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _agent_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def load_v2_prompt_template() -> str:
    """Load draft prompt from markdown ```text fences; cache in-process."""
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    path = os.path.join(_agent_dir(), V2_PROMPT_SOURCE_FILE)
    with open(path, encoding="utf-8") as f:
        md = f.read()
    m = re.search(r"```text\s*\n(.*?)```", md, re.DOTALL)
    if not m:
        raise RuntimeError(f"V2 prompt text fence not found in {V2_PROMPT_SOURCE_FILE}")
    draft = m.group(1).rstrip() + "\n"
    # Safe placeholder: draft uses {context}; JSON examples contain many braces.
    if "{context}" not in draft:
        raise RuntimeError("V2 prompt missing {context} placeholder")
    draft = draft.replace("{context}", _CONTEXT_TOKEN, 1)
    header = (
        f"PROMPT_ID: {V2_PROMPT_ID}\n"
        "This is the immutable A160 Context Chase Exit V2 research prompt.\n\n"
    )
    _prompt_cache = header + draft
    return _prompt_cache


def build_v2_prompt(context_obj: Any) -> str:
    template = load_v2_prompt_template()
    ctx_json = json.dumps(context_obj, indent=2, default=str)
    return template.replace(_CONTEXT_TOKEN, ctx_json, 1)


def parse_v2_structured_audit(text: str) -> dict:
    """Extract STRUCTURED_AUDIT block fields without touching parse_ai_response_fields."""
    audit: dict = {
        "parse_ok": False,
        "context_alignment": None,
        "regime_read": None,
        "sr_bias_read": None,
        "counter_regime_trade": None,
        "counter_regime_thesis": None,
        "reversal_evidence": [],
        "continuation_evidence": [],
        "why_not_wait": None,
        "invalidation_price": None,
        "risk_flags": [],
        "data_quality_flags": [],
        "final_reason": None,
        "score_spread": None,
        "long_score": None,
        "short_score": None,
        "win_probability": None,
        "direction": None,
        "decision": None,
        "confidence": None,
    }
    if not text:
        return audit
    # Prefer JSON block fields when present
    json_blob = {}
    jstart = text.find("```json")
    if jstart >= 0:
        brace = text.find("{", jstart)
        jend = text.find("```", brace + 1)
        chunk = text[brace:jend if jend > brace else len(text)]
        try:
            json_blob = json.loads(chunk.strip().rstrip("`"))
        except Exception:
            json_blob = {}
    if isinstance(json_blob, dict) and json_blob:
        for key in (
            "context_alignment", "regime_read", "sr_bias_read", "counter_regime_trade",
            "counter_regime_thesis", "why_not_wait", "invalidation_price", "final_reason",
            "score_spread", "long_score", "short_score", "win_probability", "direction",
            "decision", "confidence",
        ):
            if key in json_blob and json_blob[key] is not None:
                audit[key] = json_blob[key]
        for list_key in ("reversal_evidence", "continuation_evidence", "risk_flags", "data_quality_flags"):
            val = json_blob.get(list_key)
            if isinstance(val, list):
                audit[list_key] = [str(x) for x in val]
            elif val is not None:
                audit[list_key] = [str(val)]
        audit["parse_ok"] = True

    # Overlay STRUCTURED_AUDIT lines when present
    idx = text.upper().find("STRUCTURED_AUDIT")
    block = text[idx:] if idx >= 0 else ""
    if block:
        def _line(key: str):
            m = re.search(rf"^{re.escape(key)}:\s*(.+)$", block, re.MULTILINE | re.IGNORECASE)
            return m.group(1).strip() if m else None

        for key, dest in (
            ("context_alignment", "context_alignment"),
            ("regime_read", "regime_read"),
            ("sr_bias_read", "sr_bias_read"),
            ("counter_regime_thesis", "counter_regime_thesis"),
            ("why_not_wait", "why_not_wait"),
            ("final_reason", "final_reason"),
            ("direction", "direction"),
            ("decision", "decision"),
        ):
            val = _line(key)
            if val:
                audit[dest] = val
        for key, dest in (
            ("win_probability", "win_probability"),
            ("confidence", "confidence"),
            ("long_score", "long_score"),
            ("short_score", "short_score"),
            ("score_spread", "score_spread"),
            ("invalidation_price", "invalidation_price"),
        ):
            val = _line(key)
            if val is not None:
                try:
                    audit[dest] = float(val.split()[0]) if dest == "invalidation_price" else int(float(val.split()[0]))
                except (TypeError, ValueError):
                    pass
        crt = _line("counter_regime_trade")
        if crt is not None:
            audit["counter_regime_trade"] = str(crt).strip().lower() in ("true", "1", "yes")

        def _bullet_list(header: str) -> list:
            m = re.search(
                rf"^{re.escape(header)}:\s*\n((?:[ \t]*-[ \t]*.+\n?)*)",
                block,
                re.MULTILINE | re.IGNORECASE,
            )
            if not m:
                return []
            items = []
            for line in m.group(1).splitlines():
                line = line.strip()
                if line.startswith("-"):
                    item = line[1:].strip()
                    if item and item.upper() != "NONE":
                        items.append(item)
            return items

        for header, dest in (
            ("reversal_evidence", "reversal_evidence"),
            ("continuation_evidence", "continuation_evidence"),
            ("risk_flags", "risk_flags"),
            ("data_quality_flags", "data_quality_flags"),
        ):
            items = _bullet_list(header)
            if items:
                audit[dest] = items
        audit["parse_ok"] = True

    # Normalize enums
    if audit.get("context_alignment"):
        audit["context_alignment"] = str(audit["context_alignment"]).upper().split()[0]
    if audit.get("regime_read"):
        audit["regime_read"] = str(audit["regime_read"]).upper().split()[0]
    if audit.get("sr_bias_read"):
        audit["sr_bias_read"] = str(audit["sr_bias_read"]).upper().split()[0]
    if audit.get("direction"):
        audit["direction"] = str(audit["direction"]).upper().split()[0]
    if audit.get("decision"):
        audit["decision"] = str(audit["decision"]).upper().split()[0]
    if audit.get("counter_regime_trade") is not None and not isinstance(audit["counter_regime_trade"], bool):
        audit["counter_regime_trade"] = str(audit["counter_regime_trade"]).lower() in ("true", "1", "yes")
    return audit


def compute_v2_direction_spread(direction: str, parsed: dict, audit: dict) -> int:
    """Directional spread from long/short scores, falling back to bull/bear."""
    direction = str(direction or "").upper()
    long_s = audit.get("long_score")
    short_s = audit.get("short_score")
    if long_s is None:
        long_s = (parsed.get("factors") or {}).get("long_score")
    if short_s is None:
        short_s = (parsed.get("factors") or {}).get("short_score")
    try:
        long_s = int(long_s or 0)
        short_s = int(short_s or 0)
    except (TypeError, ValueError):
        long_s, short_s = 0, 0
    if long_s or short_s:
        if direction == "LONG":
            return long_s - short_s
        if direction == "SHORT":
            return short_s - long_s
        return abs(long_s - short_s)
    # Legacy bull/bear
    bull = int(parsed.get("bull_score") or (parsed.get("factors") or {}).get("bull_score") or 0)
    bear = int(parsed.get("bear_score") or (parsed.get("factors") or {}).get("bear_score") or 0)
    if direction == "LONG":
        return bull - bear
    if direction == "SHORT":
        return bear - bull
    return abs(bull - bear)


def _strong_counter_context_proof(audit: dict) -> bool:
    """Escape hatch: structured audit must prove reversal for counter-context trades."""
    thesis = str(audit.get("counter_regime_thesis") or "").strip().upper()
    if not thesis or thesis in ("NONE", "N/A", "NA", "-"):
        return False
    evidence = audit.get("reversal_evidence") or []
    if not evidence:
        return False
    why = str(audit.get("why_not_wait") or "").strip()
    if not why:
        return False
    inv = audit.get("invalidation_price")
    try:
        inv_f = float(inv or 0)
    except (TypeError, ValueError):
        inv_f = 0.0
    if inv_f <= 0:
        return False
    if not audit.get("counter_regime_trade"):
        return False
    return True


def evaluate_a160_context_chase_v2_checker(
    ctx: dict,
    parsed: dict,
    audit: dict,
    edge_score: float = 0.0,
    features: dict = None,
    source_ai: dict = None,
    orders_enabled: bool = True,
) -> dict:
    """Hard gates for V2 paper-order acceptance.

    Authorizes independent paper limits on the V2 lane only — never CONTINUOUS,
    never Bitfinex live. Callers must spawn with V2 research_lane + own trade_id.
    """
    fail_reasons = []
    direction = str(
        parsed.get("direction")
        or audit.get("direction")
        or "NO_TRADE"
    ).upper()
    try:
        win_prob = int(parsed.get("win_prob") or audit.get("win_probability") or 0)
    except (TypeError, ValueError):
        win_prob = 0
    spread = compute_v2_direction_spread(direction, parsed, audit)
    score_spread = audit.get("score_spread")
    try:
        score_spread = int(score_spread) if score_spread is not None else spread
    except (TypeError, ValueError):
        score_spread = spread
    # Prefer explicit score_spread when larger signal of conviction
    effective_spread = max(spread, score_spread)

    if not audit.get("parse_ok"):
        fail_reasons.append("V2_PARSE_FAIL")
    if direction not in ("LONG", "SHORT"):
        fail_reasons.append("NO_DIRECTION")
    if win_prob < V2_MIN_WIN_PROB:
        fail_reasons.append(f"WIN_PROB_LT_{V2_MIN_WIN_PROB}")
    if effective_spread < V2_MIN_SPREAD:
        fail_reasons.append(f"SPREAD_LT_{V2_MIN_SPREAD}")

    regime = str(audit.get("regime_read") or "").upper()
    sr_bias = str(audit.get("sr_bias_read") or "").upper()
    alignment = str(audit.get("context_alignment") or "").upper()

    # Hard veto: BULL+SHORT+LONG_PREFERRED (and symmetric BEAR+LONG+SHORT_PREFERRED)
    # unless strong counter-context proof from structured audit.
    counter_veto = False
    if direction == "SHORT" and regime == "BULL" and sr_bias == "LONG_PREFERRED":
        counter_veto = True
    if direction == "LONG" and regime == "BEAR" and sr_bias == "SHORT_PREFERRED":
        counter_veto = True
    if alignment == "COUNTER_CONTEXT" and not counter_veto:
        # Treat explicit COUNTER_CONTEXT as veto-class unless proof present
        if (direction == "SHORT" and regime == "BULL") or (direction == "LONG" and regime == "BEAR"):
            counter_veto = True

    if counter_veto and not _strong_counter_context_proof(audit):
        fail_reasons.append("CONTEXT_VETO")

    accepted = len(fail_reasons) == 0
    result = {
        "lane": V2_LANE,
        "accepted": accepted,
        "fail_reasons": fail_reasons,
        "direction": direction,
        "win_prob": win_prob,
        "spread": effective_spread,
        "regime_read": regime,
        "sr_bias_read": sr_bias,
        "context_alignment": alignment,
        "counter_veto": counter_veto,
        "counter_context_proof": bool(counter_veto and _strong_counter_context_proof(audit)),
        "chase_target_min": V2_CHASE_TARGET_MIN,
        "chase_target_max": V2_CHASE_TARGET_MAX,
        "min_signal_age_sec": V2_MIN_SIGNAL_AGE_SEC,
        "edge_score": round(float(edge_score or 0), 1),
        "source_trade_id": (ctx or {}).get("trade_id"),
        "source_ai_win_prob": (source_ai or {}).get("win_prob"),
        "source_ai_decision": (source_ai or {}).get("decision"),
        "prompt_id": V2_PROMPT_ID,
        # Paper orders only when tile ON; shadow sim when OFF.
        "shadow_only": not bool(orders_enabled),
        "orders_allowed": bool(accepted and orders_enabled),
        "independent_paper_orders": bool(orders_enabled),
        "v2_parse_ok": bool(audit.get("parse_ok")),
        "structured_audit": audit,
        "parsed": {
            "direction": direction,
            "win_prob": win_prob,
            "decision": parsed.get("decision"),
            "bull_score": parsed.get("bull_score") or (parsed.get("factors") or {}).get("bull_score"),
            "bear_score": parsed.get("bear_score") or (parsed.get("factors") or {}).get("bear_score"),
            "long_score": (parsed.get("factors") or {}).get("long_score") or audit.get("long_score"),
            "short_score": (parsed.get("factors") or {}).get("short_score") or audit.get("short_score"),
        },
    }
    return result


def checked_to_v2_ai(checked: dict, latency_ms: int = 0) -> dict:
    """Build process_signal pre_ai dict from an accepted V2 checker result."""
    parsed = (checked or {}).get("parsed") or {}
    audit = (checked or {}).get("structured_audit") or {}
    factors = {
        "bull_score": parsed.get("bull_score") or 0,
        "bear_score": parsed.get("bear_score") or 0,
        "long_score": parsed.get("long_score") or audit.get("long_score") or 0,
        "short_score": parsed.get("short_score") or audit.get("short_score") or 0,
    }
    return {
        "win_prob": checked.get("win_prob"),
        "direction": checked.get("direction"),
        "decision": "APPROVE",
        "override": False,
        "comment": f"V2_INDEPENDENT prompt_id={V2_PROMPT_ID}",
        "ai_error": False,
        "factors": factors,
        "bull_score": factors.get("bull_score", 0),
        "bear_score": factors.get("bear_score", 0),
        "long_score": factors.get("long_score", 0),
        "short_score": factors.get("short_score", 0),
        "source": "V2_FRESH",
        "approved": True,
        "latency_ms": latency_ms,
        "research_lane": V2_LANE,
        "prompt_id": V2_PROMPT_ID,
        "structured_audit": audit,
        "v2_parse_ok": bool(checked.get("v2_parse_ok")),
        "independent_paper_orders": True,
        "shadow_only": False,
    }


def load_v2_shadow_metrics(cwd: str = None) -> dict:
    """Aggregate V2 checker approves + legacy shadow outcomes for Pathway Lab tile fallback.

    Live paper fills/PnL come from lane_pnl_ledger / trades_3factor.csv (same as AI60).
    This loader supplies approve counts from v2_checker_log when analyzer disk metrics lag.
    """
    base = cwd or os.getcwd()
    outcome_path = os.path.join(base, V2_SHADOW_OUTCOME_FILE)
    checker_path = os.path.join(base, V2_CHECKER_LOG_FILE)
    approves = 0
    fills = 0
    pnl = 0.0
    wins = 0
    losses = 0
    try:
        if os.path.isfile(checker_path):
            with open(checker_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("accepted"):
                        approves += 1
    except Exception:
        pass
    try:
        if os.path.isfile(outcome_path):
            with open(outcome_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not row.get("filled"):
                        continue
                    fills += 1
                    net = float(row.get("net_pnl_usd") or 0)
                    pnl += net
                    if net >= 0:
                        wins += 1
                    else:
                        losses += 1
    except Exception:
        pass
    if approves <= 0 and fills > 0:
        approves = fills
    ev = round(pnl / approves, 2) if approves else 0.0
    win_rate = round(100.0 * wins / fills, 1) if fills else 0.0
    return {
        "approves": approves,
        "real_fills": fills,
        "approve_to_fill_pct": round(100.0 * fills / approves, 1) if approves else 0.0,
        "shadow_fill_pct": round(100.0 * fills / approves, 1) if approves else 0.0,
        "net_pnl_real": round(pnl, 2),
        "per_approve_ev": ev,
        "win_rate_pct": win_rate,
        "verdict": "shadow sim (tile OFF)" if fills else "independent paper orders",
        "lab_mode": False,
        "lab_closes": 0,
        "lab_net_pnl": 0.0,
        "lab_wins": wins,
        "lab_losses": losses,
        "lab_win_rate": round(100.0 * wins / fills, 1) if fills else 0.0,
        "lab_per_close_ev": round(pnl / fills, 2) if fills else 0.0,
    }


def v2_wipe_files() -> list:
    return [
        V2_AI_INPUT_LOG_FILE,
        V2_AI_DECISION_LOG_FILE,
        V2_CHECKER_LOG_FILE,
        V2_SHADOW_OUTCOME_FILE,
    ]
