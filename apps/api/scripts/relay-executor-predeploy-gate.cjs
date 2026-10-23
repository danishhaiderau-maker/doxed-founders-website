#!/usr/bin/env node
/**
 * LIVE-FIX-1481 relay-executor pre-deploy gate (Railway preDeployCommand).
 *
 * The executor is the only real-money process and runs as ONE replica, so a
 * deploy is a container swap. A close that lands during the swap used to be
 * lost (a Tile 6 copy, 12 Oct 17:07 AEDT). This gate runs after the build
 * and before the new container goes live: it waits until no Bitfinex live copy
 * participant is OPEN or PENDING_ENTRY, then lets the swap proceed.
 *
 * Fail-closed: if exposure persists past the wait budget the deploy FAILS and
 * Railway keeps the current (healthy) executor running. Redeploy once flat.
 * Escape hatch for an emergency fix: RELAY_EXECUTOR_PREDEPLOY_GATE=off.
 */
const { PrismaClient } = require('@prisma/client');

const MAX_WAIT_MS = Number(process.env.RELAY_EXECUTOR_PREDEPLOY_MAX_WAIT_MS ?? 270_000);
const POLL_MS = 2_000;
// Flat must hold across consecutive reads so a fresh entry isn't racing us.
const FLAT_STREAK = 2;

const EXPOSURE_SQL = `
  SELECT COUNT(*)::int AS "n"
  FROM "SignalCycleParticipant" p
  JOIN "TradingAgentInstance" i ON i."userId" = p."userId"
  JOIN "SignalCycle" c ON c."id" = p."cycleId" AND c."agentId" = i."agentId"
  WHERE i."exchangeProvider" = 'bitfinex'
    AND p."status"::text IN ('OPEN', 'PENDING_ENTRY')`;

async function exposureCount(prisma) {
  const rows = await prisma.$queryRawUnsafe(EXPOSURE_SQL);
  return Number(rows?.[0]?.n ?? 0);
}

async function runGate({ prisma, now = () => Date.now(), sleep = (ms) => new Promise((r) => setTimeout(r, ms)), log = console.log, maxWaitMs = MAX_WAIT_MS }) {
  const started = now();
  let streak = 0;
  let last = null;
  for (;;) {
    try {
      last = await exposureCount(prisma);
    } catch (err) {
      last = null;
      log(`[PREDEPLOY-GATE] exposure read failed: ${err && err.message ? err.message : err}`);
    }
    streak = last === 0 ? streak + 1 : 0;
    if (streak >= FLAT_STREAK) {
      log(`[PREDEPLOY-GATE] flat (0 OPEN/PENDING_ENTRY) after ${now() - started}ms — swap allowed`);
      return 0;
    }
    if (now() - started >= maxWaitMs) {
      log(`[PREDEPLOY-GATE] REFUSED: exposure=${last ?? 'unknown'} after ${now() - started}ms; keeping current executor. Redeploy when flat.`);
      return 1;
    }
    await sleep(POLL_MS);
  }
}

module.exports = { runGate, EXPOSURE_SQL, FLAT_STREAK };

if (require.main === module) {
  (async () => {
    if ((process.env.RELAY_EXECUTOR_PREDEPLOY_GATE ?? '').trim().toLowerCase() === 'off') {
      console.log('[PREDEPLOY-GATE] disabled by RELAY_EXECUTOR_PREDEPLOY_GATE=off');
      process.exit(0);
    }
    const prisma = new PrismaClient();
    let code = 1;
    try {
      code = await runGate({ prisma });
    } finally {
      await prisma.$disconnect().catch(() => {});
    }
    process.exit(code);
  })();
}
