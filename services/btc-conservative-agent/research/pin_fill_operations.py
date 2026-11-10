"""Offline calls into unchanged fill_model. No case IDs, golden data or I/O."""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

from . import fill_model


def evaluate_fill(data):
    entrypoint = data["entrypoint"]
    kwargs = dict(data.get("kwargs", {}))
    if entrypoint in ("taker_fill_rows", "maker_fill_rows"):
        rows = {row["bucket_ts"]: row for row in data["rows"]}
        if len(rows) != len(data["rows"]):
            raise ValueError("DUPLICATE_SYNTHETIC_SECOND")
        function = {"taker_fill_rows": fill_model.taker_fill_rows, "maker_fill_rows": fill_model.maker_fill_rows}[entrypoint]
        realistic = function(rows, **kwargs)
        out = dict(realistic=realistic)
        if data.get("report_fees") and realistic.get("fill_price") is not None:
            out["fees"] = fill_model.fee_fields(realistic["fill_price"] * realistic["filled_qty"], maker=realistic["liquidity"] == "MAKER")
        if data.get("report_size_walk"):
            out["size_walk"] = realistic["size_walk"]
        if data.get("shadow"):
            out["optimistic_shadow"] = fill_model.optimistic_touch_rows(rows, side=kwargs["side"], schedule=kwargs["schedule"])
    elif entrypoint == "size_walk":
        out = dict(realistic=dict(status="N/A_COMPONENT", size_walk=fill_model.size_walk(**kwargs)))
    elif entrypoint == "realistic_exit_margin":
        margin, index = fill_model.realistic_exit_margin(**kwargs)
        out = dict(realistic=dict(margin_pct=margin, booked_idx=index))
    elif entrypoint == "fee_fields_pair":
        maker = fill_model.fee_fields(data["notional_usd"], maker=True)
        taker = fill_model.fee_fields(data["notional_usd"], maker=False)
        out = dict(realistic=dict(fee_maker=maker, fee_taker=taker, FEE_PROFILE_ID=maker["fee_profile_id"]))
    else:
        raise ValueError("UNSUPPORTED_FILL_ENTRYPOINT")
    if "tc" in data:
        out["tc"] = data["tc"]  # Metadata, never a revived historical cost gate.
    return out


def historical_control(data):
    residual = data["mapped_cost_bp"] - data["realistic_cost_bp"]
    return dict(historical_control_rejected=residual < -data["tol_bp"], residual_cost_bp=residual, strategy_gate=False)
