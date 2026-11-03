"""Laptop-side Bitfinex / live-trading policy gate (day-1 local fresh start).

HM AMBER: replaces the disabled GitHub workflow ``bitfinex-production-gate``
(``.github/workflows/bitfinex-production-gate.yml``) for the local paper soak.

Fail closed when:
* FORCE_PAPER_MODE is not on
* BITFINEX_LIVE_ENABLED / live-arm / relay-arm bits are true
* any Bitfinex private key env **name** has a non-empty value (values never printed)

Writes a receipt JSON for RK/HM read-back. No orders. No network.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

from local_fresh_runtime import (
    FORBIDDEN_TRADE_KEY_NAMES,
    LOCAL_FRESH_CONFIRMATION,
    apply_local_fresh_environ,
    build_health_payload,
    is_local_fresh_start,
    refuse_message,
    validate_paper_safe_env,
    write_health_json,
)

SCHEMA = "local_bitfinex_live_policy_gate_v1"
GATE_ID = "LOCAL_BITFINEX_LIVE_POLICY_GATE"
REPLACES_WORKFLOW = ".github/workflows/bitfinex-production-gate.yml"
HM_AMBER_CITE = "HM AMBER — laptop-side Bitfinex/live policy gate (replaces disabled bitfinex-production-gate)"

# Extra live/arm bits beyond local_fresh_runtime (names only).
LIVE_ARM_ENV_NAMES: tuple[str, ...] = (
    "BITFINEX_LIVE_ENABLED",
    "LIVE_COPY_ARMED",
    "RELAY_ARMED",
    "LIVE_ARMED",
    "BITFINEX_LIVE_ARMED",
    "SHOWCASE_LIVE_ARMED",
    "RELAY_LIVE_COPY_ARMED",
)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def evaluate_gate(
    environ: Optional[Mapping[str, str]] = None,
    *,
    require_local_fresh_confirm: bool = True,
) -> dict[str, Any]:
    """Return a gate verdict dict. Never includes secret values."""
    env = dict(environ if environ is not None else os.environ)
    reasons: list[str] = []
    checks: dict[str, Any] = {
        "force_paper_mode": _truthy(env.get("FORCE_PAPER_MODE")),
        "bitfinex_live_enabled": _truthy(env.get("BITFINEX_LIVE_ENABLED")),
        "trade_key_names_present": [],
        "live_arm_bits_true": [],
        "local_fresh_confirmed": is_local_fresh_start(env),
        "fly_app_name_set": bool(str(env.get("FLY_APP_NAME") or env.get("FLY_APP") or "").strip()),
    }

    if require_local_fresh_confirm and not checks["local_fresh_confirmed"]:
        reasons.append("LOCAL_FRESH_START_NOT_CONFIRMED")

    ok_paper, paper_reasons = validate_paper_safe_env(env)
    if not ok_paper:
        # validate_paper_safe already covers keys / live / fly / confirm when confirm required.
        for r in paper_reasons:
            if r not in reasons:
                # When confirm not required (pure env scan), drop confirm-only reason.
                if not require_local_fresh_confirm and r == "LOCAL_FRESH_START_NOT_CONFIRMED":
                    continue
                reasons.append(r)

    # Explicit arm-bit scan (names only).
    for name in LIVE_ARM_ENV_NAMES:
        if _truthy(env.get(name)):
            checks["live_arm_bits_true"].append(name)
            tag = f"LIVE_ARM_BIT_TRUE:{name}"
            if tag not in reasons:
                reasons.append(tag)

    for name in FORBIDDEN_TRADE_KEY_NAMES:
        if str(env.get(name) or "").strip():
            checks["trade_key_names_present"].append(name)

    if not checks["force_paper_mode"]:
        if "FORCE_PAPER_MODE_REQUIRED" not in reasons:
            reasons.append("FORCE_PAPER_MODE_REQUIRED")

    ok = not reasons
    return {
        "schema": SCHEMA,
        "gate_id": GATE_ID,
        "ok": ok,
        "reasons": reasons,
        "checks": checks,
        "replaces_workflow": REPLACES_WORKFLOW,
        "hm_amber_cite": HM_AMBER_CITE,
        "confirmation_phrase_name": "LOCAL_FRESH_START_CONFIRM",
        "confirmation_phrase_expected": LOCAL_FRESH_CONFIRMATION,
        "ts": time.time(),
    }


def write_gate_receipt(path: str | Path, verdict: Mapping[str, Any]) -> str:
    return write_health_json(path, verdict)


def run_gate(
    environ: Optional[MutableMapping[str, str]] = None,
    *,
    receipt_path: Optional[str | Path] = None,
    apply_defaults: bool = False,
    require_local_fresh_confirm: bool = True,
) -> dict[str, Any]:
    env: MutableMapping[str, str]
    env = environ if environ is not None else os.environ
    if apply_defaults:
        apply_local_fresh_environ(env)
    verdict = evaluate_gate(env, require_local_fresh_confirm=require_local_fresh_confirm)
    if receipt_path:
        write_gate_receipt(receipt_path, verdict)
        # Also refresh local health snapshot when gate is green.
        if verdict["ok"]:
            health = Path(receipt_path).expanduser().resolve()
            # Prefer sibling local_fresh_health.json when receipt is under logs/.
            health_path = health.parent / "local_fresh_health.json"
            write_health_json(
                health_path,
                build_health_payload(
                    ok=True,
                    extra={"bitfinex_live_policy_gate": dict(verdict)},
                ),
            )
    return verdict


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Laptop Bitfinex/live policy gate (replaces bitfinex-production-gate)"
    )
    parser.add_argument(
        "--receipt",
        default="",
        help="Write gate receipt JSON (default: logs/local_bitfinex_live_policy_gate.json)",
    )
    parser.add_argument(
        "--apply-defaults",
        action="store_true",
        help="Force FORCE_PAPER / clear Fly env before evaluate (start-script path)",
    )
    parser.add_argument(
        "--allow-without-local-fresh-confirm",
        action="store_true",
        help="Scan process env without requiring LOCAL_FRESH_START confirm (CI helper)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo = Path(__file__).resolve().parents[2]
    receipt = args.receipt.strip() or str(
        repo / "logs" / "local_bitfinex_live_policy_gate.json"
    )
    verdict = run_gate(
        apply_defaults=bool(args.apply_defaults),
        receipt_path=receipt,
        require_local_fresh_confirm=not bool(args.allow_without_local_fresh_confirm),
    )
    print(json.dumps({"ok": verdict["ok"], "reasons": verdict["reasons"], "receipt": receipt}))
    if not verdict["ok"]:
        print(refuse_message(verdict["reasons"]), file=sys.stderr)
        return 78
    print(f"OK  {GATE_ID} — paper forced, live OFF, trade keys absent")
    print(f"    receipt={receipt}")
    print(f"    replaces={REPLACES_WORKFLOW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
