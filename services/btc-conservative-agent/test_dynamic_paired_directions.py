from test_dynamic_cohort_adapter import row, adapt


def test_both_sides_of_same_opportunity_remain_separate_comparable_groups():
    result = adapt([row(), row(direction='SHORT', net_pnl_usd=-2.0)])
    assert len(result['groups']) == 2
    by_direction = {g['direction']: g for g in result['groups']}
    assert set(by_direction) == {'LONG', 'SHORT'}
    for direction, group in by_direction.items():
        assert len(group['episodes']) == 1
        assert group['episodes'][0]['direction'] == direction
        assert group['episodes'][0]['source_episode_id'] == 'episode-one'
    assert result['counts']['supported_outcomes'] == 2
    assert not result['rejections'].get('INCOMPARABLE_EPISODE_CANDIDATES')
