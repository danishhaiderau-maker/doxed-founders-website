from research.ai_matched_comparison import compare_ai_selection
from research.discovery_scorecard_publication import _ai_verdict_coverage
from research.discovery_scorecard_publication import _ai_verdict_projection
import pytest


def test_not_called_is_coverage_only_not_reject_or_abstention_pnl():
    source={**row(1,'AI_NOT_CALLED'), 'ai_evaluated':False}
    projected={**source, **_ai_verdict_projection(source)}
    coverage=_ai_verdict_coverage([projected],[])
    assert coverage['raw_verdict_row_counts']['AI_NOT_CALLED']==1
    assert coverage['raw_verdict_row_counts']['REJECT']==0
    assert coverage['raw_verdict_row_counts']['NO_TRADE']==0
    comparison=coverage['matched_selection_comparison']
    assert comparison['matched_rows']==0 and comparison['groups']==[]
    assert comparison['excluded_reason_counts']['AI_NOT_CALLED_NOT_SELECTION_COMPARABLE']==1
    assert projected['ai_evaluated'] is False


@pytest.mark.parametrize('verdict,evaluated', [('AI_NOT_CALLED',True),('AI_NOT_CALLED',None),
    ('AI_NOT_CALLED',0),('REJECT',False),('NO_TRADE',False),('APPROVE',False)])
def test_conflicting_evaluated_flag_is_unknown(verdict,evaluated):
    projected=_ai_verdict_projection({'raw_ai_decision':verdict,'ai_evaluated':evaluated})
    assert projected['ai_verdict_class']=='UNKNOWN'
    assert 'AI_EVALUATED_VERDICT_CONFLICT' in projected['ai_verdict_blockers']


def row(index, verdict='REJECT', pnl=2):
    return dict(epoch_id='epoch',episode_id='episode-'+str(index),opportunity_id='opp-'+str(index),
        source_revision='a',deployed_revision='a',direction='LONG',policy_id='p',policy_signature='sig',
        schedule_sha256='schedule',original_requested_qty=1,tile_config_signature='tile',config_signature='config',
        cost_model_id='cost',simulation_model='model',economics_evidence_basis='REALIZED_COST_COMPLETE',
        tape_hashes=['hash'],tape_ids=['id'],evidence_world='CONSERVATIVE_BBO',
        raw_ai_decision=verdict,raw_ai_direction='LONG',ai_verdict_class=verdict,
        ai_verdict_blockers=[],terminal_complete=True,outcome_state='FULL_FILL',net_pnl_usd=pnl)


def test_actual_publication_comparison_and_same_universe():
    rows=[row(1,pnl=2),row(2,pnl=-3),row(3,'APPROVE',4),row(4,'NO_TRADE',1),
          {**row(5,'APPROVE',6),'raw_ai_direction':'SHORT'}]
    report=_ai_verdict_coverage(rows,[])['matched_selection_comparison']
    group=report['groups'][0]
    assert group['independent_episode_n']==5
    assert group['unfiltered_net_pnl_usd']==10
    assert group['approve_filtered_net_pnl_usd']==4
    assert group['rejected_positive_outcomes']==group['rejected_negative_outcomes']==1
    assert group['abstention_rows']==group['opposite_direction_approval_rows']==1
    assert report['status']=='DESCRIPTIVE_ONLY' and not report['qualification_eligible']


def test_duplicates_missing_cost_and_policy_counts_do_not_inflate_n():
    rows=[row(1),row(1),{**row(2),'net_pnl_usd':None},row(3),
          {**row(3),'policy_id':'p2','policy_signature':'sig2'}]
    report=compare_ai_selection(rows)
    assert len(report['groups'])==2
    assert report['independent_episode_n']==1 and report['matched_rows']==2
    assert report['excluded_rows']==3
    assert all(g['independent_episode_n']==1 for g in report['groups'])
    assert report['excluded_reason_counts']['DUPLICATE_OR_CONFLICTING_MATCHED_SLOT']==2
    assert report['excluded_reason_counts']['COST_COMPLETE_NET_PNL_REQUIRED']==1


def test_missing_direction_and_unsupported_terminal_stay_unknown():
    report=compare_ai_selection([{**row(1,'APPROVE'),'raw_ai_direction':None},
                                 {**row(2),'terminal_complete':False}])
    assert report['status']=='UNKNOWN' and not report['groups']


def test_bounded_work_discards_partial_result():
    report=compare_ai_selection([row(1),row(2)],max_rows=1)
    assert report['status']=='UNKNOWN' and report['groups']==[]
    assert report['blockers']==['AI_COMPARISON_ROW_LIMIT']


def test_eligibility_exclusions_and_conditional_scope():
    report=compare_ai_selection([
        {**row(1),'raw_ai_decision':'APPROVE'},
        {**row(2),'evidence_world':'IDEAL_TOUCH'},
        {**row(3),'economics_evidence_basis':None},
        {**row(4),'outcome_state':'NO_FILL','net_pnl_usd':0},
        row(5),
    ])
    assert report['matched_rows']==1 and report['excluded_rows']==4
    reasons=report['excluded_reason_counts']
    assert reasons['AI_VERDICT_UNAVAILABLE_OR_CONFLICTING']==1
    assert reasons['EXECUTION_EVIDENCE_WORLD_REQUIRED']==1
    assert reasons['ECONOMICS_BASIS_REQUIRED']==1
    assert reasons['SUPPORTED_TERMINAL_FILL_REQUIRED']==1
    assert report['excluded_outcome_counts']=={'FULL_FILL':3,'NO_FILL':1}
    assert report['selection_scope']=='CONDITIONAL_ON_SUPPORTED_COST_COMPLETE_FILLED_TERMINAL_PATHS'
    assert not report['full_opportunity_expectancy_supported']
    assert not report['entry_rate_supported']
