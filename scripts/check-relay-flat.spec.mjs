import assert from 'node:assert/strict';
import test from 'node:test';
import {
  describeOwnerFetchError,
  evaluateRelayFlatGate,
  hasFullOwnerOrderState,
  isStrictExchangeOrderAuditFlat,
  isStrictRawFlatReconcileSnapshot,
  isRelayPausedAndDisarmed,
  ownerFetchErrorChain,
  paperTipExceptionEnabled,
  pathwayLabTipExceptionEnabled,
  pathwayLabFailingRev,
  isPathwayLabBootLoop503,
  pathwayLabStartupFailProven,
  flyLogsReadUnauthorized,
  pathwayLabEncodedStartupFail,
  healthRevisionMatches,
  paperDisarmedHealth,
  paperModeDisarmLogProven,
  collectFlyLogText,
  evaluatePathwayLabTipException,
} from './check-relay-flat.mjs';

test('paused relay accepts legacy null mode only when arming timestamps are clear', () => {
  assert.equal(isRelayPausedAndDisarmed({
    status: 'PAUSED', relayExecutionMode: null, relayArmedAt: null, realTradingConfirmedAt: null,
  }), true);
  assert.equal(isRelayPausedAndDisarmed({
    status: 'PAUSED', relayExecutionMode: 'LIVE', relayArmedAt: null, realTradingConfirmedAt: null,
  }), false);
  assert.equal(isRelayPausedAndDisarmed({
    status: 'PAUSED', relayExecutionMode: null, relayArmedAt: '2026-08-09T00:00:00Z', realTradingConfirmedAt: null,
  }), false);
});

const now = Date.parse('2026-07-24T05:45:00.000Z');

function rawFlat(overrides = {}) {
  return {
    rawExchangePositionQty: 0,
    dustPositionQty: 0,
    signedExchangePositionQty: 0,
    ledgerOpenQty: 0,
    signedLedgerOpenQty: 0,
    deltaBtc: 0,
    openLots: 0,
    pendingLots: 0,
    updatedAt: '2026-07-24T05:44:50.000Z',
    ...overrides,
  };
}

test('strict flat proof accepts a fresh complete raw-zero snapshot', () => {
  assert.equal(isStrictRawFlatReconcileSnapshot(rawFlat(), now), true);
});

test('strict flat proof rejects legacy effective-zero snapshots', () => {
  const legacy = rawFlat();
  delete legacy.rawExchangePositionQty;
  delete legacy.dustPositionQty;
  delete legacy.signedExchangePositionQty;
  delete legacy.signedLedgerOpenQty;
  legacy.exchangePositionQty = 0;
  assert.equal(isStrictRawFlatReconcileSnapshot(legacy, now), false);
});

test('strict flat proof rejects one satoshi, dust, and stale observations', () => {
  assert.equal(
    isStrictRawFlatReconcileSnapshot(
      rawFlat({ rawExchangePositionQty: 0.00000001, signedExchangePositionQty: -0.00000001 }),
      now,
    ),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(rawFlat({ dustPositionQty: 0.00003999 }), now),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(
      rawFlat({ updatedAt: '2026-07-24T05:43:59.999Z' }),
      now,
    ),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(rawFlat({ rawExchangePositionQty: null }), now),
    false,
  );
});

test('authenticated source proof rejects a sanitized state without an order book', () => {
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, positions: [] }),
    false,
  );
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, pending_orders: [] }),
    true,
  );
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, orders: [] }),
    true,
  );
});

test('owner timeout explains Fly routing and health-check failure', () => {
  const error = new Error('The operation was aborted due to timeout');
  error.name = 'TimeoutError';
  const described = describeOwnerFetchError(
    error,
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
    15_000,
  );
  assert.match(described.message, /timed out after 15000ms/);
  assert.match(described.message, /doxed-btc-bot\.fly\.dev\/api\/relay-execution-state/);
  assert.match(described.message, /critical service check removed public routing/);
});

test('owner non-timeout failure retains URL and original diagnosis', () => {
  const error = new Error('showcase HTTP 503');
  const described = describeOwnerFetchError(
    error,
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
    15_000,
  );
  assert.match(described.message, /request failed/);
  assert.match(described.message, /showcase HTTP 503/);
  assert.match(described.message, /check Fly \/health/);
});

test('owner fetch diagnosis includes nested undici socket cause and attempts', () => {
  const socket = new Error('other side closed');
  socket.name = 'SocketError';
  socket.code = 'UND_ERR_SOCKET';
  const outer = new TypeError('fetch failed', { cause: socket });
  assert.match(ownerFetchErrorChain(outer), /UND_ERR_SOCKET/);
  const described = describeOwnerFetchError(
    outer,
    'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
    15_000,
    3,
  );
  assert.match(described.message, /after 3 attempts/);
  assert.match(described.message, /UND_ERR_SOCKET/);
  assert.match(described.message, /route\/socket reset/);
});

function pausedCheetah(overrides = {}) {
  return {
    user: 'Cheetah · undefined',
    status: 'PAUSED',
    relayExecutionMode: null,
    relayArmedAt: null,
    realTradingConfirmedAt: null,
    activeParticipants: 0,
    reconcile: null,
    exchangeOrderAudit: null,
    orphanOrderIds: [],
    orphanPositionIds: [],
    ...overrides,
  };
}

function flatAudit(overrides = {}) {
  return {
    known: true,
    activeOrderCount: 0,
    managedActiveOrderCount: 0,
    foreignActiveOrderCount: 0,
    checkedAt: '2026-07-24T05:44:50.000Z',
    ...overrides,
  };
}

function gate(overrides = {}) {
  return evaluateRelayFlatGate({
    showcasePositions: 0,
    showcasePendingOrders: 0,
    rows: [pausedCheetah()],
    paperTipException: false,
    nowMs: now,
    ...overrides,
  });
}

test('paper tip exception is off unless the env value is exactly true', () => {
  assert.equal(paperTipExceptionEnabled({}), false);
  assert.equal(paperTipExceptionEnabled({ PAPER_TIP_EXCEPTION: 'false' }), false);
  assert.equal(paperTipExceptionEnabled({ PAPER_TIP_EXCEPTION: '1' }), false);
  assert.equal(paperTipExceptionEnabled({ PAPER_TIP_EXCEPTION: 'TRUE' }), false);
  assert.equal(paperTipExceptionEnabled({ PAPER_TIP_EXCEPTION: 'true' }), true);
});

test('paper tip exception stays fail-closed when the flag is off', () => {
  assert.equal(gate(), 2);
  assert.equal(gate({ paperTipException: false }), 2);
});

test('paper tip exception passes a flat paused book with null Cheetah proof', () => {
  assert.equal(gate({
    paperTipException: true,
    rows: [
      pausedCheetah({ user: 'Viper · Canada' }),
      pausedCheetah(),
    ],
  }), 0);
});

test('paper tip exception fails closed when showcase is not flat', () => {
  assert.equal(gate({ paperTipException: true, showcasePositions: 1 }), 2);
  assert.equal(gate({ paperTipException: true, showcasePendingOrders: 1 }), 2);
});

test('paper tip exception fails closed when relays are not paused or disarmed', () => {
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({ status: 'ACTIVE' })],
  }), 2);
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({ relayArmedAt: '2026-08-09T00:00:00Z' })],
  }), 2);
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({ relayExecutionMode: 'LIVE' })],
  }), 2);
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({ realTradingConfirmedAt: '2026-08-09T00:00:00Z' })],
  }), 2);
  assert.equal(gate({ paperTipException: true, rows: [] }), 2);
});

test('paper tip exception does not accept a stale or partial Cheetah proof', () => {
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({
      reconcile: rawFlat({ updatedAt: '2026-07-24T05:43:59.999Z' }),
    })],
  }), 2);
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({
      exchangeOrderAudit: flatAudit({ activeOrderCount: 1 }),
    })],
  }), 2);
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({ orphanOrderIds: ['ord-1'] })],
  }), 2);
  assert.equal(gate({
    paperTipException: true,
    rows: [pausedCheetah({ activeParticipants: 1 })],
  }), 2);
});

test('strict Cheetah freshness still passes when the paper tip exception is off', () => {
  assert.equal(gate({
    rows: [pausedCheetah({
      reconcile: rawFlat(),
      exchangeOrderAudit: flatAudit(),
    })],
  }), 0);
});

test('strict exchange order proof requires a fresh known zero-order snapshot', () => {
  const flatAudit = {
    known: true,
    activeOrderCount: 0,
    managedActiveOrderCount: 0,
    foreignActiveOrderCount: 0,
    checkedAt: '2026-07-24T05:44:50.000Z',
  };
  assert.equal(isStrictExchangeOrderAuditFlat(flatAudit, now), true);
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, activeOrderCount: 1 },
      now,
    ),
    false,
  );
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, known: false },
      now,
    ),
    false,
  );
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, checkedAt: '2026-07-24T05:43:59.999Z' },
      now,
    ),
    false,
  );
});

const startupFailLog = [
  '[PAPER MODE] FORCE_PAPER_MODE active: live arming and Bitfinex execution disabled',
  'Pathway Lab startup validation FAILED — type_b=PASS tiles=PASS',
  'ai_scan=PASS ai_scan_role=PASS v1_post_ai=FAIL sync=PASS',
].join(' ');

function bootLoopError(body = { ok: false, boot: 'starting', error: 'dashboard loading' }) {
  const error = new Error(
    'canonical owner state request failed after 3 attempts at '
    + 'https://doxed-btc-bot.fly.dev/api/relay-execution-state; '
    + 'root cause: Error: HTTP 503; check Fly /health, machine status, and public routing',
  );
  error.cause = new Error('HTTP 503');
  error.cause.status = 503;
  error.cause.body = JSON.stringify(body);
  return error;
}

function relayEdgeError({
  status = 502,
  body = '',
  url = 'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
} = {}) {
  const error = new Error(
    'canonical owner state request failed after 3 attempts at '
    + `${url}; `
    + `root cause: Error: HTTP ${status}; check Fly /health, machine status, and public routing`,
  );
  error.cause = new Error(`HTTP ${status}`);
  error.cause.status = status;
  error.cause.body = body;
  return error;
}

/** Boss live capture ~22:09Z: Fly edge 502, empty body. */
function liveFlyEdge502EmptyError() {
  return relayEdgeError({ status: 502, body: '' });
}

/** /health at the same capture: early-boot, rev 5790d091, no arm flags. */
function liveEarlyBootHealth(overrides = {}) {
  return {
    ok: true,
    boot: 'starting',
    bot_pid: 4076,
    dashboard_pid: 4076,
    dashboard_port: 7002,
    dashboard_owner: true,
    source_git_rev: '5790d0919fc2',
    bot_version: 'v31-five-family-score-led-paper-v1',
    server_ts: '2026-09-27T22:09:36.144597+00:00',
    status: 'starting',
    ...overrides,
  };
}

function earlyBootHealth(overrides = {}) {
  return {
    ok: true,
    boot: 'starting',
    dashboard_owner: true,
    source_git_rev: '5790d0919fc2',
    bot_version: 'v31-five-family-score-led-paper-v1',
    ...overrides,
  };
}

function pathwayDecision(overrides = {}) {
  return evaluatePathwayLabTipException({
    enabled: true,
    failingRev: '5790d091',
    relayError: bootLoopError(),
    health: earlyBootHealth(),
    logText: startupFailLog,
    rows: [pausedCheetah()],
    recoverStalled: false,
    ...overrides,
  });
}

test('pathway lab tip exception is off unless the env value is exactly true', () => {
  assert.equal(pathwayLabTipExceptionEnabled({}), false);
  assert.equal(pathwayLabTipExceptionEnabled({ PATHWAY_LAB_TIP_EXCEPTION: 'false' }), false);
  assert.equal(pathwayLabTipExceptionEnabled({ PATHWAY_LAB_TIP_EXCEPTION: '1' }), false);
  assert.equal(pathwayLabTipExceptionEnabled({ PATHWAY_LAB_TIP_EXCEPTION: 'TRUE' }), false);
  assert.equal(pathwayLabTipExceptionEnabled({ PATHWAY_LAB_TIP_EXCEPTION: 'true' }), true);
  assert.equal(pathwayLabFailingRev({}), '');
  assert.equal(pathwayLabFailingRev({ PATHWAY_LAB_FAILING_REV: '5790d091' }), '5790d091');
  assert.equal(pathwayLabFailingRev({ PATHWAY_LAB_FAILING_REV: 'not-a-rev' }), '');
});

test('pathway lab tip exception passes only the proven boot-loop 503', () => {
  const decision = pathwayDecision();
  assert.equal(decision.pass, true);
  assert.equal(decision.exitCode, 0);
  assert.equal(isPathwayLabBootLoop503(bootLoopError()), true);
  assert.equal(pathwayLabStartupFailProven(startupFailLog), true);
  assert.equal(healthRevisionMatches(earlyBootHealth(), '5790d091'), true);
  assert.deepEqual(paperDisarmedHealth(earlyBootHealth()), { ok: true, mode: 'early_boot' });
});

test('pathway lab tip exception stays fail-closed without the flag or the FAIL proof', () => {
  assert.equal(pathwayDecision({ enabled: false }).pass, false);
  assert.equal(pathwayDecision({ enabled: false }).exitCode, 1);
  assert.equal(pathwayDecision({
    logText: 'Pathway Lab startup validation FAILED — v1_post_ai=PASS',
  }).reason, 'startup FAIL proof missing');
  assert.equal(pathwayDecision({ logText: 'v1_post_ai=FAIL' }).pass, false);
  assert.equal(pathwayDecision({
    logText: 'Pathway Lab startup validation FAILED — v1_post_ai=FAIL',
  }).reason, 'paper disarm log missing');
  assert.equal(paperModeDisarmLogProven(startupFailLog), true);
  assert.equal(pathwayDecision({ recoverStalled: true }).reason, 'recover stalled path');
  assert.equal(pathwayDecision({ failingRev: '' }).pass, false);
  assert.equal(pathwayDecision({
    health: earlyBootHealth({ source_git_rev: '538a39e6c366' }),
  }).reason, 'failing revision proof missing');
});

test('pathway lab tip exception rejects 503s that are not the boot loop', () => {
  assert.equal(pathwayDecision({
    relayError: bootLoopError({ ok: false, error: 'dashboard_busy' }),
  }).reason, 'not the pathway boot-loop 503');
  assert.equal(pathwayDecision({
    relayError: bootLoopError({
      api_state_error: 'canonical execution snapshot unavailable or stale',
    }),
  }).pass, false);
  const timeout = new Error(
    'canonical owner state timed out after 15000ms per attempt after 3 attempts at '
    + 'https://doxed-btc-bot.fly.dev/api/relay-execution-state',
  );
  assert.equal(isPathwayLabBootLoop503(timeout), false);
  assert.equal(pathwayDecision({ relayError: timeout }).pass, false);
});

test('pathway lab tip exception fails closed when the owner is armed or not paper', () => {
  assert.equal(pathwayDecision({
    health: {
      live_armed: true,
      force_paper_mode: true,
      bitfinex_live_enabled: false,
      source_git_rev: '5790d0919fc2',
    },
  }).reason, 'paper-disarmed proof missing (live_armed)');
  assert.equal(pathwayDecision({
    health: {
      live_armed: false,
      force_paper_mode: false,
      bitfinex_live_enabled: false,
      source_git_rev: '5790d0919fc2',
    },
  }).reason, 'paper-disarmed proof missing (not_paper)');
  assert.equal(pathwayDecision({
    health: {
      live_armed: false,
      force_paper_mode: true,
      bitfinex_live_enabled: true,
      git_rev: '5790d0919fc2',
    },
  }).reason, 'paper-disarmed proof missing (bitfinex_live)');
  assert.deepEqual(paperDisarmedHealth({
    live_armed: false,
    force_paper_mode: true,
    bitfinex_live_enabled: false,
  }), { ok: true, mode: 'explicit' });
  assert.equal(pathwayDecision({
    health: {
      live_armed: false,
      force_paper_mode: true,
      bitfinex_live_enabled: false,
      source_git_rev: '5790d0919fc2',
    },
    logText: 'Pathway Lab startup validation FAILED — v1_post_ai=FAIL',
  }).pass, true);
});

test('pathway lab tip exception still requires paused disarmed relays', () => {
  assert.equal(pathwayDecision({
    rows: [pausedCheetah({ status: 'ACTIVE' })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    rows: [pausedCheetah({ relayArmedAt: '2026-08-09T00:00:00Z' })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    rows: [pausedCheetah({ relayExecutionMode: 'LIVE' })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    rows: [pausedCheetah({ activeParticipants: 1 })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    rows: [pausedCheetah({ orphanOrderIds: ['ord-1'] })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({ rows: [] }).exitCode, 2);
});

test('fly log text keeps the pathway startup FAIL line', () => {
  const text = collectFlyLogText({
    data: [{ attributes: { message: startupFailLog } }],
  });
  assert.equal(pathwayLabStartupFailProven(text), true);
  assert.equal(collectFlyLogText({ data: [] }), '');
});

test('fly logs 401 is the only logs failure that can use the alternate proof', () => {
  assert.equal(flyLogsReadUnauthorized({ status: 401 }), true);
  assert.equal(flyLogsReadUnauthorized(Object.assign(new Error('fly logs HTTP 401'), { status: 401 })), true);
  assert.equal(flyLogsReadUnauthorized({ status: 403, body: 'token unauthorized' }), true);
  assert.equal(flyLogsReadUnauthorized({ status: 403, body: 'Forbidden' }), false);
  assert.equal(flyLogsReadUnauthorized({ status: 500, message: 'fly logs HTTP 500' }), false);
  assert.equal(flyLogsReadUnauthorized(new Error('fly logs HTTP 404')), false);
  assert.equal(
    flyLogsReadUnauthorized(new Error('PATHWAY_LAB_TIP_EXCEPTION requires FLY_API_TOKEN to prove the startup FAIL')),
    false,
  );
  assert.equal(pathwayLabEncodedStartupFail(earlyBootHealth()).present, false);
  assert.equal(pathwayLabEncodedStartupFail({ v1_post_ai: 'FAIL' }).fail, true);
  assert.equal(pathwayLabEncodedStartupFail({ independent_v1_post_ai_spawn: 'PASS' }).fail, false);
  assert.equal(
    pathwayLabEncodedStartupFail([startupFailLog]).fail,
    true,
  );
});

test('pathway lab tip exception accepts a complete 401 alternate proof without fly logs', () => {
  const decision = pathwayDecision({
    logText: '',
    logsUnauthorized: true,
  });
  assert.equal(decision.pass, true);
  assert.equal(decision.exitCode, 0);
  assert.equal(decision.reason, 'pathway lab 401 alternate proof');
  const encoded = pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    health: earlyBootHealth({ v1_post_ai: 'FAIL' }),
  });
  assert.equal(encoded.pass, true);
  assert.equal(encoded.reason, 'pathway lab 401 alternate proof with encoded v1_post_ai=FAIL');
  const artifact = pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    failArtifacts: [startupFailLog],
  });
  assert.equal(artifact.pass, true);
  assert.equal(artifact.reason, 'pathway lab 401 alternate proof with encoded v1_post_ai=FAIL');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    health: {
      live_armed: false,
      force_paper_mode: true,
      bitfinex_live_enabled: false,
      source_git_rev: '5790d0919fc2',
    },
  }).pass, true);
});

test('pathway lab tip exception stays fail-closed when the 401 alternate proof is incomplete', () => {
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: false,
  }).reason, 'startup FAIL proof missing');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    enabled: false,
  }).reason, 'flag off');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    recoverStalled: true,
  }).reason, 'recover stalled path');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    relayError: bootLoopError({ ok: false, error: 'dashboard_busy' }),
  }).reason, 'not the pathway boot-loop 503');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    failingRev: '',
  }).reason, 'failing revision proof missing');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    health: earlyBootHealth({ source_git_rev: '538a39e6c366' }),
  }).reason, 'failing revision proof missing');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    health: earlyBootHealth({ live_armed: true }),
  }).reason, 'paper-disarmed proof missing (live_armed)');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    health: earlyBootHealth({ v1_post_ai: 'PASS' }),
  }).reason, 'startup FAIL proof contradicted');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    failArtifacts: ['Pathway Lab startup validation FAILED — v1_post_ai=PASS'],
  }).reason, 'startup FAIL proof contradicted');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    rows: [pausedCheetah({ status: 'ACTIVE' })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    rows: [pausedCheetah({ relayArmedAt: '2026-08-09T00:00:00Z' })],
  }).reason, 'relays are not paused and disarmed');
  assert.equal(pathwayDecision({
    logText: '',
    logsUnauthorized: true,
    rows: [],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    logText: 'Pathway Lab startup validation FAILED — v1_post_ai=FAIL',
    logsUnauthorized: false,
  }).reason, 'paper disarm log missing');
});

test('pathway lab 401 alternate accepts the live Fly-edge 502 empty body with early-boot health', () => {
  const relayError = liveFlyEdge502EmptyError();
  assert.equal(isPathwayLabBootLoop503(relayError), true);
  assert.equal(relayError.cause.status, 502);
  assert.equal(relayError.cause.body, '');
  const decision = pathwayDecision({
    relayError,
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
  });
  assert.equal(decision.pass, true);
  assert.equal(decision.exitCode, 0);
  assert.equal(decision.reason, 'pathway lab 401 alternate proof');
  assert.equal(paperDisarmedHealth(liveEarlyBootHealth()).mode, 'early_boot');
  assert.equal(healthRevisionMatches(liveEarlyBootHealth(), '5790d091'), true);
  const encoded = pathwayDecision({
    relayError,
    health: liveEarlyBootHealth({ v1_post_ai: 'FAIL' }),
    logText: '',
    logsUnauthorized: true,
  });
  assert.equal(encoded.pass, true);
  assert.equal(encoded.reason, 'pathway lab 401 alternate proof with encoded v1_post_ai=FAIL');
  assert.equal(pathwayDecision({
    relayError,
    health: liveEarlyBootHealth(),
    logText: startupFailLog,
    logsUnauthorized: false,
  }).reason, 'pathway lab boot-loop tip');
});

test('pathway lab boot loop still accepts app 503 JSON and an empty edge 503', () => {
  const restoring = bootLoopError({
    boot: 'starting',
    error: 'dashboard state is restoring',
    ok: false,
  });
  assert.equal(isPathwayLabBootLoop503(restoring), true);
  assert.equal(pathwayDecision({
    relayError: restoring,
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
  }).pass, true);
  const empty503 = relayEdgeError({ status: 503, body: '' });
  assert.equal(isPathwayLabBootLoop503(empty503), true);
  assert.equal(pathwayDecision({
    relayError: empty503,
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
  }).reason, 'pathway lab 401 alternate proof');
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({ status: 503, body: '   ' })), true);
});

test('pathway lab boot loop stays fail-closed on non-edge bodies and other statuses', () => {
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({ status: 502, body: 'Bad Gateway' })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 502,
    body: '<html><title>502 Bad Gateway</title></html>',
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 502,
    body: JSON.stringify({ ok: false, boot: 'starting', error: 'dashboard loading' }),
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({ status: 200, body: '' })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 200,
    body: JSON.stringify({ ok: true, boot: 'ready', positions: [], orders: [] }),
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({ status: 500, body: '' })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({ status: 504, body: '' })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 503,
    body: '<html><title>503 Service Unavailable</title></html>',
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 503,
    body: JSON.stringify({ ok: false, boot: 'ready', error: 'dashboard loading' }),
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 503,
    body: JSON.stringify({ ok: false, boot: 'starting', error: 'dashboard_busy' }),
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 503,
    body: JSON.stringify({
      api_state_error: 'canonical execution snapshot unavailable or stale',
    }),
  })), false);
  assert.equal(isPathwayLabBootLoop503(relayEdgeError({
    status: 502,
    body: '',
    url: 'https://doxed-btc-bot.fly.dev/health',
  })), false);
  assert.equal(pathwayDecision({
    relayError: relayEdgeError({ status: 200, body: '' }),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
  }).reason, 'not the pathway boot-loop 503');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth({ source_git_rev: '538a39e6c366' }),
    logText: '',
    logsUnauthorized: true,
  }).reason, 'failing revision proof missing');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth({ live_armed: true }),
    logText: '',
    logsUnauthorized: true,
  }).reason, 'paper-disarmed proof missing (live_armed)');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth({
      live_armed: false,
      force_paper_mode: false,
      bitfinex_live_enabled: false,
    }),
    logText: '',
    logsUnauthorized: true,
  }).reason, 'paper-disarmed proof missing (not_paper)');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth({ v1_post_ai: 'PASS' }),
    logText: '',
    logsUnauthorized: true,
  }).reason, 'startup FAIL proof contradicted');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
    recoverStalled: true,
  }).reason, 'recover stalled path');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
    enabled: false,
  }).reason, 'flag off');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: false,
  }).reason, 'startup FAIL proof missing');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: 'Pathway Lab startup validation FAILED — v1_post_ai=FAIL',
    logsUnauthorized: false,
  }).reason, 'paper disarm log missing');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
    rows: [pausedCheetah({ status: 'ACTIVE' })],
  }).exitCode, 2);
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
    rows: [pausedCheetah({ orphanOrderIds: ['ord-1'] })],
  }).reason, 'relays are not paused and disarmed');
  assert.equal(pathwayDecision({
    relayError: liveFlyEdge502EmptyError(),
    health: liveEarlyBootHealth(),
    logText: '',
    logsUnauthorized: true,
    rows: [],
  }).exitCode, 2);
});
