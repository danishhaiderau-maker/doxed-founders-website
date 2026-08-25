import ast
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import paper_policy_offset029 as offset_policy
import paper_policy_offset029_protected as protected_policy
import paper_policy_offset029_regime as regime_policy


ROOT = Path(__file__).parent
BOT = ROOT / "bot.py"
ANALYZER = ROOT / "analyzer_research_engine_v62.py"
LANE = "RESEARCH_LANE_OFFSET_029_ATR_TP_25"


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node)


def _load_function(path: Path, name: str, env: dict):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), env)
    return env[name]


def test_patient_chase_never_advertises_internal_sl_reference_as_protection():
    protection_view = _load_function(
        BOT,
        "_position_protection_view",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": "OFFSET_029_ATR_TP_25",
            "RESEARCH_LANE_OFFSET_029_ATR_PROTECTED": protected_policy.LANE,
            "RESEARCH_LANE_OFFSET_029_ATR_REGIME": regime_policy.LANE,
            "_buf_float": lambda value, default=0.0: float(value or default),
            "offset029_policy": offset_policy,
            "offset029_protected_policy": protected_policy,
            "offset029_regime_policy": regime_policy,
        },
    )

    patient = protection_view({
        "research_lane": "OFFSET_029_ATR_TP_25",
        "sl": 77_936.13,
    })
    assert patient == {
        "sl": None,
        "sl_enforced": False,
        "stop_policy": "NONE_TP_ONLY_RESEARCH",
        "tp": None,
        "tp_policy": "FROZEN_3M_ATR_TP_2_5X",
    }

    patient_with_atr = protection_view({
        "research_lane": "OFFSET_029_ATR_TP_25",
        "dir": "LONG",
        "entry": 78_170.65,
        "atr14_3m": 399.12654179,
        "tp": 79_343.21,
    })
    assert patient_with_atr["tp"] == 79_168.47
    assert patient_with_atr["tp_policy"] == "FROZEN_3M_ATR_TP_2_5X"

    continuous = protection_view({"research_lane": "CONTINUOUS", "sl": 77_936.13})
    assert continuous["sl"] == 77_936.13
    assert continuous["sl_enforced"] is True
    assert continuous["stop_policy"] == "PHASE_STOP_POLICY"
    assert continuous["tp_policy"] == "CONFIGURED_EXIT_POLICY"

    paper_book = _function_source(BOT, "build_paper_order_book")
    relay_view = _function_source(BOT, "_relay_position_row_lite")
    assert paper_book.count("_position_protection_view(") >= 2
    assert "_position_protection_view(row)" in relay_view


def test_patient_chase_pause_blocks_orders_but_tile_off_collects_policy_shadow():
    process = _function_source(BOT, "process_signal")
    spawn = _function_source(BOT, "_spawn_combo_lane")
    lab = _function_source(BOT, "_spawn_lab_combo_shadow")
    cancel_pending = _function_source(BOT, "circuit_breaker_cancel_pending")

    assert "is_patient_chase_lane(event_lane)" in process
    assert "paused_shadow_mode = False" in process
    assert "_manual_pause_block_entry" in process
    assert "SPAWN_SHADOW" in spawn
    assert "TILE_OFF_SHADOW_ONLY" in spawn
    assert "is_patient_chase_lane(target_lane)" in lab
    assert "start_replay_buffer" in lab
    assert "offset029_policy.ENTRY_OFFSET if patient_shadow else 0.0" in lab
    assert 'record_expired=True' in cancel_pending
    assert 'expire_signal=True' in cancel_pending
    assert "start_replay_buffer" not in cancel_pending


def test_offset_outcome_cannot_use_scenario_c_profit_lock():
    begin = _function_source(BOT, "begin_approve_research")
    exits = _function_source(BOT, "_apply_position_exits")
    offset_exit = _function_source(BOT, "_apply_offset_029_atr_exit")

    assert LANE in begin and begin.index("return") < begin.index("start_replay_buffer")
    assert exits.index("_apply_offset_029_atr_exit") < exits.index("_check_phase_margin_stop")
    assert "PROFIT_LOCK_LADDER" not in offset_exit
    assert "offset029_policy.exit_decision" in offset_exit


def test_analyzer_excludes_preserved_policy_mismatch_rows():
    shadow_loader = _function_source(ANALYZER, "_load_shadow_lane_outcome_df")

    assert "OFFSET_029_ATR_TP_25" in shadow_loader
    assert '"OFFSET_029_ATR_PROTECTED"' not in shadow_loader
    assert '"OFFSET_029_ATR_REGIME"' not in shadow_loader
    assert "policy_mismatch_rows_excluded" in shadow_loader
    assert "PATIENT_CHASE_SHADOW_POLICY_IDENTITY_MISSING" in shadow_loader


def test_shadow_loader_keeps_signed_patient_shadow_and_excludes_legacy_mismatch():
    rows = [
        {"research_lane": "OFFSET_029_ATR_TP_25", "policy_version": offset_policy.POLICY_ID, "net_pnl_usd": 2.0},
        {"research_lane": "OFFSET_029_ATR_TP_25", "net_pnl_usd": 99.0},
        {"research_lane": "CONTINUOUS", "net_pnl_usd": 1.0},
    ]
    fn = _load_function(
        ANALYZER,
        "_load_shadow_lane_outcome_df",
        {
            "pd": pd,
            "SHADOW_LANE_OUTCOME_FILE": "unused",
            "_load_jsonl_rows": lambda _path: rows,
            "_session_start_ts": lambda _session: None,
            "filter_df_since_session": lambda df, *_args, **_kwargs: df,
        },
    )
    ranked = fn()
    assert ranked["research_lane"].tolist() == ["OFFSET_029_ATR_TP_25", "CONTINUOUS"]
    assert ranked.attrs["policy_mismatch_rows_excluded"] == 1
    assert rows[1]["net_pnl_usd"] == 99.0  # immutable source evidence untouched


def test_offset_dashboard_gate_places_now_despite_global_chase_and_spread_filters():
    fn = _load_function(
        BOT,
        "evaluate_dashboard_execution_gate",
        {
            "state_lock": nullcontext(),
            "state": {"ai_enabled": True, "leverage": 100},
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "is_patient_chase_lane": lambda lane: lane == offset_policy.LANE,
            "MAX_RESEARCH_LEVERAGE": 100,
            "lane_orders_allowed": lambda _lane: True,
            "ensure_signal_capacity": lambda: True,
            # These global experiment gates must not be consulted for Offset.
            "_signal_spread_gate_blocked": lambda *_args: (_ for _ in ()).throw(AssertionError("spread gate leaked")),
            "dashboard_ai_band_blocks": lambda *_args: (_ for _ in ()).throw(AssertionError("AI band leaked")),
            "get_chase_execution_buckets": lambda: {},
        },
    )
    allowed, reason, defer = fn(
        {"research_lane": offset_policy.LANE, "structural_entry_valid": True},
        {"decision": "APPROVE"},
        stage="promote",
    )
    assert (allowed, reason, defer) == (True, "OK_OFFSET029_REGISTERED_POLICY", False)


def test_offset_resolver_preserves_exact_anchor_without_smart_reanchor():
    fn = _load_function(
        BOT,
        "_resolve_submit_limit_price",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_OFFSET_029_ATR_PROTECTED": protected_policy.LANE,
            "RESEARCH_LANE_OFFSET_029_ATR_REGIME": regime_policy.LANE,
            "is_patient_chase_lane": lambda lane: lane in {offset_policy.LANE, protected_policy.LANE},
            "offset029_policy": offset_policy,
            "_patient_chase_policy": lambda lane: protected_policy if lane == protected_policy.LANE else offset_policy,
            "resolve_entry_limit_price": lambda signal: (signal["planned_limit_price"], "AI_DIRECT"),
            "smart_submit_enabled": lambda: (_ for _ in ()).throw(AssertionError("smart submit leaked")),
        },
    )
    signal = offset_policy.entry_fields("LONG", 70_000)
    signal["research_lane"] = offset_policy.LANE
    planned, mode, meta, raw = fn(signal)
    assert planned == raw == 69_797.0
    assert mode == "AI_DIRECT"
    assert meta["reanchored"] is False
    assert meta["policy_id"] == offset_policy.POLICY_ID


def test_offset_order_path_disables_generic_chase_and_direct_exchange_submit():
    place = _function_source(BOT, "_place_simulated_limit_order")
    touch_grid = _function_source(BOT, "_arm_chase_offset_touch_grid")
    assert "dashboard_exact_chase_managed = not registered_offset_policy" in place
    assert "if relay_publishes_approve_outcome(lane):\n        _relay_mirror" in place
    assert "if not registered_offset_policy:\n        _maybe_bitfinex_limit_entry" in place
    assert "if not registered_offset_policy:\n        _log_shadow_vs_live_entry" in place
    assert "funnel_on_order(signal, order)" in place
    assert "lane_register_pending_order(order)" in place
    assert "FIXED_MARGIN_USDT if raw_margin_usdt is None else raw_margin_usdt" in place
    assert "if margin_usdt <= 0 or margin_usdt > SIGNED_SHOWCASE_MAX_MARGIN_USDT:" in place
    assert "qty = margin_usdt * lev / limit_price" in place
    assert "is_patient_chase_lane" in touch_grid and touch_grid.index("return") < touch_grid.index("arm_touch_grid_rows")


def test_offset_post_approve_path_bypasses_legacy_strategy_gates():
    process = _function_source(BOT, "process_signal")
    assert "registered_offset_policy" in process
    for legacy_gate in (
        "evaluate_profitability_entry_gates",
        "evaluate_golden_stack_filter",
        "evaluate_sr_direction_filter",
        "evaluate_entry_location_filter",
        "evaluate_entry_quality_filter",
        "evaluate_evidence_entry_filter",
        "resolve_entry_margin_usdt",
    ):
        if legacy_gate in process:
            call = process.index(legacy_gate)
            preceding = process[max(0, call - 180):call]
            assert "registered_offset_policy" in preceding, legacy_gate
    assert "if not registered_offset_policy:\n                log_golden_stack_rejection" in process
    assert "if is_combo_execution_lane(research_lane) and not registered_offset_policy" in process


def test_api_matches_patient_chase_lifecycle_by_shared_call_identity():
    fn = _load_function(
        BOT,
        "_attach_patient_chase_routes",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
            "DASHBOARD_PRIMARY_LANES": ("CONTINUOUS", offset_policy.LANE, protected_policy.LANE),
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
        },
    )
    history = [{
        "shared_ai_call_id": "scan-1",
        "lane_verdicts": {"RETIRED_TEST_LANE": {"accepted": True}},
    }]
    pending = [{
        "shared_ai_call_id": "scan-1",
        "trade_id": "o29atr-1",
        "research_lane": offset_policy.LANE,
        "status": "PENDING",
        "limit_price": 69_797.0,
    }]
    enriched, counts = fn(history, pending=pending)
    assert enriched[0]["patient_chase_route"] == {
        "status": "PENDING",
        "lifecycle_status": "PENDING",
        "trade_id": "o29atr-1",
        "limit_price": 69_797.0,
        "entry_price": None,
        "exit_reason": None,
    }
    assert counts == {
        "pending": 1,
        "open": 0,
        "closed": 0,
        "expired": 0,
        "unlinked_lifecycle_rows": 0,
        "selected_calls": 1,
    }
    assert "TYPE_B_HUNTER_V1" not in enriched[0]["lane_verdicts"]


def test_api_recovers_live_patient_child_identity_from_signal_snapshot():
    """A pre-fix child order still matches its canonical parent scan after deploy."""
    fn = _load_function(
        BOT,
        "_attach_patient_chase_routes",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
            "DASHBOARD_PRIMARY_LANES": ("CONTINUOUS", offset_policy.LANE, protected_policy.LANE),
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
        },
    )
    history = [{"shared_ai_call_id": "scan-196e02c33ddb"}]
    signals = [{"signal_ref": {
        "trade_id": "o29atr-a01440a5125e",
        "research_lane": offset_policy.LANE,
        "status": "ORDERED",
        "ai_output": {"trade_id": "scan-196e02c33ddb"},
    }}]
    pending = [{
        "trade_id": "o29atr-a01440a5125e",
        "research_lane": offset_policy.LANE,
        "status": "PENDING",
        "limit_price": 62_817.0,
    }]
    enriched, counts = fn(history, signals=signals, pending=pending)
    assert enriched[0]["patient_chase_route"]["status"] == "PENDING"
    assert enriched[0]["patient_chase_route"]["trade_id"] == "o29atr-a01440a5125e"
    assert counts["pending"] == 1
    assert counts["selected_calls"] == 1


def test_api_counts_patient_order_even_when_parent_ai_identity_is_missing():
    """A visible paper order must never be hidden from the lifecycle totals."""
    fn = _load_function(
        BOT,
        "_attach_patient_chase_routes",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
            "DASHBOARD_PRIMARY_LANES": ("CONTINUOUS", offset_policy.LANE, protected_policy.LANE),
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
        },
    )
    history = [{"shared_ai_call_id": "scan-newer-visible-call"}]
    pending = [{
        "trade_id": "o29atr-legacy-child",
        "research_lane": offset_policy.LANE,
        "status": "PENDING",
        "limit_price": 77_460.99,
    }]

    enriched, counts = fn(history, pending=pending)

    assert enriched[0]["patient_chase_route"]["status"] == "NOT_SELECTED"
    assert counts["pending"] == 1
    assert counts["selected_calls"] == 0
    assert counts["unlinked_lifecycle_rows"] == 1


def test_api_lifecycle_totals_do_not_double_count_same_trade_across_sources():
    fn = _load_function(
        BOT,
        "_attach_patient_chase_routes",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
            "DASHBOARD_PRIMARY_LANES": ("CONTINUOUS", offset_policy.LANE, protected_policy.LANE),
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
        },
    )
    history = [{"shared_ai_call_id": "scan-duplicate"}]
    signals = [{"signal_ref": {
        "shared_ai_call_id": "scan-duplicate",
        "trade_id": "o29atr-same",
        "research_lane": offset_policy.LANE,
        "status": "ORDERED",
    }}]
    pending = [{
        "shared_ai_call_id": "scan-duplicate",
        "trade_id": "o29atr-same",
        "research_lane": offset_policy.LANE,
        "status": "PENDING",
    }]

    _enriched, counts = fn(history, signals=signals, pending=pending)

    assert counts["pending"] == 1
    assert counts["selected_calls"] == 1
    assert counts["unlinked_lifecycle_rows"] == 0


def test_offset_spawn_and_terminal_records_preserve_shared_ai_identity():
    spawn = _function_source(BOT, "_spawn_combo_lane")
    assert "call_id = _shared_ai_call_id(ai_result=ai, ctx=ctx)" in spawn
    assert 'spawn_ctx["shared_ai_call_id"] = call_id' in spawn
    assert 'spawn_ai["shared_ai_call_id"] = call_id' in spawn
    assert '"pre_ai": spawn_ai' in spawn

    position = _function_source(BOT, "_build_open_position")
    market_order = _function_source(BOT, "execute_market_order")
    limit_order = _function_source(BOT, "create_limit_order")
    expired = _function_source(BOT, "_record_expired_order")
    close = _function_source(BOT, "close_position")
    for order_source in (market_order, limit_order):
        assert '"shared_ai_call_id": signal.get("shared_ai_call_id")' in order_source
        assert '"shared_ai_call_ts": signal.get("shared_ai_call_ts")' in order_source
        assert '"source_trade_id": signal.get("source_trade_id")' in order_source
    assert '"shared_ai_call_ts": (' in position
    assert '"shared_ai_call_ts": source.get("shared_ai_call_ts")' in expired
    assert '"research_lane": pos.get("research_lane") or master.get("research_lane")' in close
    assert 'master.get("shared_ai_call_id")' in close


def test_relay_lifecycle_projections_preserve_shared_ai_identity():
    """Authenticated live overlays must not sever Patient Chase from its AI call."""
    for function_name in (
        "_relay_signal_ref_lite",
        "_relay_order_row_lite",
        "_relay_position_row_lite",
        "_relay_trade_row_lite",
    ):
        source = _function_source(BOT, function_name)
        assert '"shared_ai_call_id"' in source, function_name
        assert '"shared_ai_call_ts"' in source, function_name
        assert '"source_trade_id"' in source, function_name


def test_lightweight_dashboard_signal_keeps_patient_join_identity():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_DASHBOARD_ACTIVE_SIGNAL_KEYS"
                for target in node.targets)
    )
    values = {
        item.value
        for item in assignment.value.args[0].elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    assert {"shared_ai_call_id", "shared_ai_call_ts", "source_trade_id"} <= values


def test_ai_scan_fans_out_patient_before_slow_continuous_processing():
    process = _function_source(BOT, "process_signal")
    patient_call = "spawn_combo_lanes_from_ai_scan(\n                        ctx, ai, edge_score, features, research_lane,"
    continuous_call = "spawn_continuous_lane_from_ai_scan(\n                        ctx, ai, edge_score, features, research_lane,"
    assert process.count("spawn_combo_lanes_from_ai_scan(") == 1
    assert patient_call in process
    assert continuous_call in process
    assert process.index(patient_call) < process.index(continuous_call)
    assert "fanout_ctx" not in process


def test_every_shared_call_writes_patient_and_continuous_v3_lane_verdicts():
    patient = _function_source(BOT, "spawn_combo_lanes_from_ai_scan")
    continuous = _function_source(BOT, "spawn_continuous_lane_from_ai_scan")
    bridge = _function_source(BOT, "_write_v3_shared_lane_decision")
    assert "dual_write_lane_decision(" in bridge
    assert "if not ai_accepted:\n            continue" in patient
    write_at = patient.index("_write_v3_shared_lane_decision(")
    reject_continue_at = patient.index("if not ai_accepted:\n            continue", write_at)
    assert write_at < reject_continue_at
    assert "LANE_DISABLED_NO_ORDER" in patient
    assert "AI_REJECTED_NO_ORDER" in patient
    assert "LANE_DISABLED_DATA_ONLY" in continuous
    assert "POLICY_REJECTED_NO_ORDER" in continuous
    assert "_write_v3_shared_lane_decision(" in continuous


def test_v3_lane_identity_separates_local_paper_world_from_relay_capability():
    bridge = _function_source(BOT, "_write_v3_shared_lane_decision")
    identity = _function_source(BOT, "_v3_lane_policy_material")
    assert "_v3_lane_policy_material(lane)" in bridge
    assert '"paper_only": True' in identity
    assert '"relay_eligible": relay_eligible' in identity
    assert 'lane == RESEARCH_LANE_CONTINUOUS' in identity


def test_api_distinguishes_legacy_approved_no_order_from_pending():
    fn = _load_function(
        BOT,
        "_attach_patient_chase_routes",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
            "DASHBOARD_PRIMARY_LANES": ("CONTINUOUS", offset_policy.LANE, protected_policy.LANE),
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
        },
    )
    history = [{"shared_ai_call_id": "scan-old"}]
    signals = [{"signal_ref": {
        "shared_ai_call_id": "scan-old",
        "trade_id": "o29atr-old",
        "research_lane": offset_policy.LANE,
        "status": "AWAITING_DASHBOARD_CHASE",
    }}]
    enriched, counts = fn(history, signals=signals)
    route = enriched[0]["patient_chase_route"]
    assert route["status"] == "APPROVED_NO_ORDER"
    assert route["lifecycle_status"] == "AWAITING_DASHBOARD_CHASE"
    assert counts["pending"] == 0


def test_sanitized_overlay_preserves_verified_patient_route():
    fn = _load_function(
        BOT,
        "_attach_patient_chase_routes",
        {
            "copy": __import__("copy"),
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
            "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
            "DASHBOARD_PRIMARY_LANES": ("CONTINUOUS", offset_policy.LANE, protected_policy.LANE),
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
        },
    )
    history = [{
        "shared_ai_call_id": "scan-cdd7a7c3fb01",
        "patient_chase_route": {
            "status": "OPEN",
            "lifecycle_status": "OPEN",
            "trade_id": "o29atr-deef524bb3a1",
            "limit_price": 78170.65,
            "entry_price": 78170.65,
            "exit_reason": None,
        },
    }]
    # Public relay position has lane/trade identity but deliberately omits the
    # private shared_ai_call_id.  A second enrichment pass must not downgrade it.
    sanitized_positions = [{
        "trade_id": "o29atr-deef524bb3a1",
        "research_lane": offset_policy.LANE,
        "status": "OPEN",
        "entry": 78170.65,
    }]
    enriched, counts = fn(history, positions=sanitized_positions)
    assert enriched[0]["patient_chase_route"]["status"] == "OPEN"
    assert enriched[0]["patient_chase_route"]["trade_id"] == "o29atr-deef524bb3a1"
    assert counts["open"] == 1
    assert counts["selected_calls"] == 1


def test_dashboard_replaces_retired_type_b_column_with_patient_route():
    source = BOT.read_text(encoding="utf-8")
    assert "<th>Type B research verdict</th>" not in source
    assert "<th>Raw AI verdict</th>" in source
    assert "<th>Continuous evaluation</th>" in source
    assert "<th>Patient Chase route</th>" in source
    assert "<th>Static evaluation</th>" in source
    assert "<th>Static route</th>" in source
    assert "<th>Regime evaluation</th>" in source
    assert "<th>Regime route</th>" in source
    assert "<th>AI explanation / block reason</th>" in source
    assert "formatPatientRoute(patientRoute)" in source
    assert "Raw AI verdict, Continuous benchmark evaluation, and Patient Chase execution are separate" in source
    assert "Patient Chase now:" in source
    assert "lifecycle row(s) are missing parent AI identity" in source
    assert "statRow('Pending', laneNow.pending || 0)" in source
    assert "statRow('Open', laneNow.open || 0)" in source
    assert "statRow('Closed', stats.real_fills" in source
    assert "statRow('Executed'" not in source
    assert "PATHWAY_LANE_ORDER = tuple(ACTIVE_TILE_ORDER)" in source
    assert "LEGACY_PATHWAY_LANES" not in source
    assert "RESEARCH_LANE_TYPE_B_HUNTER_V1" not in source
