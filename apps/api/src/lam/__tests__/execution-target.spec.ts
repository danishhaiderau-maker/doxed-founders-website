/**
 * Unit tests for the Phase 9 Computer-Use ExecutionTarget factory + the
 * PlaywrightTarget / RealScreenTarget surface.
 *
 *   1. createExecutionTarget — env-var wiring + safe fallback.
 *   2. RealScreenTarget — fails closed when native deps are missing.
 *   3. PlaywrightTarget — display dimensions read from env, returns a
 *      clear "not started" error before start() runs.
 *
 * No Playwright launch is exercised here — that's an integration test
 * the founder runs on a host with Chromium installed. We keep the spec
 * to the deterministic contract so CI never depends on a binary.
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  createExecutionTarget,
  PlaywrightTarget,
  RealScreenTarget,
} from '../execution-target';

function makeConfig(map: Record<string, string>): {
  get: <T = string>(key: string) => T | undefined;
} {
  return {
    get: <T = string>(key: string): T | undefined => map[key] as T | undefined,
  };
}

describe('createExecutionTarget — env-var factory wiring', () => {
  it('defaults to a PlaywrightTarget when LAM_EXECUTION_TARGET is unset', () => {
    const t = createExecutionTarget(makeConfig({}));
    assert.equal(t.id, 'browser');
    assert.ok(t instanceof PlaywrightTarget);
    assert.equal(t.displayWidthPx, 1024);
    assert.equal(t.displayHeightPx, 768);
  });

  it('selects browser when LAM_EXECUTION_TARGET=browser', () => {
    const t = createExecutionTarget(makeConfig({ LAM_EXECUTION_TARGET: 'browser' }));
    assert.equal(t.id, 'browser');
  });

  it('selects screen when LAM_EXECUTION_TARGET=screen (case-insensitive)', () => {
    const t = createExecutionTarget(makeConfig({ LAM_EXECUTION_TARGET: 'SCREEN' }));
    assert.equal(t.id, 'screen');
    assert.ok(t instanceof RealScreenTarget);
    assert.equal(t.displayWidthPx, 1920);
    assert.equal(t.displayHeightPx, 1080);
  });

  it('honours LAM_DISPLAY_WIDTH / LAM_DISPLAY_HEIGHT overrides', () => {
    const t = createExecutionTarget(
      makeConfig({
        LAM_EXECUTION_TARGET: 'browser',
        LAM_DISPLAY_WIDTH: '1280',
        LAM_DISPLAY_HEIGHT: '800',
      }),
    ) as PlaywrightTarget;
    assert.equal(t.displayWidthPx, 1280);
    assert.equal(t.displayHeightPx, 800);
  });

  it('falls back to defaults for non-numeric dimensions', () => {
    const t = createExecutionTarget(
      makeConfig({
        LAM_DISPLAY_WIDTH: 'not-a-number',
        LAM_DISPLAY_HEIGHT: '',
      }),
    ) as PlaywrightTarget;
    assert.equal(t.displayWidthPx, 1024);
    assert.equal(t.displayHeightPx, 768);
  });

  it('screen target honours width/height overrides', () => {
    const t = createExecutionTarget(
      makeConfig({
        LAM_EXECUTION_TARGET: 'screen',
        LAM_DISPLAY_WIDTH: '2560',
        LAM_DISPLAY_HEIGHT: '1440',
      }),
    ) as RealScreenTarget;
    assert.equal(t.displayWidthPx, 2560);
    assert.equal(t.displayHeightPx, 1440);
  });

  it('unknown LAM_EXECUTION_TARGET values fall back to browser', () => {
    const t = createExecutionTarget(makeConfig({ LAM_EXECUTION_TARGET: 'wasm' }));
    assert.equal(t.id, 'browser');
  });
});

describe('RealScreenTarget — fails closed without native deps', () => {
  let target: RealScreenTarget;

  beforeEach(() => {
    target = new RealScreenTarget(1920, 1080);
  });

  it('reports as not running before start()', () => {
    assert.equal(target.isRunning(), false);
  });

  it('execute() before start() returns a clear "not started" error', async () => {
    const r = await target.execute({ type: 'screenshot' });
    assert.equal(r.ok, false);
    assert.match(r.error ?? '', /not started|native/);
  });

  it('start() throws when native screen-control library is missing', async () => {
    // The CI environment has neither @nut-tree-fork/nut-js nor robotjs;
    // start() must throw a clear "install one" error.
    await assert.rejects(
      () => target.start(),
      (err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        return /native|nut-js|robotjs|display/i.test(msg);
      },
    );
    assert.equal(target.isRunning(), false);
  });

  it('stop() is idempotent and never throws', async () => {
    await target.stop();
    await target.stop();
    assert.equal(target.isRunning(), false);
  });
});

describe('PlaywrightTarget — contract before start()', () => {
  let target: PlaywrightTarget;

  beforeEach(() => {
    target = new PlaywrightTarget(makeConfig({}) as never);
  });

  it('reports as not running before start()', () => {
    assert.equal(target.isRunning(), false);
  });

  it('execute() before start() returns a clear "not started" error', async () => {
    const r = await target.execute({ type: 'screenshot' });
    assert.equal(r.ok, false);
    assert.match(r.error ?? '', /not started/);
  });

  it('stop() is idempotent before start()', async () => {
    await target.stop();
    assert.equal(target.isRunning(), false);
  });

  it('implements the ExecutionTarget interface surface', () => {
    assert.equal(typeof target.start, 'function');
    assert.equal(typeof target.stop, 'function');
    assert.equal(typeof target.isRunning, 'function');
    assert.equal(typeof target.execute, 'function');
    assert.ok(target.displayWidthPx > 0);
    assert.ok(target.displayHeightPx > 0);
  });
});
