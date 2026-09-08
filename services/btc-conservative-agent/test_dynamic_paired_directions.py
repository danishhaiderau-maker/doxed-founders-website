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


def test_paired_groups_map_without_merging_or_inflating_source_identity():
    from test_local_dynamic_mapping import fixture, build
    adapted = fixture([row(), row(direction='SHORT', net_pnl_usd=-2.0)])
    mapped = [build(adapted, group_id=g['group_id']) for g in adapted['groups']]
    assert len({m['mapping_sha256'] for m in mapped}) == 2
    assert len({m['protocol_run_id'] for m in mapped}) == 2
    assert {m['training_episodes'][0]['source_episode_id'] for m in mapped} == {'episode-one'}
    outcomes = {m['selected_group']['direction']:
                m['training_episodes'][0]['policy_outcomes']['ENTRY_PLUS_EXIT_A']['net_pnl_usd']
                for m in mapped}
    assert outcomes == {'LONG': 2.5, 'SHORT': -2.0}
    assert all(not m['qualification_allowed'] for m in mapped)
