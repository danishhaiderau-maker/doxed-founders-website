import assert from 'node:assert/strict';
import test from 'node:test';
import {
  founderNodeIntMetric,
  founderRemoteCapability,
  founderRemoteCapabilityReset,
} from './founder-node-capability';

test('heartbeat capability is exact, bounded, and stamped by the server observation time', () => {
  const observedAt = new Date('2026-09-21T01:02:03.000Z');
  assert.deepEqual(
    founderRemoteCapability(
      {
        provider: 'founder-ide-next',
        capabilityVersion: 1,
        capabilities: ['remote-build-v1', 'unapproved-terminal'],
      },
      observedAt,
    ),
    {
      ideProvider: 'founder-ide-next',
      ideCapabilityVersion: 1,
      ideCapabilities: ['remote-build-v1'],
      ideCapabilitiesAt: observedAt,
    },
  );
});

test('absent or invalid capability registration clears all capability fields and timestamp', () => {
  const observedAt = new Date('2026-09-21T01:02:03.000Z');
  for (const input of [
    undefined,
    null,
    [],
    {},
    { provider: 'cursor', capabilityVersion: 1, capabilities: ['remote-build-v1'] },
    { provider: 'founder-ide-next', capabilityVersion: '1', capabilities: ['remote-build-v1'] },
    { provider: 'founder-ide-next', capabilityVersion: 1, capabilities: [] },
  ]) {
    assert.deepEqual(founderRemoteCapability(input, observedAt), founderRemoteCapabilityReset());
  }
  assert.deepEqual(
    founderRemoteCapability(
      { provider: 'founder-ide-next', capabilityVersion: 1, capabilities: ['remote-build-v1'] },
      new Date(Number.NaN),
    ),
    founderRemoteCapabilityReset(),
  );
});

test('heartbeat Int metrics are finite nonnegative rounded integers or null', () => {
  assert.equal(founderNodeIntMetric(63.8), 64);
  assert.equal(founderNodeIntMetric(999.4), 999);
  assert.equal(founderNodeIntMetric(0), 0);
  assert.equal(founderNodeIntMetric(9_999_999_999), 2_147_483_647);
  for (const value of [undefined, null, '63.8', Number.NaN, Number.POSITIVE_INFINITY, -0.1]) {
    assert.equal(founderNodeIntMetric(value), null);
  }
});
