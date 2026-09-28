"""Frozen execution/relay contract shared by every active research tile."""
from __future__ import annotations

CONTRACT_VERSION = "EXPERIMENTAL_TILE_EXECUTION_V1"
REQUIRED_IDENTITY_FIELDS = (
    "research_lane", "raw_policy_id", "policy_id", "trade_id",
    "shared_ai_call_id", "epoch_id",
)


def effective_route(*, tile_enabled: bool, relay_armed: bool,
                    relay_eligible: bool, safety_ready: bool = True) -> dict:
    """Return the only permitted route; tile ON never directly means LIVE."""
    paper = bool(tile_enabled and safety_ready)
    live_copy = bool(paper and relay_armed and relay_eligible)
    return {
        "contract_version": CONTRACT_VERSION,
        "shadow_collect": True,
        "paper_order_eligible": paper,
        "bitfinex_copy_eligible": live_copy,
        "direct_exchange_submit": False,
        "reason": (
            "TILE_OFF_SHADOW_ONLY" if not tile_enabled else
            "SAFETY_GATE_BLOCK" if not safety_ready else
            "PAPER_AND_RELAY_COPY" if live_copy else "PAPER_ONLY_RELAY_OFF"
        ),
    }


def validate_tile_spec(lane: str, spec: dict) -> None:
    required = ("raw_policy_id", "id_prefix", "entry_offset_pct", "entry_ttl_sec")
    missing = [key for key in required if spec.get(key) in (None, "")]
    if missing:
        raise ValueError(f"{lane}: missing frozen tile fields {missing}")
    if spec.get("platform_relay_eligible") and spec.get("paper_only"):
        raise ValueError(f"{lane}: relay-eligible tile cannot be declared paper_only")


def relay_event_blockers(event: dict, *, supported_operations=()) -> list[str]:
    """Fail closed when causal identity or a required copy operation is absent."""
    blockers = [f"MISSING_{key.upper()}" for key in REQUIRED_IDENTITY_FIELDS if not event.get(key)]
    operation = str(event.get("operation") or event.get("event") or "").upper()
    supported = {str(value).upper() for value in supported_operations}
    if operation and operation not in supported:
        blockers.append(f"UNSUPPORTED_RELAY_OPERATION_{operation}")
    return blockers
