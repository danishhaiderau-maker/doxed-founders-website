"""Frozen candidate-vs-control forward paper trial (recording only).

A trial freezes two already-registered policies -- a candidate tile and a
control (another tile or the analytical CONTINUOUS label) -- at their exact
registry policy signatures.  While enabled it only stamps a trial identity on
evidence rows that those policies produce after the freeze.  It owns no order
path: it never creates, sizes, reprices or relays an order, whether OFF or ON.
Paper orders still come solely from each tile's own toggle and gates.

Default OFF.  Enabling needs both ``FROZEN_POLICY_TRIAL_ENABLED=1`` and a
selection file named by ``FROZEN_POLICY_TRIAL_SELECTION``.  Any registry drift
of a frozen signature fails closed to ``DRIFTED`` and stops stamping.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from combo_pathway_config import ACTIVE_TILE_REGISTRY, COMPARISON_BENCHMARK_LANE

TRIAL_SCHEMA = "frozen_policy_trial_v1"
ENABLED_ENV = "FROZEN_POLICY_TRIAL_ENABLED"
SELECTION_ENV = "FROZEN_POLICY_TRIAL_SELECTION"
ANALYTIC_CONTROL_SIGNATURE = "ANALYTIC_COMPARISON_LABEL_ONLY"
ARMS = ("CANDIDATE", "CONTROL")


def _lane_signature(lane: str, registry: Mapping[str, Mapping[str, Any]]) -> str | None:
    if lane == COMPARISON_BENCHMARK_LANE:
        return ANALYTIC_CONTROL_SIGNATURE
    spec = registry.get(lane)
    signature = spec.get("policy_signature") if isinstance(spec, Mapping) else None
    return str(signature) if signature else None


def _trial_signature(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(material), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def freeze_selection(
    candidate_lane: str, control_lane: str, *, selected_by: str, frozen_at_ts: float,
    registry: Mapping[str, Mapping[str, Any]] = ACTIVE_TILE_REGISTRY,
) -> dict[str, Any]:
    """Build an immutable selection record; raises on an ineligible pair."""
    candidate = str(candidate_lane or "").strip().upper()
    control = str(control_lane or "").strip().upper()
    if candidate not in registry:
        raise ValueError("FROZEN_TRIAL_CANDIDATE_NOT_REGISTERED")
    if control != COMPARISON_BENCHMARK_LANE and control not in registry:
        raise ValueError("FROZEN_TRIAL_CONTROL_NOT_REGISTERED")
    if candidate == control:
        raise ValueError("FROZEN_TRIAL_CANDIDATE_EQUALS_CONTROL")
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,64}", str(selected_by or "")):
        raise ValueError("FROZEN_TRIAL_SELECTED_BY_INVALID")
    material = {
        "schema": TRIAL_SCHEMA,
        "candidate_lane": candidate,
        "candidate_policy_signature": _lane_signature(candidate, registry),
        "control_lane": control,
        "control_policy_signature": _lane_signature(control, registry),
        "frozen_at_ts": float(frozen_at_ts),
        "selected_by": str(selected_by),
        "paper_only": True,
        "relay_eligible": False,
    }
    trial_signature = _trial_signature(material)
    return {**material, "trial_id": f"frozen-trial-{trial_signature[:16]}",
            "trial_signature": trial_signature}


class FrozenPolicyTrial:
    def __init__(self, selection: Mapping[str, Any] | None = None, *, enabled: bool = False,
                 registry: Mapping[str, Mapping[str, Any]] = ACTIVE_TILE_REGISTRY,
                 load_error: str | None = None) -> None:
        self.enabled = bool(enabled)
        self.selection = dict(selection) if isinstance(selection, Mapping) else None
        self.registry = registry
        self.load_error = load_error

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *,
                 registry: Mapping[str, Mapping[str, Any]] = ACTIVE_TILE_REGISTRY) -> "FrozenPolicyTrial":
        env = os.environ if env is None else env
        enabled = str(env.get(ENABLED_ENV) or "").strip() == "1"
        if not enabled:
            return cls(None, enabled=False, registry=registry)
        path = str(env.get(SELECTION_ENV) or "").strip()
        if not path:
            return cls(None, enabled=True, registry=registry, load_error="SELECTION_PATH_MISSING")
        try:
            selection = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return cls(None, enabled=True, registry=registry,
                       load_error=f"SELECTION_UNREADABLE:{type(exc).__name__}")
        return cls(selection, enabled=True, registry=registry)

    @staticmethod
    def may_create_order() -> bool:
        return False

    def _validation_error(self) -> str | None:
        if self.load_error:
            return self.load_error
        selection = self.selection
        if not isinstance(selection, dict) or selection.get("schema") != TRIAL_SCHEMA:
            return "SELECTION_SCHEMA_INVALID"
        if selection.get("paper_only") is not True or selection.get("relay_eligible") is not False:
            return "SELECTION_NOT_PAPER_ONLY"
        material = {key: selection.get(key) for key in (
            "schema", "candidate_lane", "candidate_policy_signature", "control_lane",
            "control_policy_signature", "frozen_at_ts", "selected_by", "paper_only",
            "relay_eligible",
        )}
        if selection.get("trial_signature") != _trial_signature(material):
            return "SELECTION_SIGNATURE_MISMATCH"
        return None

    def _drift(self) -> list[str]:
        selection = self.selection or {}
        drift = []
        for arm, lane_key, signature_key in (
            ("CANDIDATE", "candidate_lane", "candidate_policy_signature"),
            ("CONTROL", "control_lane", "control_policy_signature"),
        ):
            current = _lane_signature(str(selection.get(lane_key) or ""), self.registry)
            if current != selection.get(signature_key):
                drift.append(f"{arm}_POLICY_SIGNATURE_DRIFT")
        return drift

    def status(self) -> dict[str, Any]:
        base = {"schema": TRIAL_SCHEMA, "enabled": self.enabled, "paper_only": True,
                "relay_eligible": False, "creates_orders": False}
        if not self.enabled:
            return {**base, "state": "OFF", "reason": "DEFAULT_OFF"}
        error = self._validation_error()
        if error:
            return {**base, "state": "INVALID", "reason": error}
        drift = self._drift()
        identity = {key: self.selection.get(key) for key in (
            "trial_id", "trial_signature", "candidate_lane", "candidate_policy_signature",
            "control_lane", "control_policy_signature", "frozen_at_ts", "selected_by",
        )}
        if drift:
            return {**base, "state": "DRIFTED", "reason": ",".join(drift), **identity}
        return {**base, "state": "RECORDING", "reason": None, **identity}

    def assignment(self, lane: Any, *, observed_ts: Any = None) -> dict[str, Any] | None:
        """Trial stamp for a row from a frozen arm created after the freeze."""
        status = self.status()
        if status["state"] != "RECORDING":
            return None
        lane_name = str(lane or "").strip().upper()
        arm = ("CANDIDATE" if lane_name == status["candidate_lane"]
               else "CONTROL" if lane_name == status["control_lane"] else None)
        if arm is None:
            return None
        try:
            ts = float(observed_ts)
        except (TypeError, ValueError):
            return None
        if ts < float(status["frozen_at_ts"]):
            return None
        return {
            "schema": TRIAL_SCHEMA, "trial_id": status["trial_id"],
            "trial_signature": status["trial_signature"], "arm": arm,
            "arm_lane": lane_name,
            "arm_policy_signature": status[f"{arm.lower()}_policy_signature"],
            "paper_only": True, "relay_eligible": False,
        }


if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Write a frozen trial selection file (no runtime effect).")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--selected-by", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    record = freeze_selection(args.candidate, args.control, selected_by=args.selected_by,
                              frozen_at_ts=time.time())
    Path(args.out).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"trial_id": record["trial_id"], "out": args.out}))
