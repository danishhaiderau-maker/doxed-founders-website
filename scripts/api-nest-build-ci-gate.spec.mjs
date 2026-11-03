/**
 * R51 mutation proof: the api-nest-build workflow must keep running
 * `npm run build --workspace=@dcf/api` so PR CI cannot go green while
 * Railway nest build would fail.
 *
 * Mutation: remove that build line from .github/workflows/api-nest-build.yml
 * → this test fails.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const workflowPath = join(root, '.github/workflows/api-nest-build.yml');

test('api-nest-build workflow requires nest/@dcf/api build', () => {
  const yml = readFileSync(workflowPath, 'utf8');
  assert.match(
    yml,
    /npm run build --workspace=@dcf\/api/,
    'api-nest-build.yml must run nest build (Railway prod parity)',
  );
  assert.match(yml, /^name:\s*api-nest-build\s*$/m, 'workflow name must be api-nest-build');
  assert.match(yml, /on:\s*\n\s*pull_request:/m, 'must run on pull_request');
});
