import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { assertExactSha, pausedAndDisarmed } from './deploy-paused-credential-api.mjs';

test('paused control-plane boundary requires explicit PAUSED execution mode and no arm timestamps', () => {
  const row = {
    status: 'PAUSED',
    dashboardState: {
      relayExecutionMode: 'PAUSED',
      relayArmedAt: null,
      realTradingConfirmedAt: null,
    },
  };
  assert.equal(pausedAndDisarmed(row), true);
  assert.equal(pausedAndDisarmed({ ...row, status: 'ACTIVE' }), false);
  assert.equal(pausedAndDisarmed({ ...row, dashboardState: { ...row.dashboardState, relayExecutionMode: null } }), false);
  assert.equal(pausedAndDisarmed({ ...row, dashboardState: { ...row.dashboardState, relayArmedAt: '2026-09-03T00:00:00Z' } }), false);
  assert.equal(pausedAndDisarmed({ ...row, dashboardState: { ...row.dashboardState, realTradingConfirmedAt: '2026-09-03T00:00:00Z' } }), false);
});

test('exact deploy identity accepts only a full Git SHA', () => {
  const sha = '3cdefa4123456789012345678901234567890123';
  assert.equal(assertExactSha(sha.toUpperCase()), sha);
  assert.throws(() => assertExactSha('3cdefa4'), /exact 40-character/);
  assert.throws(() => assertExactSha('z'.repeat(40)), /exact 40-character/);
});

test('workflow isolates API deployment and proves executor deployment identity is unchanged', () => {
  const workflow = fs.readFileSync(new URL('../.github/workflows/auto-deploy.yml', import.meta.url), 'utf8');
  const start = workflow.indexOf('api-control-plane-credential-refresh:');
  assert.notEqual(start, -1);
  const end = workflow.indexOf('\n  deploy:', start);
  assert.notEqual(end, -1);
  const section = workflow.slice(start, end);
  assert.match(section, /deploy-paused-credential-api\.mjs prove-paused/);
  assert.match(section, /deploy-paused-credential-api\.mjs snapshot/);
  assert.match(section, /deploy-paused-credential-api\.mjs deploy/);
  assert.match(section, /deploy-paused-credential-api\.mjs verify/);
  assert.match(section, /EXPECTED_EXECUTOR_DEPLOYMENT_ID/);
  assert.match(section, /merge-base --is-ancestor 3cdefa4f0dfead539c78de08a75c8ee817c6170b/);
  assert.doesNotMatch(section, /ci-railway-redeploy|verify-relay-executor-revision|check-relay-flat/);
});

test('deploy script targets only API and fails if executor deployment changes', () => {
  const source = fs.readFileSync(new URL('./deploy-paused-credential-api.mjs', import.meta.url), 'utf8');
  const mutation = source.slice(source.indexOf('async function deploy'), source.indexOf('async function verify'));
  assert.match(mutation, /serviceInstanceDeployV2/);
  assert.match(mutation, /topology\.api\.id/);
  assert.doesNotMatch(mutation, /topology\.executor\.id/);
  assert.match(source, /Isolated relay-executor deployment changed during API-only rollout/);
});
