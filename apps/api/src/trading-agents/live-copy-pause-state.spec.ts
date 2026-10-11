import { liveCopyPauseState } from './live-copy-ops.service';

describe('liveCopyPauseState', () => {
  it('reports an armed account paused by the showcase bot', () => {
    const s = liveCopyPauseState({ status: 'ACTIVE', lastError: 'Showcase bot paused on dashboard — x', lastPassExitStage: 'SHOWCASE_PAUSED' });
    expect(s).toMatchObject({ paused: true, pause_owner: 'FLY_BOT', pause_source: 'EXECUTOR_LAST_ERROR', executor_last_pass_exit_stage: 'SHOWCASE_PAUSED' });
  });
  it('uses the last transition for a PAUSED account', () => {
    const s = liveCopyPauseState({ status: 'PAUSED', transition: { actor: 'SAFETY', reason: 'POSITION_MISMATCH', at: '2026-10-11T08:00:20Z' } });
    expect(s).toMatchObject({ paused: true, pause_owner: 'SAFETY', pause_reason: 'POSITION_MISMATCH', paused_at: '2026-10-11T08:00:20Z' });
  });
  it('is not paused when active with no pause error', () => {
    expect(liveCopyPauseState({ status: 'ACTIVE', lastError: null }).paused).toBe(false);
  });
});
