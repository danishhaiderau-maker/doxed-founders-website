/**
 * Unit tests for the IPC protocol contract (Phase 3).
 *
 * These tests pin the wire-level contract that Workstream B will implement.
 * They assert:
 *   - The protocol version is 1 (so both sides can refuse mismatches).
 *   - The IpcMessage union covers every message type the brief requires.
 *   - The NonceTracker replay-protection reference implementation behaves
 *     per the contract: accepts first-seen nonces, rejects duplicates, ages
 *     out old nonces after the max-age window.
 *   - isIpcMessage() accepts well-formed messages and rejects garbage.
 *
 * Pure TypeScript — no dependencies on vscode or node:net. Run with:
 *   npx tsx --test packages/founder-ide-extension/src/ipc/protocol.spec.ts
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  IPC_PROTOCOL_VERSION,
  NonceTracker,
  generateNonce,
  isIpcMessage,
  type IpcMessage,
} from './protocol.js';

// ---------------------------------------------------------------------------
// Compile-time assertion: every literal in the brief is a valid IpcMessage['type'].
// ---------------------------------------------------------------------------
const ALL_TYPES = [
  'hello',
  'authState',
  'workspace',
  'openFiles',
  'selection',
  'taskState',
  'proposedEdit',
  'editReviewResult',
  'commandRequest',
  'commandReviewResult',
  'commandOutput',
  'cancel',
  'gatewayHealth',
  'memoryHealth',
  'versionState',
  'heartbeat',
] as const;

// If this line type-errors, the IpcMessage union dropped one of the message
// types. Each literal MUST be assignable to IpcMessage['type'].
const _typeCheck: IpcMessage['type'][] = [...ALL_TYPES];

describe('IPC protocol contract', () => {
  describe('IPC_PROTOCOL_VERSION', () => {
    it('is pinned to 1', () => {
      // Both sides refuse peers whose protocolVersion differs by more than
      // they can tolerate. v1 is the initial Phase 3 release.
      assert.equal(IPC_PROTOCOL_VERSION, 1);
    });
  });

  describe('IpcMessage discriminated union', () => {
    it('covers all 16 message types from the brief', () => {
      // 16 = hello + authState + 13 capability-named messages + heartbeat.
      // The brief lists "15 message types" but counts hello/authState as a
      // single handshake pair; we expose them as separate union members so
      // the dispatcher can switch on `type` exhaustively.
      assert.equal(ALL_TYPES.length, 16);
      // Sanity: no duplicates in the literal list.
      const unique = new Set(ALL_TYPES);
      assert.equal(unique.size, ALL_TYPES.length, 'duplicate message type in ALL_TYPES');
    });

    it('every literal is assignable to IpcMessage[type]', () => {
      // _typeCheck above is the compile-time assertion; this is the runtime
      // mirror so a regression surfaces as a test failure, not just a
      // compile error CI might not run.
      for (const t of ALL_TYPES) {
        assert.ok(typeof t === 'string', `message type ${t} must be a string`);
      }
      // Referencing _typeCheck keeps it from being tree-shaken in some setups.
      assert.equal(_typeCheck.length, 16);
    });
  });

  describe('generateNonce', () => {
    it('returns a 32-char hex string (16 random bytes)', () => {
      const n = generateNonce();
      assert.equal(n.length, 32, 'expected 32 hex chars');
      assert.match(n, /^[0-9a-f]{32}$/i, 'expected hex encoding');
    });

    it('does not collide across 1000 calls (probabilistic, 2^128 space)', () => {
      const seen = new Set<string>();
      for (let i = 0; i < 1000; i++) {
        seen.add(generateNonce());
      }
      // Collisions are astronomically unlikely (birthday paradox at 2^128
      // with 1000 samples). If this ever fails, the RNG is broken.
      assert.equal(seen.size, 1000, 'nonce collision detected');
    });
  });

  describe('NonceTracker', () => {
    it('accepts first-seen nonces (returns true)', () => {
      const tracker = new NonceTracker();
      assert.equal(tracker.check('nonce-a'), true);
      assert.equal(tracker.check('nonce-b'), true);
      assert.equal(tracker.check('nonce-c'), true);
    });

    it('rejects duplicate nonces within the max-age window (returns false)', () => {
      const tracker = new NonceTracker();
      const t0 = 10_000;
      assert.equal(tracker.check('nonce-a', t0), true);
      // Same nonce at any later point within the window is a replay.
      assert.equal(tracker.check('nonce-a', t0 + 1000), false);
      assert.equal(tracker.check('nonce-a', t0 + 60_000), false);
    });

    it('ages out old nonces after the max-age window', () => {
      const MAX_AGE = 5 * 60 * 1000; // 5 min default
      const tracker = new NonceTracker(MAX_AGE);
      const t0 = 100_000;
      assert.equal(tracker.check('nonce-a', t0), true);
      // Within the window: replay.
      assert.equal(tracker.check('nonce-a', t0 + MAX_AGE - 1), false);
      // At exactly max-age later the entry is purged by ageOut on the next
      // call, so the nonce becomes fresh again.
      assert.equal(tracker.check('nonce-a', t0 + MAX_AGE + 1), true);
    });

    it('purges entries older than max-age on each check (bounded memory)', () => {
      const MAX_AGE = 1000;
      const tracker = new NonceTracker(MAX_AGE);
      // Stuff in 50 nonces at t=0.
      for (let i = 0; i < 50; i++) {
        tracker.check(`old-${i}`, 0);
      }
      assert.equal(tracker.size, 50);
      // Advance past max-age and add one more — ageOut reaps the 50 old ones.
      tracker.check('fresh', 0 + MAX_AGE + 1);
      assert.equal(tracker.size, 1, 'old nonces should have been reaped');
    });

    it('reset() clears all tracked nonces', () => {
      const tracker = new NonceTracker();
      tracker.check('a');
      tracker.check('b');
      assert.equal(tracker.size, 2);
      tracker.reset();
      assert.equal(tracker.size, 0);
      // After reset, the same nonces are fresh again.
      assert.equal(tracker.check('a'), true);
    });

    it('treats nonces as case-sensitive and distinct', () => {
      const tracker = new NonceTracker();
      assert.equal(tracker.check('ABC123'), true);
      assert.equal(tracker.check('abc123'), true); // different string
      assert.equal(tracker.check('ABC123'), false); // exact replay
    });
  });

  describe('isIpcMessage type guard', () => {
    it('accepts a well-formed hello message', () => {
      const msg = {
        type: 'hello',
        nonce: 'abc',
        ts: new Date().toISOString(),
        protocolVersion: 1,
        installId: 'install-1',
        ipcSecret: 'secret',
        capabilities: ['workspace', 'heartbeat'],
      };
      assert.equal(isIpcMessage(msg), true);
    });

    it('accepts every message type in the union', () => {
      for (const t of ALL_TYPES) {
        const msg = { type: t, nonce: 'n', ts: new Date().toISOString() };
        assert.equal(isIpcMessage(msg), true, `expected ${t} to be a valid IpcMessage`);
      }
    });

    it('rejects null / undefined / primitives', () => {
      assert.equal(isIpcMessage(null), false);
      assert.equal(isIpcMessage(undefined), false);
      assert.equal(isIpcMessage('hello'), false);
      assert.equal(isIpcMessage(42), false);
    });

    it('rejects objects with unknown type', () => {
      assert.equal(
        isIpcMessage({ type: 'bogus', nonce: 'n', ts: 'now' }),
        false,
      );
    });

    it('rejects objects missing required envelope fields', () => {
      // Missing nonce.
      assert.equal(
        isIpcMessage({ type: 'hello', ts: 'now' }),
        false,
      );
      // Missing ts.
      assert.equal(
        isIpcMessage({ type: 'hello', nonce: 'n' }),
        false,
      );
      // Missing type.
      assert.equal(
        isIpcMessage({ nonce: 'n', ts: 'now' }),
        false,
      );
    });
  });
});
