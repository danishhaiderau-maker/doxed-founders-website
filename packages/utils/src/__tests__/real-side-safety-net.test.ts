import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  evaluateRealSideSafetyNetExit,
  SCENARIO_C_LADDER,
} from '../subscriber-exit';

/**
 * Real-side protective safety net — the independent Scenario C math that runs
 * on the Railway executor for every OPEN real Bitfinex lot, even when the
 * showcase mirror path is the default exit decision maker.
 *
 * These tests pin the exact thresholds that map to the 2026-08-07 incident:
 *   - THESIS_FAST_CUT fires at -12% margin, suppressed after +5% MFE.
 *   - HARD_STOP is the absolute floor (-18% default).
 *   - PROFIT_LOCK rungs protect peak MFE through the SCENARIO_C_LADDER.
 *
 * NOTE: thresholds sync'd 2026-08-08 to the live bot (THESIS_MFE_PROTECT_PCT=5.0,
 * first ladder rung 8→5). Peak-margin test inputs use values that cross the NEW
 * thresholds so the logic boundaries (suppression, rung firing) stay covered.
 */
describe('evaluateRealSideSafetyNetExit — independent real-side Scenario C safety net', () => {
  describe('HARD_STOP — the absolute floor (only reachable after MFE protect suppresses fast-cut)', () => {
    // THESIS_FAST_CUT (-12%) fires before HARD_STOP (-18%) whenever peak < +5.
    // So HARD_STOP is only reachable when peak >= +5 (MFE protect suppresses
    // the fast-cut), then the position reverses past -18%.
    it('fires at -18% unrealized margin when peak reached MFE protect (peak=+5)', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -18,
        peakMarginPct: 5,
      });
      assert.equal(result.reason, 'HARD_STOP');
      assert.equal(result.lockFloor, undefined);
    });

    it('fires deeper than -18% when peak exceeded MFE protect', () => {
      // peak 6 > MFE threshold 5 (suppresses fast-cut) but < first ladder rung 8
      // (so no profit-lock floor), so HARD_STOP owns the exit at -25%.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -25,
        peakMarginPct: 6,
      });
      assert.equal(result.reason, 'HARD_STOP');
    });

    it('does NOT fire above -18% (peak >= +5)', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -17.99,
        peakMarginPct: 6,
      });
      assert.equal(result.reason, null);
    });

    it('a -18% move with peak < +5 is caught earlier by THESIS_FAST_CUT at -12%', () => {
      // Documents the priority: fast-cut is the tighter exit and fires first.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -18,
        peakMarginPct: -5,
      });
      assert.equal(result.reason, 'THESIS_FAST_CUT');
    });

    it('honours an explicit tighter hard-stop override (-12) — but fast-cut fires first', () => {
      // -12 unreal + peak -5 satisfies THESIS_FAST_CUT, which is evaluated first.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -12,
        peakMarginPct: -5,
        hardStopMarginPct: -12,
      });
      assert.equal(result.reason, 'THESIS_FAST_CUT');
    });

    it('honours an explicit tighter hard-stop override (-12) when fast-cut is suppressed', () => {
      // peak >= +5 suppresses fast-cut; now the tighter hard stop (-12) fires.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -12,
        peakMarginPct: 6,
        hardStopMarginPct: -12,
      });
      assert.equal(result.reason, 'HARD_STOP');
    });
  });

  describe('THESIS_FAST_CUT — deep adverse move before meaningful MFE', () => {
    it('fires at exactly -12% unrealized margin when peak never exceeded +5%', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -12,
        peakMarginPct: 0,
      });
      assert.equal(result.reason, 'THESIS_FAST_CUT');
    });

    it('fires when peak is slightly negative (never profitable)', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -13,
        peakMarginPct: -1,
      });
      assert.equal(result.reason, 'THESIS_FAST_CUT');
    });

    it('does NOT fire when peak reached +5% MFE protect threshold', () => {
      // Trade "proved" itself by hitting +5% peak; let hard stop / profit-lock own the exit.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -12,
        peakMarginPct: 5,
      });
      assert.equal(result.reason, null);
    });

    it('does NOT fire when peak exceeded +5% MFE protect threshold', () => {
      // peak 6 > MFE threshold 5 (fast-cut suppressed) but < first ladder rung 8
      // (no profit-lock floor), and unreal -14 > hard stop -18 → no exit fires.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -14,
        peakMarginPct: 6,
      });
      assert.equal(result.reason, null);
    });

    it('does NOT fire at -11.99 (just above the fast-cut trigger)', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -11.99,
        peakMarginPct: 0,
      });
      assert.equal(result.reason, null);
    });

    it('respects a custom thesis-fast-cut trigger (-15)', () => {
      const at14 = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -14,
        peakMarginPct: -5,
        thesisFastCutMarginPct: -15,
      });
      assert.equal(at14.reason, null);
      const at15 = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -15,
        peakMarginPct: -5,
        thesisFastCutMarginPct: -15,
      });
      assert.equal(at15.reason, 'THESIS_FAST_CUT');
    });
  });

  describe('PROFIT_LOCK — Scenario C ladder protects peak MFE', () => {
    it('fires when peak crossed first rung (8) and unreal fell to lock floor (5)', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 5,
        peakMarginPct: 8,
      });
      assert.equal(result.reason, 'PROFIT_LOCK');
      assert.equal(result.lockFloor, 5);
    });

    it('fires when peak hit 19 and unreal reversed to 17', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 17,
        peakMarginPct: 19,
      });
      assert.equal(result.reason, 'PROFIT_LOCK');
      assert.equal(result.lockFloor, 17);
    });

    it('does NOT fire when unreal is still above the lock floor', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 7,
        peakMarginPct: 8,
      });
      // peak 8 → lock floor 5; unreal 7 still above 5 → no exit
      assert.equal(result.reason, null);
    });

    it('does NOT fire when peak is below the first rung', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 4,
        peakMarginPct: 7.99,
      });
      assert.equal(result.reason, null);
    });

    it('top rung: peak 150 → lock floor 120', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 120,
        peakMarginPct: 150,
      });
      assert.equal(result.reason, 'PROFIT_LOCK');
      assert.equal(result.lockFloor, 120);
    });

    it('honours a custom ladder', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 4,
        peakMarginPct: 12,
        ladder: [[8, 5]] as ReadonlyArray<readonly [number, number]>,
      });
      assert.equal(result.reason, 'PROFIT_LOCK');
      assert.equal(result.lockFloor, 5);
    });
  });

  describe('priority — profit-lock is evaluated before thesis-fast-cut', () => {
    it('peak above first rung with unreal at -12 returns PROFIT_LOCK not THESIS_FAST_CUT', () => {
      // Edge case: peak hit 8 (rung 0, lock floor 5), then price reversed hard
      // to -12. The profit-lock floor (5) requires unreal <= 5 to fire — at
      // -12 it DOES fire, returning PROFIT_LOCK with floor 5. This is correct:
      // once peak crossed the rung, the lock floor is the authoritative exit.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -12,
        peakMarginPct: 8,
      });
      assert.equal(result.reason, 'PROFIT_LOCK');
      assert.equal(result.lockFloor, 5);
    });
  });

  describe('regression — the 2026-08-07 orphan scenario', () => {
    // cont-de8f316fd3c0 entered at 65,016 (LONG). Showcase exited at 64,811.59
    // (-$6.30 paper). The real position was never closed by the mirror path.
    // At 100x leverage:
    //   - entry 65,016, exit 64,811.59 → price move -0.314% → margin -31.4%
    // Without the safety net, the position would have stayed open indefinitely.
    // With the safety net, it closes via HARD_STOP at -18% margin (price -0.18%,
    // i.e. mark ≈ 64,899) well before reaching -31%.
    it('safety net catches the position at THESIS_FAST_CUT (-12% margin) before HARD_STOP', () => {
      // The real position moved past -12% margin (price -0.12% at 100x,
      // mark ≈ 64,938) before ever reaching -18%. With peak below +5% MFE
      // protect, THESIS_FAST_CUT fires — the safety net closes the orphan
      // at a ~$2.40 loss instead of the ~$6.30 the showcase realized, and
      // infinitely better than the unmanaged position the user had to flatten.
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -12,
        peakMarginPct: -5,
      });
      assert.equal(result.reason, 'THESIS_FAST_CUT');
    });

    it('HARD_STOP catches positions that proved themselves (peak >= +5) then reversed', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -18,
        peakMarginPct: 6,
      });
      assert.equal(result.reason, 'HARD_STOP');
    });
  });

  describe('no exit — the steady state', () => {
    it('returns null for a fresh, modestly profitable position', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 1.5,
        peakMarginPct: 1.5,
      });
      assert.equal(result.reason, null);
    });

    it('returns null for a small adverse move within all thresholds', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: -3,
        peakMarginPct: 0,
      });
      assert.equal(result.reason, null);
    });

    it('returns null for a profitable position between ladder rungs', () => {
      const result = evaluateRealSideSafetyNetExit({
        unrealMarginPct: 25,
        peakMarginPct: 25,
      });
      assert.equal(result.reason, null);
    });
  });
});

describe('evaluateRealSideSafetyNetExit — ladder invariant preservation', () => {
  it('every lock floor is below its trigger (no loosen-up)', () => {
    for (const [trigger, lock] of SCENARIO_C_LADDER) {
      assert.ok(
        lock < trigger,
        `lock ${lock} must be below trigger ${trigger} (else the safety net would exit too early)`,
      );
    }
  });
});
