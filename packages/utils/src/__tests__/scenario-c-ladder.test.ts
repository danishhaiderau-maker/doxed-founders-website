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
  it('matches the Python TRAIL_LADDER_SCENARIO_C exactly (10→6 ... 150→120)', () => {
    // Canonical source: services/btc-conservative-agent/scenario_c_config.py
    // SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_10_v4"
    // Updated 2026-06-25 (wider first rung 10→6, reduces runner cuts).
    assert.deepEqual(
      [...SCENARIO_C_LADDER],
      [
        [10, 6],
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

  it('returns null at 9.99% (just below first rung)', () => {
    assert.equal(solveScenarioCRung(9.99), null);
  });

  it('rung 0 at exactly 10% (first trigger crossed)', () => {
    assert.equal(solveScenarioCRung(10), 0);
  });

  it('rung 0 at 15% (only the first trigger crossed)', () => {
    assert.equal(solveScenarioCRung(15), 0);
  });

  it('rung 1 at exactly 19% (second trigger crossed)', () => {
    assert.equal(solveScenarioCRung(19), 1);
  });

  it('rung 1 at 20% (still below the 40 trigger)', () => {
    assert.equal(solveScenarioCRung(20), 1);
  });

  it('rung 2 at exactly 40%', () => {
    assert.equal(solveScenarioCRung(40), 2);
  });

  it('rung 3 at exactly 60%', () => {
    assert.equal(solveScenarioCRung(60), 3);
  });

  it('rung 4 at exactly 80%', () => {
    assert.equal(solveScenarioCRung(80), 4);
  });

  it('rung 5 at exactly 100%', () => {
    assert.equal(solveScenarioCRung(100), 5);
  });

  it('rung 6 at exactly 150% (top rung)', () => {
    assert.equal(solveScenarioCRung(150), 6);
  });

  it('rung 6 at 200% (clamps to highest)', () => {
    assert.equal(solveScenarioCRung(200), 6);
    assert.equal(solveScenarioCRung(1_000_000), 6);
  });
});

describe('getProfitLockFloor — lock floor per peak', () => {
  it('null below first rung', () => {
    assert.equal(getProfitLockFloor(0), null);
    assert.equal(getProfitLockFloor(9.99), null);
  });

  it('6% floor at 10% peak (rung 0 protects 6)', () => {
    assert.equal(getProfitLockFloor(10), 6);
  });

  it('17% floor at 19% peak (rung 1 protects 17)', () => {
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
  // With 100x leverage, 6% margin = 0.06% price move (6 / (100 * 100)).
  // Verified against the Python scenario_c_config ladder + the showcase bot's
  // per-trade exit path: a profit-lock stop at margin floor F is placed at
  //   entry * (1 ± F/(100*leverage))
  // sign chosen so the stop is INSIDE the entry (tighter than entry on the
  // profitable side).
  it('LONG @ 100x, 6% floor → entry * 1.0006 (above entry)', () => {
    const stop = computeProfitLockStopPrice(100_000, 'LONG', 6, 100);
    // 6 / (100 * 100) = 0.0006 → 100000 * 1.0006 = 100060
    assert.equal(stop, 100_060);
  });

  it('SHORT @ 100x, 6% floor → entry * 0.9994 (below entry)', () => {
    const stop = computeProfitLockStopPrice(100_000, 'SHORT', 6, 100);
    // 100000 * (1 - 0.0006) = 99940
    assert.equal(stop, 99_940);
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
    const stop100x = computeProfitLockStopPrice(100_000, 'LONG', 6, 100);
    const stop50x = computeProfitLockStopPrice(100_000, 'LONG', 6, 50);
    // 6 / (100 * 50) = 0.0012 → 100120 (allow FP epsilon)
    assert.ok(Math.abs(stop50x - 100_120) < 1e-6, `50x stop ${stop50x} ≈ 100120`);
    assert.ok(stop50x > stop100x, 'lower leverage = wider price distance for the same margin %');
  });
});
