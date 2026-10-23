"""Tile 6/7 removal (23 Oct 2026): entry-fidelity enrichment stays per chain for every tile, the DISLOC
IOC/MARKET carve-out and dsl- fill-rate shadow are gone (a dsl- chain gets no special treatment)."""
import live_copy_monitor as lcm


def test_entry_fidelity_enrichment_is_per_chain():
    chains = [{"correlation_id": "gs1-1", "order_sent_at_ts": 10.0, "exchange_ack_at_ts": 10.2, "fill_at_ts": 10.25,
               "fill_price": 100030.0, "order_type": "LIMIT"}]
    twins = {"gs1-1": {"side": "LONG", "signal_price": 100000.0, "entry_price": 100010.0,
                       "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "signal_ask_age_ms": 40}}
    out = lcm.enrich_entry_fidelity(chains, twins)
    assert out == {"schema": "entry_fidelity_v2", "chains": 1}
    c = chains[0]
    assert c["fill_model"] == "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY" and c["signal_ask_age_ms"] == 40
    assert c["rested_s"] == 0.05 and c["slippage_vs_signal_bp"] == 3.0 and c["slippage_vs_paper_bp"] == 2.0


def test_no_dsl_carve_outs_remain():
    src = open("live_copy_monitor.py", encoding="utf-8").read()
    assert 'startswith("dsl-")' not in src and "DISLOC fill rate" not in src
    api = open("live_copy_monitor_api.py", encoding="utf-8").read()
    assert 'startswith("dsl-")' not in api
