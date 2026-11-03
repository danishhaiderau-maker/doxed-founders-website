/**
 * RK 21:05: /api/health must not touch Neon; DB probe lives at /api/health/db.
 *
 * Mutation proof: restore a prisma call inside check() → prismaCalls becomes
 * non-zero and/or database leaves 'skipped'.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { HealthController } from './health.controller';

function prismaTrap() {
  const calls: string[] = [];
  const prisma = {
    connect: async () => {
      calls.push('connect');
      return true;
    },
    $queryRaw: async () => {
      calls.push('$queryRaw');
      return [{ '?column?': 1 }];
    },
    listingApplication: {
      count: async () => {
        calls.push('listingApplication.count');
        return 0;
      },
    },
  };
  return { prisma: prisma as never, calls };
}

test('/api/health (check) is DB-free: no prisma calls, database=skipped', async () => {
  const { prisma, calls } = prismaTrap();
  const ctl = new HealthController(prisma);
  const body = ctl.check();
  assert.equal(body.status, 'ok');
  assert.equal(body.services.api, 'ok');
  assert.equal(body.services.database, 'skipped');
  assert.equal(calls.length, 0);
});

test('/api/health/live stays DB-free', () => {
  const { prisma, calls } = prismaTrap();
  const ctl = new HealthController(prisma);
  const body = ctl.live();
  assert.equal(body.status, 'ok');
  assert.ok(typeof body.timestamp === 'string');
  assert.equal(calls.length, 0);
});

test('/api/health/db still probes Neon', async () => {
  const { prisma, calls } = prismaTrap();
  const ctl = new HealthController(prisma);
  const body = await ctl.checkDb();
  assert.equal(body.status, 'ok');
  assert.equal(body.services.database, 'ok');
  assert.ok(calls.includes('connect'));
  assert.ok(calls.includes('$queryRaw'));
  assert.ok(calls.includes('listingApplication.count'));
});

test('mutation proof: check() source must not call prisma', () => {
  const src = readFileSync(path.join(__dirname, 'health.controller.ts'), 'utf8');
  const checkStart = src.indexOf('@Get()\n  check()');
  const dbStart = src.indexOf("@Get('db')");
  assert.ok(checkStart >= 0 && dbStart > checkStart);
  const checkBody = src.slice(checkStart, dbStart);
  assert.equal(checkBody.includes('this.prisma'), false);
  assert.ok(checkBody.includes("'skipped'") || checkBody.includes('"skipped"'));
});
