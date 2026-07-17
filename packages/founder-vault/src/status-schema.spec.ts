/**
 * Unit tests for the FounderStackRuntimeStatus contract (Phase 3).
 *
 * Pins the canonical status object shape that Workstream C renders and
 * Workstream E feeds update state into. Asserts:
 *   - The status object has all 10 required fields.
 *   - updateState only accepts the 6 enum values.
 *   - executionConsentState only accepts the 4 enum values.
 *   - emptyFounderStackRuntimeStatus() returns a valid default.
 *
 * Pure TypeScript — run with:
 *   npx tsx --test packages/founder-vault/src/status-schema.spec.ts
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  FOUNDER_STACK_UPDATE_STATES,
  EXECUTION_CONSENT_STATES,
  STATUS_STALENESS_MS,
  emptyFounderStackRuntimeStatus,
  isFounderStackUpdateState,
  isExecutionConsentState,
  type FounderStackRuntimeStatus,
  type FounderStackUpdateState,
  type ExecutionConsentState,
} from './status-schema.js';

const REQUIRED_STATUS_FIELDS: ReadonlyArray<keyof FounderStackRuntimeStatus> = [
  'installedVersion',
  'latestVersion',
  'founderNodeOnline',
  'ideHandshakeActive',
  'gatewayReachable',
  'paired',
  'workspace',
  'lastHeartbeat',
  'updateState',
  'executionConsentState',
];

describe('FounderStackRuntimeStatus contract', () => {
  describe('required fields', () => {
    it('has exactly 10 required fields', () => {
      assert.equal(REQUIRED_STATUS_FIELDS.length, 10);
    });

    it('emptyFounderStackRuntimeStatus() returns an object with all 10 fields', () => {
      const s = emptyFounderStackRuntimeStatus();
      for (const key of REQUIRED_STATUS_FIELDS) {
        assert.ok(
          key in s,
          `expected status object to have field "${key}"`,
        );
      }
      // No extra fields.
      const actualKeys = Object.keys(s).sort();
      const expectedKeys = [...REQUIRED_STATUS_FIELDS].sort();
      assert.deepEqual(actualKeys, expectedKeys);
    });

    it('emptyFounderStackRuntimeStatus() returns safe defaults', () => {
      const s = emptyFounderStackRuntimeStatus();
      // "Everything offline" — no source has reported yet.
      assert.equal(s.founderNodeOnline, false);
      assert.equal(s.ideHandshakeActive, false);
      assert.equal(s.gatewayReachable, false);
      assert.equal(s.paired, false);
      assert.equal(s.workspace, null);
      assert.equal(s.installedVersion, '');
      assert.equal(s.latestVersion, '');
      assert.equal(s.updateState, 'idle');
      assert.equal(s.executionConsentState, 'pending');
      // lastHeartbeat is an ISO string (epoch zero as ISO).
      assert.equal(typeof s.lastHeartbeat, 'string');
      assert.ok(!Number.isNaN(Date.parse(s.lastHeartbeat)));
    });
  });

  describe('FounderStackUpdateState enum (updater state machine)', () => {
    it('exposes exactly the 6 valid values', () => {
      assert.equal(FOUNDER_STACK_UPDATE_STATES.length, 6);
      const expected: readonly FounderStackUpdateState[] = [
        'idle',
        'downloading',
        'verifying',
        'installing',
        'rolling_back',
        'failed',
      ];
      assert.deepEqual([...FOUNDER_STACK_UPDATE_STATES], [...expected]);
    });

    it('isFounderStackUpdateState accepts all 6 values', () => {
      for (const v of FOUNDER_STACK_UPDATE_STATES) {
        assert.equal(isFounderStackUpdateState(v), true, `${v} should be valid`);
      }
    });

    it('isFounderStackUpdateState rejects invalid values', () => {
      assert.equal(isFounderStackUpdateState('complete'), false);
      assert.equal(isFounderStackUpdateState('done'), false);
      assert.equal(isFounderStackUpdateState(''), false);
      assert.equal(isFounderStackUpdateState(null), false);
      assert.equal(isFounderStackUpdateState(undefined), false);
      assert.equal(isFounderStackUpdateState(42), false);
      // Uppercase variant must be rejected — the contract is lowercase.
      assert.equal(isFounderStackUpdateState('IDLE'), false);
    });
  });

  describe('ExecutionConsentState enum', () => {
    it('exposes exactly the 4 valid values', () => {
      assert.equal(EXECUTION_CONSENT_STATES.length, 4);
      const expected: readonly ExecutionConsentState[] = [
        'granted',
        'pending',
        'denied',
        'expired',
      ];
      assert.deepEqual([...EXECUTION_CONSENT_STATES], [...expected]);
    });

    it('isExecutionConsentState accepts all 4 values', () => {
      for (const v of EXECUTION_CONSENT_STATES) {
        assert.equal(isExecutionConsentState(v), true, `${v} should be valid`);
      }
    });

    it('isExecutionConsentState rejects invalid values', () => {
      assert.equal(isExecutionConsentState('allowed'), false);
      assert.equal(isExecutionConsentState('revoked'), false);
      assert.equal(isExecutionConsentState(''), false);
      assert.equal(isExecutionConsentState(null), false);
      assert.equal(isExecutionConsentState(undefined), false);
      assert.equal(isExecutionConsentState(42), false);
    });
  });

  describe('STATUS_STALENESS_MS thresholds', () => {
    it('exposes thresholds for every derived field', () => {
      // These match the JSDoc freshness rules on the status interface.
      assert.equal(STATUS_STALENESS_MS.gatewayReachable, 30_000);
      assert.equal(STATUS_STALENESS_MS.ideHandshakeActive, 15_000);
      assert.equal(STATUS_STALENESS_MS.workspace, 15_000);
      assert.equal(STATUS_STALENESS_MS.latestVersion, 60_000);
      assert.equal(STATUS_STALENESS_MS.founderNodeOnline, 5 * 60_000);
      assert.equal(STATUS_STALENESS_MS.executionConsent, 5 * 60_000);
    });
  });
});
