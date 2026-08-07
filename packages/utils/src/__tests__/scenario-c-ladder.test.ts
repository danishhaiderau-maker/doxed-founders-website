import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  SCENARIO_C_LADDER,
  SUBSCRIBER_TRAIL_LADDER,
  solveScenarioCRung,
  getProfitLockFloor,
  computeProfitLockStopPrice,
} from '../subscriber-exit';

describe('SCENARIO_C_LADDER — canonical values match Python scenario_c_config.py', () => {
  it('matches the Python TRAIL_LADDER_SCENARIO_C exactly (8→5 ... 150→120, 8 rungs)', () => {
    // Canonical source: services/btc-conservative-agent/scenario_c_config.py
    // SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_8_v6_20260806"
    // Synced 2026-08-08: (8,5) first rung + (12,10) rung 1 (Danish decision 2026-08-06).
    assert.deepEqual(
      [...SCENARIO_C_LADDER],
      [
        [8, 5],
        [12, 10],
        [19, 17],
        [40, 28],
        [60, 45],
        [80, 60],
        [100, 75],
        [150, 120],
      ],
    );
  });

  it('legacy alias SUBSCRIBER_TRAIL_LADDER points at the same ladder', () => {
    assert.equal(SUBSCRIBER_TRAIL_LADDER, SCENARIO_C_LADDER);
  });

  it('triggers are strictly increasing', () => {
    for (let i = 1; i < SCENARIO_C_LADDER.length; i++) {
      assert.ok(
        SCENARIO_C_LADDER[i][0] > SCENARIO_C_LADDER[i - 1][0],
        `trigger at index ${i} must exceed index ${i - 1}`,
      );
    }
  });

  it('each lock floor is below its trigger (lock < trigger)', () => {
    for (const [trigger, lock] of SCENARIO_C_LADDER) {
      assert.ok(lock < trigger, `lock ${lock} must be below trigger ${trigger}`);
    }
  });
});

describe('solveScenarioCRung — rung solver', () => {
  it('returns null below the first trigger', () => {
    assert.equal(solveScenarioCRung(0), null);
    assert.equal(solveScenarioCRung(-5), null);
    assert.equal(solveScenarioCRung(Number.NaN), null);
  });

  it('returns null at 7.99% (just below first rung)', () => {
    assert.equal(solveScenarioCRung(7.99), null);
  });

  it('rung 0 at exactly 8% (first trigger crossed)', () => {
    assert.equal(solveScenarioCRung(8), 0);
  });

  it('rung 0 at 11% (only the first trigger crossed)', () => {
    assert.equal(solveScenarioCRung(11), 0);
  });

  it('rung 1 at exactly 12% (second trigger crossed)', () => {
    assert.equal(solveScenarioCRung(12), 1);
  });

  it('rung 2 at exactly 19% (third trigger crossed)', () => {
    assert.equal(solveScenarioCRung(19), 2);
  });

  it('rung 2 at 20% (still below the 40 trigger)', () => {
    assert.equal(solveScenarioCRung(20), 2);
  });

  it('rung 3 at exactly 40%', () => {
    assert.equal(solveScenarioCRung(40), 3);
  });

  it('rung 4 at exactly 60%', () => {
    assert.equal(solveScenarioCRung(60), 4);
  });

  it('rung 5 at exactly 80%', () => {
    assert.equal(solveScenarioCRung(80), 5);
  });

  it('rung 6 at exactly 100%', () => {
    assert.equal(solveScenarioCRung(100), 6);
  });

  it('rung 7 at exactly 150% (top rung)', () => {
    assert.equal(solveScenarioCRung(150), 7);
  });

  it('rung 7 at 200% (clamps to highest)', () => {
    assert.equal(solveScenarioCRung(200), 7);
    assert.equal(solveScenarioCRung(1_000_000), 7);
  });
});

describe('getProfitLockFloor — lock floor per peak', () => {
  it('null below first rung', () => {
    assert.equal(getProfitLockFloor(0), null);
    assert.equal(getProfitLockFloor(7.99), null);
  });

  it('5% floor at 8% peak (rung 0 protects 5)', () => {
    assert.equal(getProfitLockFloor(8), 5);
  });

  it('10% floor at 12% peak (rung 1 protects 10)', () => {
    assert.equal(getProfitLockFloor(12), 10);
  });

  it('17% floor at 19% peak (rung 2 protects 17)', () => {
    assert.equal(getProfitLockFloor(19), 17);
  });

  it('28% floor at 40% peak', () => {
    assert.equal(getProfitLockFloor(40), 28);
  });

  it('120% floor at 150% peak', () => {
    assert.equal(getProfitLockFloor(150), 120);
  });
});

describe('computeProfitLockStopPrice — margin% → price conversion', () => {
  // With 100x leverage, 5% margin = 0.05% price move (5 / (100 * 100)).
  // Verified against the Python scenario_c_config ladder + the showcase bot's
  // per-trade exit path: a profit-lock stop at margin floor F is placed at
  //   entry * (1 ± F/(100*leverage))
  // sign chosen so the stop is INSIDE the entry (tighter than entry on the
  // profitable side).
  it('LONG @ 100x, 5% floor → entry * 1.0005 (above entry)', () => {
    const stop = computeProfitLockStopPrice(100_000, 'LONG', 5, 100);
    // 5 / (100 * 100) = 0.0005 → 100000 * 1.0005 = 100050
    assert.equal(stop, 100_050);
  });

  it('SHORT @ 100x, 5% floor → entry * 0.9995 (below entry)', () => {
    const stop = computeProfitLockStopPrice(100_000, 'SHORT', 5, 100);
    // 100000 * (1 - 0.0005) = 99950
    assert.equal(stop, 99_950);
  });

  it('LONG @ 100x, 17% floor → entry * 1.0017', () => {
    const stop = computeProfitLockStopPrice(100_000, 'LONG', 17, 100);
    assert.equal(stop, 100_170);
  });

  it('LONG @ 100x, 120% floor → entry * 1.012 (top rung)', () => {
    const stop = computeProfitLockStopPrice(100_000, 'LONG', 120, 100);
    assert.equal(stop, 101_200);
  });

  it('never loosens: LONG stop strictly increases as floor rises', () => {
    let prev = 0;
    for (const [, lock] of SCENARIO_C_LADDER) {
      const stop = computeProfitLockStopPrice(100_000, 'LONG', lock, 100);
      assert.ok(stop > prev, `LONG stop ${stop} must exceed previous ${prev}`);
      prev = stop;
    }
  });

  it('never loosens: SHORT stop strictly decreases as floor rises', () => {
    let prev = Number.POSITIVE_INFINITY;
    for (const [, lock] of SCENARIO_C_LADDER) {
      const stop = computeProfitLockStopPrice(100_000, 'SHORT', lock, 100);
      assert.ok(stop < prev, `SHORT stop ${stop} must be below previous ${prev}`);
      prev = stop;
    }
  });

  it('leverage divisor: 50x leverage doubles the price distance for the same floor', () => {
    const stop100x = computeProfitLockStopPrice(100_000, 'LONG', 5, 100);
    const stop50x = computeProfitLockStopPrice(100_000, 'LONG', 5, 50);
    // 5 / (100 * 50) = 0.001 → 100100 (allow FP epsilon)
    assert.ok(Math.abs(stop50x - 100_100) < 1e-6, `50x stop ${stop50x} ≈ 100100`);
    assert.ok(stop50x > stop100x, 'lower leverage = wider price distance for the same margin %');
  });
});
