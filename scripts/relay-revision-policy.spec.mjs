import assert from 'node:assert/strict';
import test from 'node:test';
import {
  classifyRelaySourceRevision,
  onlyAllowedFounderMetadataChanged,
} from './relay-revision-policy.mjs';

const expected = '1'.repeat(40);
const observed = '2'.repeat(40);

test('accepts the exact executor revision without descendant inspection', async () => {
  let inspected = false;
  const result = await classifyRelaySourceRevision({
    expected,
    observed: expected,
    inspectDescendant: async () => {
      inspected = true;
      return { isDescendant: false, changedFiles: [] };
    },
  });
  assert.equal(result.accepted, true);
  assert.equal(result.mode, 'exact');
  assert.equal(inspected, false);
});

test('accepts only a proven descendant limited to Founder OS memory metadata', async () => {
  const result = await classifyRelaySourceRevision({
    expected,
    observed,
    inspectDescendant: async () => ({
      isDescendant: true,
      changedFiles: [
        '.github/founder-os/project-context.md',
        '.github/founder-os/tasks.json',
      ],
    }),
  });
  assert.equal(result.accepted, true);
  assert.equal(result.mode, 'founder-metadata-descendant');
});

test('rejects code drift, non-descendants, invalid revisions, and empty diffs', async () => {
  assert.equal(
    onlyAllowedFounderMetadataChanged([
      '.github/founder-os/project-context.md',
      'apps/api/src/main.ts',
    ]),
    false,
  );
  assert.equal(onlyAllowedFounderMetadataChanged([]), false);

  const codeDrift = await classifyRelaySourceRevision({
    expected,
    observed,
    inspectDescendant: async () => ({
      isDescendant: true,
      changedFiles: ['apps/api/src/main.ts'],
    }),
  });
  assert.equal(codeDrift.accepted, false);

  const unrelated = await classifyRelaySourceRevision({
    expected,
    observed,
    inspectDescendant: async () => ({
      isDescendant: false,
      changedFiles: ['.github/founder-os/tasks.json'],
    }),
  });
  assert.equal(unrelated.accepted, false);

  const invalid = await classifyRelaySourceRevision({
    expected,
    observed: 'not-a-sha',
    inspectDescendant: async () => {
      throw new Error('must not inspect an invalid revision');
    },
  });
  assert.equal(invalid.accepted, false);
});
