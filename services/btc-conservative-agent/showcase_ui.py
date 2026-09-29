#!/usr/bin/env python3
"""Execution-mirror helpers for doxxedcrypto.digital — sync APIs + warehouse route guards."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from flask import jsonify

from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    ACTIVE_TILE_REGISTRY,
    COMPARISON_BENCHMARK_LANE,
    active_tile_registry_signature,
)

# Research warehouse routes blocked on execution mirror (exports + mass file wipe).
# Full operator dashboard, toggles, pathway lab, and trade history remain available.
WAREHOUSE_BLOCKED_ROUTES = frozenset({
    "/api/toggle_fresh_collection",
    "/api/export_csv",
    "/api/export.csv",
    "/api/export_debug",
    "/api/download_debug_config",
    "/api/reset",
})


def showcase_tile_roster() -> dict:
    """Ordered tile roster for the showcase, resolved from the canonical registry."""
    return {
        "lanes": list(ACTIVE_TILE_ORDER),
        "labels": {lane: ACTIVE_TILE_REGISTRY[lane]["label"] for lane in ACTIVE_TILE_ORDER},
        "registry_signature": active_tile_registry_signature(),
        "comparison_label": COMPARISON_BENCHMARK_LANE,
        "comparison_places_orders": False,
    }


def _blocked_handler():
    return jsonify({
        "error": "Research warehouse export not available on execution mirror",
        "runtime_mode": "EXECUTION_MIRROR",
        "hint": "Use Fresh start ($500) for session reset; CSV/JSONL exports stay on research bot.",
    }), 404


def load_manifest_versions() -> dict:
    """Load signal engine manifest for sync status."""
    local = Path(__file__).resolve().parent / "manifest.json"
    manifest_path = local if local.is_file() else Path(__file__).resolve().parent.parent / "btc-signal-engine" / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        with manifest_path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_sync_status(bot_module=None) -> dict:
    manifest = load_manifest_versions()
    showcase_version = None
    if bot_module is not None:
        showcase_version = getattr(bot_module, "EXECUTION_FIX_VERSION", None)

    signal_hash = manifest.get("signal_hash")
    parity_ok = True
    parity_status = "PASS (manifest)"

    if bot_module is not None:
        try:
            from hashlib import sha256
            bot_path = Path(bot_module.__file__).resolve()
            bot_text = bot_path.read_text(encoding="utf-8")
            live_hash = sha256(bot_text.encode("utf-8")).hexdigest()[:12]
            engine_path = Path(__file__).resolve().parent.parent / "btc-signal-engine" / "engine.py"
            if engine_path.is_file():
                engine_hash = sha256(engine_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:12]
                parity_ok = live_hash == engine_hash == (signal_hash or live_hash)
                parity_status = "PASS" if parity_ok else f"DRIFT bot={live_hash} engine={engine_hash}"
        except Exception as exc:
            parity_status = f"UNKNOWN ({exc})"
            parity_ok = False

    return {
        "research_source": manifest.get("source", "bybit-15m-research-bot/bybit_bot.py"),
        "research_version": manifest.get("engine_version"),
        "showcase_version": showcase_version or manifest.get("engine_version"),
        "signal_hash": signal_hash,
        "parity_ok": parity_ok,
        "parity_status": parity_status,
        "manifest_updated_at": manifest.get("updated_at"),
        "tile_roster": showcase_tile_roster(),
    }


def perform_showcase_fresh_start(bot_module) -> dict:
    """Lightweight operator reset — memory + balance only, no research CSV archive."""
    bot_module.reset_runtime_state()
    bot_module.reset_session_risk_state()
    bot_module.bot_start_time = time.time()
    with bot_module.state_lock:
        bot_module.state["last_fresh_reset_ts"] = time.time()
        bot_module.state["last_fresh_reset_summary"] = "showcase fresh start (no research archive)"
        bot_module.state["bot_start_time"] = bot_module.bot_start_time
    if hasattr(bot_module, "_write_research_session"):
        bot_module._write_research_session(bot_module.bot_start_time)
    if hasattr(bot_module, "save_persistent_config"):
        bot_module.save_persistent_config()
    return {
        "ok": True,
        "account_balance": bot_module.STARTING_BALANCE,
        "ts": bot_module.utc_iso() if hasattr(bot_module, "utc_iso") else None,
    }


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _should_block_research_warehouse(block_warehouse: bool | None) -> bool:
    """Block CSV/JSONL warehouse dumps on execution-mirror hosts only.

    Fly ``doxed-btc-bot`` is the paper research owner (collection writers +
    owner dashboard). SHOWCASE_AGENT must not imply EXECUTION_MIRROR.
    """
    if block_warehouse is not None:
        return bool(block_warehouse)
    if _truthy_env("EXECUTION_MIRROR_ONLY"):
        return True
    if _truthy_env("HOME_RESEARCH_FULL"):
        return False
    if _truthy_env("BLOCK_RESEARCH_WAREHOUSE"):
        return True
    return False


def register_showcase_ui(app, bot_module=None, *, block_warehouse: bool | None = None) -> None:
    """Keep full bot dashboard; optionally block research warehouse exports; add sync APIs."""
    block = _should_block_research_warehouse(block_warehouse)

    if block:
        for rule in list(app.url_map.iter_rules()):
            if rule.rule in WAREHOUSE_BLOCKED_ROUTES:
                app.view_functions[rule.endpoint] = _blocked_handler

    @app.route("/api/sync_status")
    def api_sync_status():
        return jsonify(build_sync_status(bot_module))

    @app.route("/api/fresh_start", methods=["POST"])
    def api_fresh_start():
        if bot_module is None:
            return jsonify({"ok": False, "error": "bot module unavailable"}), 500
        try:
            result = perform_showcase_fresh_start(bot_module)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)[:300]}), 500
