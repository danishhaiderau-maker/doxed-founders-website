#!/usr/bin/env python3
"""Loopback-only saved-state viewer. Never imports or starts the bot runtime."""
from __future__ import annotations

import argparse
import ast
import html
import json
import os
from pathlib import Path
import re
import sys
import time

# The documented CLI also uses -B; dependencies must not write bytecode either.
sys.dont_write_bytecode = True

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from combo_pathway_config import ACTIVE_TILE_REGISTRY, ACTIVE_TILE_ORDER, tile_card_sections as combo_tile_card_sections

SERVICE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
MAX_JSON_BYTES = 8 * 1024 * 1024
READ_FILES = ("debug_state_snapshot.json", "pathway_lane_specs.json", "paper_lifecycle_v1.json")
CONTROL_NAME = re.compile(r"(^|_)(LIVE|ARM|ARMED)(_|$)", re.I)
CREDENTIAL_NAMES = frozenset({
    "DATABASE_URL", "NEON_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "FLY_API_TOKEN",
    "RAILWAY_TOKEN", "RAILWAY_API_TOKEN", "BOT_ADMIN_TOKEN", "BOT_CONTROL_SECRET",
    "CREDENTIALS_ENCRYPTION_KEY", "DEEPSEEK_API_KEY",
})


class ViewerRefused(ValueError):
    """A fixed, non-secret diagnostic safe to display on the console."""


def validate_environment(environ=None):
    # Presence, not truthiness: even an empty key or LIVE_*=0 must be removed.
    names = os.environ if environ is None else environ
    if any(CONTROL_NAME.search(name) or name.upper().startswith(("BITFINEX_", "BFX_"))
           or name.upper() in CREDENTIAL_NAMES for name in names):
        raise ViewerRefused(
            "Viewer refused: live/arm, Bitfinex key or remote credential environment names "
            "are present. Unset them for this process; values are never logged."
        )


def load_frontend():
    """Read ONLY literal presentation constants. No import, exec, eval or bot handlers.

    Keeping the literals in bot.py preserves the legacy dashboard and its source
    contract tests. AST literal extraction avoids copying 2,800 lines of UI.
    Unexpected/nonliteral templates fail closed rather than falling back to bot.
    """
    tree = ast.parse((SERVICE / "bot.py").read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"HTML", "DASHBOARD_JS"}:
                    if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                        raise ViewerRefused("Viewer refused: frontend must remain literal text.")
                    found[target.id] = ast.literal_eval(node.value)
    if set(found) != {"HTML", "DASHBOARD_JS"}:
        raise ViewerRefused("Viewer refused: dashboard frontend unavailable.")
    return found


def _read_json(root, name):
    path = root / name
    # Resolve again on EVERY read; never follow a link outside the approved root.
    try:
        path.resolve().relative_to(root)
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ViewerRefused("Viewer refused: saved JSON exceeds the read cap.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ViewerRefused("Viewer refused: saved JSON must be an object.")
        _check_disarmed(value)
        if name == "paper_lifecycle_v1.json" and value.get("paper_only") is not True:
            raise ViewerRefused("Viewer refused: lifecycle is not explicitly paper-only.")
        return value, path.stat().st_mtime
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, ViewerRefused):
            raise
        raise ViewerRefused("Viewer refused: saved JSON is unreadable, invalid or outside data root.") from None


def _check_disarmed(value):
    if isinstance(value, dict):
        for key, child in value.items():
            control = str(key).lower() in {
                "armed", "live_armed", "live_enabled", "bitfinex_live_enabled",
                "live_copy_armed", "relay_armed", "bitfinex_live_armed", "effective_live",
                "live_copy_output_enabled", "live_copy_source_enabled", "live_tiles_on",
                "bitfinex_live_on", "live_tiles_requested",
            }
            if control and child not in (False, None, 0, "", "0", "false", {}, []):
                raise ViewerRefused("Viewer refused: saved live/arm state is enabled or uncertain.")
            _check_disarmed(child)
    elif isinstance(value, list):
        for child in value:
            _check_disarmed(child)


def _safe_text(value):
    # Legacy tile renderer uses innerHTML; evidence strings must remain text.
    if isinstance(value, str):
        return html.escape(value, quote=True)
    if isinstance(value, dict):
        return {key: _safe_text(child) for key, child in value.items()
                if not any(word in str(key).lower() for word in ("secret", "token", "password", "api_key"))}
    if isinstance(value, list):
        return [_safe_text(child) for child in value]
    return value


def read_snapshot(root):
    sources = {}
    saved = {}
    for name in READ_FILES:
        data, mtime = _read_json(root, name)
        saved[name] = data
        sources[name] = {"available": data is not None, "mtime": mtime,
                         "age_sec": max(0, time.time() - mtime) if mtime is not None else None}
    snapshot = saved["debug_state_snapshot.json"] or {}
    specs = snapshot.get("pathway_lane_specs") or saved["pathway_lane_specs.json"] or {}
    if not isinstance(specs, dict) or not isinstance(specs.get("lanes", []), list):
        raise ViewerRefused("Viewer refused: invalid saved tile specification.")
    existing = {}
    for row in specs.get("lanes", []):
        if not isinstance(row, dict) or not isinstance(row.get("lane"), str):
            raise ViewerRefused("Viewer refused: invalid saved tile row.")
        existing[row["lane"]] = row
    lanes = []
    # The CURRENT registry owns the roster. Old B1/B2 evidence cannot resurrect tiles.
    for number, lane in enumerate(ACTIVE_TILE_ORDER, 1):
        registry = ACTIVE_TILE_REGISTRY[lane]
        row = dict(existing.get(lane) or {})
        row.update({"lane": lane, "label": registry["label"], "tile_number": number,
                    "status": "PAPER_ONLY", "toggle_key": None, "default_enabled": False})
        row.setdefault("subtitle", registry.get("subtitle", ""))
        row.setdefault("card_sections", combo_tile_card_sections(lane))
        if "session_stats" in row and not isinstance(row["session_stats"], dict):
            raise ViewerRefused("Viewer refused: invalid saved tile statistics.")
        stats = row.get("session_stats") or {}
        if not all(isinstance(stats.get(key), (int, float)) and not isinstance(stats[key], bool)
                   for key in ("real_fills", "net_pnl_real", "wins", "losses", "approves")):
            row.pop("session_stats", None)
        lanes.append(row)
    # Deliberately do not forward arbitrary saved /api/state: it may contain
    # credentials, live controls or stale claims of liveness. No PnL is recomputed.
    return _safe_text({
        "pathway_lane_specs": {"lanes": lanes, "retired_tiles_note":
            "Saved evidence only. Current code roster; no strategy PASS or promotion is granted."},
        "viewer": {"mode": "READ_ONLY", "live": "OFF", "sources": sources,
                   "snapshot_revision": snapshot.get("git_rev") or snapshot.get("source_git_rev"),
                   "lifecycle_saved_at": (saved["paper_lifecycle_v1.json"] or {}).get("saved_at"),
                   "warning": "HISTORICAL / UNKNOWN freshness: this viewer starts no engine, collector or writers."},
    })


def create_app(data_dir, *, port=7002):
    validate_environment()
    root = Path(data_dir).resolve()
    if not root.is_dir():
        raise ViewerRefused("Viewer refused: data directory must already exist (nothing is created).")
    if not 1 <= port <= 65535:
        raise ViewerRefused("Viewer refused: port must be between 1 and 65535.")
    read_snapshot(root)  # Fail before binding on invalid or armed persisted state.
    frontend = load_frontend()
    app = Flask(__name__, static_folder=None)
    app.config.update(VIEWER_DATA_ROOT=str(root), VIEWER_PORT=port)
    url = f"http://{HOST}:{port}"

    @app.before_request
    def read_only_boundary():
        validate_environment()
        if request.host not in {f"{HOST}:{port}", f"localhost:{port}"}:
            return jsonify(error="Viewer refused: unexpected Host header."), 403
        if request.method not in {"GET", "HEAD"}:
            return jsonify(error="READ_ONLY: mutations and controls are unavailable."), 405

    @app.after_request
    def viewer_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "form-action 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
        )
        return response

    @app.errorhandler(ViewerRefused)
    def refusal(error):
        return jsonify(error=str(error), mode="READ_ONLY", live="OFF"), 503

    @app.get("/")
    def dashboard():
        page = frontend["HTML"]
        for key, value in {
            "__DASHBOARD_URL__": url, "__ANALYZER_URL__": "http://127.0.0.1:9001/details",
            "__AGENT_HUB_URL__": "#", "__DASHBOARD_PORT__": str(port),
            "__BOT_VERSION__": "READ_ONLY_SAVED_VIEWER", "__TILE_ENTRY_OFFSETS__": "saved policy below",
            "__TILE_R17_COUNTS__": "not evaluated by viewer",
            "__ADMIN_ACCESS_CONTROLS__": '<div class="admin-access">READ ONLY — no login or controls.</div>',
        }.items():
            page = page.replace(key, value)
        # Runs before the original dashboard script. No network or filesystem writes.
        page = page.replace("<head>", '<head><script src="/static/dashboard-viewer.js"></script>', 1)
        return page

    @app.get("/static/dashboard.js")
    def dashboard_js():
        script = frontend["DASHBOARD_JS"]
        for key, value in {"__DASHBOARD_PORT__": str(port), "__DASHBOARD_URL__": url,
                           "__LAST_AI_PAYLOAD_JSON__": "{}", "__TILE_REGISTRY_JSON__": "[]"}.items():
            script = script.replace(key, value)
        return script, 200, {"Content-Type": "application/javascript"}

    @app.get("/static/dashboard-viewer.js")
    def viewer_js():
        return (SERVICE / "dashboard_viewer.js").read_text(encoding="utf-8"), 200, {
            "Content-Type": "application/javascript"}

    @app.get("/api/state")
    def api_state():
        return jsonify(read_snapshot(root))

    @app.get("/health")
    def health():
        read_snapshot(root)
        return jsonify(mode="READ_ONLY", live="OFF", engine_started=False, writers_started=False)

    return app


def make_viewer_server(app):
    # No configurable host, reloader, debugger, request threads or engine worker.
    return make_server(HOST, app.config["VIEWER_PORT"], app, threaded=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="Existing local saved-state directory (read-only).")
    parser.add_argument("--port", type=int, default=7002)
    args = parser.parse_args(argv)
    try:
        app = create_app(args.data_dir, port=args.port)
        with make_viewer_server(app) as server:
            print(f"READ_ONLY viewer: http://{HOST}:{args.port} — Live OFF; no engine or writers.")
            server.serve_forever()
    except ViewerRefused as exc:
        print(str(exc), file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
