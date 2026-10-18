import fills_by_model as fbm


def _row(trade_id, **kw):
    return {"trade_id": trade_id, "research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", **kw}


def test_listed_trade_ids_are_identity_quarantined_never_countable():
    for tid in fbm.IDENTITY_QUARANTINED_TRADE_IDS:
        assert fbm.classify(_row(tid)) == "identity_quarantined"
    assert len(fbm.IDENTITY_QUARANTINED_TRADE_IDS) == 4


def test_unlisted_trade_id_is_classified_as_before_and_counts_are_separate():
    assert fbm.classify(_row("gb1-other")) != "identity_quarantined"
    out = fbm.summarise([_row("gb1-746896eb658e"), _row("gb1-other")], ["FAMILY_GSB1_CVD_DIV_REGIME"])
    t = out["tiles"]["FAMILY_GSB1_CVD_DIV_REGIME"]
    assert t["by_category"]["identity_quarantined"] == 1 and t["countable"] == 0 and t["rows"] == 2
