import live_copy_monitor as lcm


def _ch(i, anchor, lag):
    return {"account": "a", "fly_intent_emitted_at_ts": anchor, "lags_sec": {"order_sent->exchange_ack": lag}}


def test_last20_and_since_revision():
    chains = {"accounts": {f"c{i}": _ch(i, 100 + i, float(i)) for i in range(30)}}
    w = lcm.lag_stats(chains, since_ts=0, since_process_boot_ts=125)["windows"]
    assert w["last_n_chains"] == 20 and w["last20"]["order_sent->exchange_ack"]["n"] == 20
    assert w["since_process_boot_chains"] == 5 and w["since_process_boot"]["order_sent->exchange_ack"]["max"] == 29.0


def test_no_revision_ts():
    w = lcm.hop_windows({"accounts": {}})
    assert w["since_process_boot"] == {} and w["last20"] == {}
