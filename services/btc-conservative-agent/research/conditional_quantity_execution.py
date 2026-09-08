"""Conditional amount arithmetic, not exchange-acceptance evidence."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from research.venue_quantity_observation import validate_venue_quantity_observation

LABELS = {'evidence_basis':'DECLARED_SIMULATION_CONDITIONAL', 'venue_acceptance':'UNKNOWN',
          'qualification_eligible':False, 'minimum_notional_decision':'UNAVAILABLE',
          'min_notional_treatment':'UNMODELED_VENUE_ACCEPTANCE_CONDITIONAL'}

def validate_conditional_constraints(raw, *, symbol=None):
    value, reasons = validate_venue_quantity_observation(raw, symbol=symbol)
    if value is not None and value['min_notional']['status'] != 'UNAVAILABLE':
        return None, ['CONDITIONAL_NOTIONAL_UNAVAILABLE_REQUIRED']
    return value, reasons

def apply_conditional_quantity_constraints(*, requested_qty, raw_partial_qty, execution_price,
                                          accumulated_qty=0, constraints, symbol=None):
    base = dict(LABELS, schema='conditional_quantity_execution_decision_v1', accepted=False,
                final_classification='UNSUPPORTED', constraints=None, reasons=[],
                raw_partial_quantity=0.0, rounded_executable_quantity=0.0,
                accumulated_quantity_before=0.0, accumulated_quantity_after=0.0,
                minimum_lot_decision='UNKNOWN', executable_notional=None)
    normalized, reasons = validate_conditional_constraints(constraints, symbol=symbol)
    def numeric(value, positive):
        if isinstance(value,bool): raise ValueError()
        result=Decimal(str(value))
        if not result.is_finite() or (result <= 0 if positive else result < 0): raise ValueError()
        return result
    try:
        requested=numeric(requested_qty,True); raw=numeric(raw_partial_qty,False)
        price=numeric(execution_price,True); accumulated=numeric(accumulated_qty,False)
        if accumulated>requested: raise ValueError()
    except (ValueError,TypeError,InvalidOperation):
        base['reasons']=reasons+['CONDITIONAL_QUANTITY_INPUT_INVALID']; return base
    if reasons:
        base['reasons']=reasons; return base
    step=Decimal(normalized['quantity_step']); lot=Decimal(normalized['min_lot'])
    raw=min(raw,requested-accumulated)
    rounded=(raw/step).to_integral_value(rounding=ROUND_DOWN)*step
    base.update(constraints=normalized,requested_quantity=float(requested),execution_price=float(price),
        raw_partial_quantity=float(raw),rounded_executable_quantity=float(rounded),
        accumulated_quantity_before=float(accumulated),accumulated_quantity_after=float(accumulated),
        executable_notional=float(rounded*price),minimum_lot_decision='PASS' if rounded>=lot else 'FAIL',
        final_classification='NO_FILL' if accumulated==0 else 'PARTIAL_FILL')
    if rounded<=0 or rounded<lot:
        base['reasons']=['RAW_PARTIAL_ROUNDED_TO_ZERO' if rounded<=0 else 'MINIMUM_LOT_NOT_MET']; return base
    after=accumulated+rounded
    base.update(accepted=True,accumulated_quantity_after=float(after),
                final_classification='FULL_FILL' if after>=requested else 'PARTIAL_FILL')
    return base
