import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
MODULE_PATH = AGENT / "chase_offset_touch_grid.py"
ENGINE_PATH = ROOT / "services" / "btc-signal-engine" / "engine.py"
ANALYZER_PATH = AGENT / "analyzer_research_engine_v62.py"


def _load_grid():
    spec = importlib.util.spec_from_file_location("compressed_shadow_grid", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _armed(module):
    return module.arm_compressed_shadow_chase(
        trade_id="shadow-1",
        direction="LONG",
        signal_price=100.0,
        signal_ts=1_000.0,
        initial_limit_price=99.9,
        shared_ai_call_id="ai-1",
        opportunity_id="opp-1",
        episode_id="episode-1",
        epoch_id="epoch-1",
    )


def test_exact_compressed_schedule_and_terminal_identity_are_immutable():
    module = _load_grid()
    assert module.COMPRESSED_SHADOW_STAGE_SECONDS == (0, 60, 120, 240, 420, 600)
    assert module.COMPRESSED_SHADOW_EXPIRY_SEC == 780

    state, stage_zero = _armed(module)
    rows = [stage_zero]
    for seconds in (60, 120, 240, 420, 600, 780):
        rows.extend(
            module.poll_compressed_shadow_chase(
                state,
                now_ts=1_000.0 + seconds,
                last=101.0,
                bid=100.9,
                ask=101.1,
            )
        )

    stage_rows = [row for row in rows if row["event"] == "STAGE"]
    terminal_rows = [row for row in rows if row["event"] == "EXPIRED"]
    assert [row["stage_due_sec"] for row in stage_rows] == [0, 60, 120, 240, 420, 600]
    assert len(terminal_rows) == 1
    identity = {
        key: stage_zero[key]
        for key in (
            "trade_id",
            "shared_ai_call_id",
            "opportunity_id",
            "episode_id",
            "epoch_id",
            "policy_id",
            "policy_signature",
        )
    }
    assert all(all(row[key] == value for key, value in identity.items()) for row in rows)


def test_terminal_is_idempotent_and_no_post_terminal_provisional_event_exists():
    module = _load_grid()
    state, _ = _armed(module)
    first = module.poll_compressed_shadow_chase(
        state, now_ts=1_780.0, last=101.0, bid=100.9, ask=101.1
    )
    assert [row["event"] for row in first].count("EXPIRED") == 1
    assert module.poll_compressed_shadow_chase(
        state, now_ts=2_000.0, last=102.0, bid=101.9, ask=102.1
    ) == []


def test_shadow_receipts_cannot_be_mistaken_for_paper_or_live_execution():
    module = _load_grid()
    state, receipt = _armed(module)
    for row in (state, receipt):
        assert row["execution_class"] == "SHADOW_ONLY"
        assert row["places_order"] is False
        assert row["relay_eligible"] is False
        assert "qty" not in row
        assert "order_id" not in row
        assert "client_order_id" not in row
    source = MODULE_PATH.read_text(encoding="utf-8-sig")
    assert "requests." not in source
    assert "submit_order" not in source
    assert "place_order" not in source


def test_runtime_analyzer_dashboard_and_download_provenance_are_explicit():
    engine = ENGINE_PATH.read_text(encoding="utf-8-sig")
    analyzer = ANALYZER_PATH.read_text(encoding="utf-8-sig")

    assert "CHASE_OFFSET_TOUCH_GRID_FILE" in engine
    assert "arm_compressed_shadow_chase" in engine
    assert "poll_compressed_shadow_chase" in engine
    assert "missed_opportunity_proof_report.json" in analyzer
    assert "missed_opportunity_proof_v1" in analyzer
    assert "chase_policy_lab_report.json" in analyzer
    assert "chase_policy_lab_v1" in analyzer
    assert "PROVEN_MISSED_PROFIT" in analyzer
    assert "PROVEN_AVOIDED_LOSS" in analyzer
    assert "AMBIGUOUS" in analyzer
    assert "INSUFFICIENT_EVIDENCE" in analyzer
    # The same constant is used by collection plus the runtime data inventory
    # and download surfaces; do not create a second, drifting literal path.
    assert engine.count("CHASE_OFFSET_TOUCH_GRID_FILE") >= 5
