"""Fail-closed validation shared by the scheduled Fly monitor jobs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class MonitorContractError(ValueError):
    """A monitor endpoint did not provide the required current safe receipt."""


def require_health(payload: Any, *, status: int) -> Mapping[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        raise MonitorContractError("Fly /health liveness response is unavailable")
    if payload.get("probe_contract") != "PROCESS_LIVENESS_ONLY":
        raise MonitorContractError("Fly /health returned an unknown probe contract")
    if payload.get("process_alive") is not True:
        raise MonitorContractError("Fly process liveness failed")
    if not (
        payload.get("force_paper_mode") is True
        and payload.get("live_armed") is False
        and payload.get("bitfinex_live_enabled") is False
    ):
        raise MonitorContractError("Fly bot execution safety drift")
    return payload


def require_ready(payload: Any, *, status: int) -> Mapping[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        raise MonitorContractError("Fly /ready response is unavailable or stale")
    if payload.get("ok") is not True or payload.get("process_ready") is not True:
        raise MonitorContractError("Fly strict strategy readiness failed")
    if not isinstance(payload.get("strategy_progress"), dict):
        raise MonitorContractError("Fly /ready omitted strategy_progress")
    if not isinstance(payload.get("active_tiles"), list):
        raise MonitorContractError("Fly /ready omitted active_tiles")
    return payload


def require_strategy_progress(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    progress = payload["strategy_progress"]
    if progress.get("ok") is not True:
        reasons = progress.get("reasons") or ["UNKNOWN_PROGRESS_FAILURE"]
        raise MonitorContractError(
            "Fly strategy progress stalled: "
            f"reasons={reasons} ws_age={progress.get('ws_age_sec')} "
            f"ai_age={progress.get('ai_age_sec')} "
            f"trade_lock_available={progress.get('trade_lock_available')} "
            f"positions={progress.get('open_positions')} "
            f"pending={progress.get('pending_orders')}"
        )
    return progress


def require_tile_registry(
    payload: Mapping[str, Any],
    *,
    expected_version: str,
    expected_signature: str,
    expected_lanes: Sequence[str],
) -> None:
    runtime_lanes = [row.get("lane") for row in payload["active_tiles"] if isinstance(row, dict)]
    if (
        payload.get("bot_version") != expected_version
        or payload.get("tile_registry_signature") != expected_signature
        or runtime_lanes != list(expected_lanes)
    ):
        raise MonitorContractError(
            "Fly tile registry drift: "
            f"version={payload.get('bot_version')} lanes={runtime_lanes} "
            f"signature={payload.get('tile_registry_signature')}"
        )
