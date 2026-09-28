"""Descriptive AI selection attribution on identical supported research paths.

This is not a portfolio replay: rejecting an entry contributes zero incremental
trade PnL, without assuming released capital can be reused or earns interest.
"""
import json
from collections import Counter, defaultdict
from discovery_cohort_scorecard import _identity, _finite


def compare_ai_selection(rows, *, max_rows=100000):
    slots = defaultdict(list)
    excluded = Counter()
    outcome_counts = Counter()
    excluded_outcomes = Counter()
    count = 0
    for row in rows:
        count += 1
        if count > max_rows:
            return {'status':'UNKNOWN', 'blockers':['AI_COMPARISON_ROW_LIMIT'],
                    'groups':[], 'qualification_eligible':False}
        outcome = row.get('outcome_state')
        outcome = outcome if outcome in {'FULL_FILL','PARTIAL_FILL','NO_FILL','UNKNOWN'} else 'OTHER_OR_MISSING'
        outcome_counts[outcome] += 1
        identity, reasons = _identity(row)
        # Broad slot deliberately excludes provenance: conflicting variants of
        # one opportunity/policy/direction must poison the slot, not separate it.
        slot = tuple(str(row.get(k) or '') for k in
                     ('epoch_id','episode_id','opportunity_id','policy_id','direction','evidence_world'))
        verdict = row.get('ai_verdict_class')
        raw = row.get('raw_ai_decision')
        expected = ('APPROVE' if raw in {'APPROVE','STRONG_APPROVE','SOFT_APPROVE'} else
                    'REJECT' if raw in {'REJECT','SOFT_REJECT'} else
                    'NO_TRADE' if raw == 'NO_TRADE' else
                    'AI_NOT_CALLED' if raw == 'AI_NOT_CALLED' else 'UNKNOWN')
        not_called = verdict == expected == 'AI_NOT_CALLED' and row.get('ai_evaluated') is False
        if not_called:
            reasons.append('AI_NOT_CALLED_NOT_SELECTION_COMPARABLE')
        if (verdict not in {'APPROVE','REJECT','NO_TRADE'} and not not_called) or row.get('ai_verdict_blockers'):
            reasons.append('AI_VERDICT_UNAVAILABLE_OR_CONFLICTING')
        if verdict in {'APPROVE','REJECT','NO_TRADE'} and row.get('ai_evaluated') is False:
            reasons.append('AI_EVALUATED_VERDICT_CONFLICT')
        if expected != verdict or row.get('ai_error_status') == 'ERROR':
            reasons.append('AI_VERDICT_UNAVAILABLE_OR_CONFLICTING')
        if verdict == 'APPROVE' and row.get('raw_ai_direction') not in {'LONG','SHORT'}:
            reasons.append('AI_APPROVAL_DIRECTION_REQUIRED')
        if row.get('terminal_complete') is not True or row.get('outcome_state') not in {'FULL_FILL','PARTIAL_FILL'}:
            reasons.append('SUPPORTED_TERMINAL_FILL_REQUIRED')
        if row.get('evidence_world') not in {'OBSERVED_PAPER','CONSERVATIVE_BBO'}:
            reasons.append('EXECUTION_EVIDENCE_WORLD_REQUIRED')
        if not identity.get('economics_evidence_basis'):
            reasons.append('ECONOMICS_BASIS_REQUIRED')
        pnl = _finite(row.get('net_pnl_usd'))
        if pnl is None:
            reasons.append('COST_COMPLETE_NET_PNL_REQUIRED')
        if not row.get('episode_id'):
            reasons.append('EPISODE_ID_REQUIRED')
        slots[slot].append((identity, verdict, pnl, sorted(set(reasons)), row.get('raw_ai_direction'), outcome))

    groups = {}
    included_episodes, included_opportunities = set(), set()
    excluded_rows = 0
    for slot, entries in slots.items():
        if len(entries) != 1:
            excluded_rows += len(entries)
            excluded_outcomes.update(entry[-1] for entry in entries)
            excluded['DUPLICATE_OR_CONFLICTING_MATCHED_SLOT'] += len(entries)
            continue
        identity, verdict, pnl, reasons, ai_direction, outcome = entries[0]
        if reasons:
            excluded_rows += 1
            excluded_outcomes[outcome] += 1
            for reason in reasons:
                excluded[reason] += 1
            continue
        included_episodes.add((slot[0],slot[1]))
        included_opportunities.add((slot[0],slot[2]))
        dimensions = {k:identity.get(k) for k in (
            'epoch','source_revision','deployed_revision','policy_id','policy_signature',
            'direction','tile_config','config','cost_model','simulation_model',
            'economics_evidence_basis','declared_contract_sha256','quantity')}
        dimensions['evidence_world'] = slot[-1]
        key = json.dumps(dimensions, sort_keys=True)
        group = groups.setdefault(key, {'dimensions':dimensions, 'opportunities':set(),
            'episodes':set(), 'supported_rows':0, 'approved_rows':0, 'rejected_rows':0,
            'abstention_rows':0, 'opposite_direction_approval_rows':0,
            'unfiltered_net_pnl_usd':0., 'approve_filtered_net_pnl_usd':0.,
            'rejected_positive_outcomes':0, 'rejected_negative_outcomes':0,
            'rejected_zero_outcomes':0})
        group['opportunities'].add(slot[2])
        group['episodes'].add(slot[1])
        group['supported_rows'] += 1
        group['unfiltered_net_pnl_usd'] += pnl
        if verdict == 'APPROVE' and ai_direction == identity['direction']:
            group['approved_rows'] += 1
            group['approve_filtered_net_pnl_usd'] += pnl
        elif verdict == 'REJECT':
            group['rejected_rows'] += 1
            group['rejected_positive_outcomes' if pnl > 0 else
                  'rejected_negative_outcomes' if pnl < 0 else 'rejected_zero_outcomes'] += 1
        elif verdict == 'NO_TRADE':
            group['abstention_rows'] += 1
        else:
            group['opposite_direction_approval_rows'] += 1
    result = []
    for group in groups.values():
        group['independent_episode_n'] = len(group.pop('episodes'))
        group['opportunity_n'] = len(group.pop('opportunities'))
        group['filter_minus_unfiltered_usd'] = group['approve_filtered_net_pnl_usd'] - group['unfiltered_net_pnl_usd']
        result.append(group)
    return {'schema':'ai_same_opportunity_selection_v1',
        'status':'DESCRIPTIVE_ONLY' if result else 'UNKNOWN',
        'qualification_eligible':False, 'profitability_supported':False,
        'comparison_basis':'SAME_PATH_APPROVE_AND_DIRECTION_MATCH_ELSE_ZERO_INCREMENTAL_TRADE_PNL',
        'portfolio_replay':False, 'input_rows':count,
        'selection_scope':'CONDITIONAL_ON_SUPPORTED_COST_COMPLETE_FILLED_TERMINAL_PATHS',
        'full_opportunity_expectancy_supported':False, 'entry_rate_supported':False,
        'input_outcome_counts':dict(outcome_counts),
        'excluded_outcome_counts':dict(excluded_outcomes),
        'excluded_rows':excluded_rows, 'matched_rows':count-excluded_rows,
        'independent_episode_n':len(included_episodes),
        'opportunity_n':len(included_opportunities),
        'excluded_reason_counts':dict(excluded), 'groups':result,
        'blockers':[] if result else ['NO_ELIGIBLE_MATCHED_AI_OUTCOMES']}
