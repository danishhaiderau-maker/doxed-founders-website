import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveFlyStatus, resolveFeedStatus } from './agent-admin-showcase-control';

// These tests pin down the exact flap Danish reported on the admin profile:
// the admin "Fly strategy/trading owner" chip must NOT flip to "unreachable"
// when the parallel /bot-health probe loses the cross-region race, as long as
// the authoritative flyReachable flag (snapshot + lightweight /health probe)
// confirms Fly is up. Mirrors the public-view fix in commit 7a2ce1cf.

test('resolveFlyStatus: authoritative flyReachable=true => online even if direct probe failed', () => {
  // Heavy /api/state flapped (directProbe=false via /ready timeout), but the
  // lightweight dashboard flag proves Fly is up. Must stay online.
  assert.equal(resolveFlyStatus(true, false), 'online');
  assert.equal(resolveFlyStatus(true, null), 'online');
});

test('resolveFlyStatus: direct probe success => online even without dashboard flag', () => {
  // First render, dashboard poll hasn't returned yet, but /bot-health probe
  // succeeded. Direct connectivity evidence is enough.
  assert.equal(resolveFlyStatus(undefined, true), 'online');
  assert.equal(resolveFlyStatus(false, true), 'online');
});

test('resolveFlyStatus: lone direct-probe miss => stale, never unreachable (the flap fix)', () => {
  // This is the exact regression: /ready + /api/ping time out on the live Fly
  // host while /health stays 200. Without flyReachable corroborating, a lone
  // false MUST NOT paint "unreachable" — that was the false "offline" flap.
  assert.equal(resolveFlyStatus(undefined, false), 'stale');
  assert.equal(resolveFlyStatus(false, false), 'unreachable');
});

test('resolveFlyStatus: both signals genuinely absent => unreachable (true outage)', () => {
  assert.equal(resolveFlyStatus(false, null), 'unreachable');
  assert.equal(resolveFlyStatus(undefined, null), 'unreachable');
  assert.equal(resolveFlyStatus(false, undefined), 'unreachable');
});

test('resolveFeedStatus: botConnected snapshot => online', () => {
  assert.equal(resolveFeedStatus(true, null, false), 'online');
  assert.equal(resolveFeedStatus(false, true, false), 'online');
});

test('resolveFeedStatus: snapshot stale but Fly reachable => stale, not offline', () => {
  // Dashboard snapshot momentarily unavailable (serverUplinkOnline false,
  // botConnected false) but flyReachable confirms Fly itself is up. This is
  // the "feed stale" warn state — must not say "offline".
  assert.equal(resolveFeedStatus(false, false, true), 'stale');
  assert.equal(resolveFeedStatus(false, null, true), 'stale');
});

test('resolveFeedStatus: true outage => unreachable', () => {
  assert.equal(resolveFeedStatus(false, false, false), 'unreachable');
  assert.equal(resolveFeedStatus(false, null, false), 'unreachable');
  assert.equal(resolveFeedStatus(undefined, undefined, undefined), 'unreachable');
});
