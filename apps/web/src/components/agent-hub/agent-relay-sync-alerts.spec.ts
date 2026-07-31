import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRelaySyncAlerts } from './agent-relay-sync-alerts';

test('Fly health-reachable suppresses the false "Showcase bot offline" alert', () => {
  // Heavy /api/state fetch flapped to botConnected=false, but lightweight
  // /api/ping (surfaced as flyReachable=true) confirms Fly is still up.
  // This is the exact dashboard contradiction the new flag resolves: the
  // relay-sync panel must not cry "offline" while /bot-health shows fly:true.
  const alerts = buildRelaySyncAlerts({
    mode: 'live',
    botConnected: false,
    flyReachable: true,
    instanceLastError: null,
  });
  const titles = alerts.map((a) => a.title);
  assert.ok(!titles.includes('Showcase bot offline'));
  assert.ok(titles.includes('Fly feed stale'));
  const stale = alerts.find((a) => a.title === 'Fly feed stale');
  assert.equal(stale?.level, 'warn');
});

test('A real Fly outage still surfaces the hard "Showcase bot offline" alert', () => {
  // Both the heavy state fetch AND the lightweight probe failed — Fly is
  // genuinely unreachable. The error-level alert must fire unchanged so
  // users do not lose the strong signal during a true outage.
  const alerts = buildRelaySyncAlerts({
    mode: 'live',
    botConnected: false,
    flyReachable: false,
    instanceLastError: null,
  });
  const offline = alerts.find((a) => a.title === 'Showcase bot offline');
  assert.ok(offline, 'offline alert must fire when flyReachable is false');
  assert.equal(offline?.level, 'error');
});

test('F3 circuit-breaker lastError overrides flyReachable to surface a real outage', () => {
  // The display cache can lag up to 10min, so botConnected may still read
  // true while the executor has already entered safe mode. flyReachable
  // must NOT suppress the offline alert in that window — the executor is
  // the authority on real outages, and its lastError escalates past the
  // lightweight probe.
  const alerts = buildRelaySyncAlerts({
    mode: 'live',
    botConnected: true,
    flyReachable: true,
    instanceLastError: 'Showcase unreachable for >60s — entering safe mode',
  });
  const titles = alerts.map((a) => a.title);
  assert.ok(
    titles.includes('Showcase bot offline'),
    'F3 lastError must override flyReachable',
  );
  const stale = alerts.find((a) => a.title === 'Fly feed stale');
  assert.equal(stale, undefined);
});

test('Sim mode without an active sim does not surface showcase alerts', () => {
  // Sim panel only needs the showcase feed while a sim is actually running.
  // An idle signed-out visitor should not see "offline" nor "stale".
  const alerts = buildRelaySyncAlerts({
    mode: 'sim',
    botConnected: false,
    flyReachable: false,
    instanceLastError: null,
    copyRelaySim: { active: false, reconcile: null } as never,
    copyRelayReconcile: null,
  });
  const titles = alerts.map((a) => a.title);
  assert.ok(
    !titles.includes('Showcase bot offline'),
    `unexpected offline alert in idle sim: ${JSON.stringify(titles)}`,
  );
  assert.ok(
    !titles.includes('Fly feed stale'),
    `unexpected stale alert in idle sim: ${JSON.stringify(titles)}`,
  );
});
