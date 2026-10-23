// B4 R3 (#852, 24 Oct): REAL Prisma path for the account live-mutex + LiveEntryFence race. Throwaway Neon branch ONLY.
// Executes the relay's exported functions (takeLiveMutexLock -> lockedEntryClaimInsert [copy-cluster lock + INSERT INTENT]
// -> countLiveMutexOthers [excl. self, incl. INTENT < 180 s + unexpired fences], and acquireLiveEntryFence) through a
// Prisma 6.8.2 client generated from prisma/schema.prisma (output /tmp/b4gen/client, previewFeatures driverAdapters) with
// @prisma/adapter-neon (WebSocket/443, because the box blocks TCP 5432). Branch URL read from /tmp/b4.url (never printed).
// Run: npx tsx scripts/realpg/b4r3-prisma-race.ts  (after applying the 20261024040000 migration + b4r3-* fixtures).
// RESULTS 2026-10-24 ~05:05-05:13 AEDT, branch pr852-b4r3-prisma-race-throwaway (br-sweet-hill-ap4jv2hr):
//   With Prisma's default 5 s fence txn timeout: the fence expired in 3 of 5 runs under the 20-claim burst (lock wait
//   ~9 s over the transpacific socket) -> unfenced flatten. Fixed: LIVE_ENTRY_FENCE_TX_OPTIONS {maxWait 10 s, timeout 20 s}.
//   After the fix, 6/6 runs PASS: 21 sessions (20 claims over 5 agents + 1 fence), 20 backends, 190 concurrently launched
//   pairs, <=1 admitted per run (0 when the fence won), 0 admitted after the fence, every claim locked after the fence saw
//   it, 0 overlapping locked sections. Control (same sequence, no live-mutex lock, 10 claims/5 agents): 5/10 admitted
//   (race is real). INTENT window: 200 s-old INTENT -> 0 others, 100 s-old -> 1.
import { readFileSync } from 'fs';
import { PrismaClient } from '/tmp/b4gen/client';
import { PrismaNeon } from '/tmp/b4gen/node_modules/@prisma/adapter-neon';
import { neonConfig } from '/tmp/b4gen/node_modules/@neondatabase/serverless';
import ws from '/tmp/b4gen/node_modules/ws';
neonConfig.webSocketConstructor = ws as any;
import {
  takeLiveMutexLock, lockedEntryClaimInsert, countLiveMutexOthers, acquireLiveEntryFence,
} from '../../src/trading-agents/signal-subscriber-execution.service';

const url = readFileSync('/tmp/b4.url', 'utf8').trim().replace('-pooler', '');
const sep = url.includes('?') ? '&' : '?';
void sep;
// Box egress blocks TCP 5432, so Prisma runs over Neon's WebSocket (443) driver adapter; same query engine + interactive txns.
const prisma = new PrismaClient({ adapter: new PrismaNeon({ connectionString: url, max: 40 } as any) } as any);
const ts = async (tx: any) => ((await tx.$queryRawUnsafe('SELECT clock_timestamp() AS t, pg_backend_pid() AS p')) as any[])[0];

type Row = { kind: string; who: string; agent?: string; others?: number; admitted?: boolean; t0: number; tLock?: Date; tEnd?: Date; pid?: number; err?: string };
async function claim(userId: string, agentId: string, cycleId: string, useLock: boolean): Promise<Row> {
  const t0 = Date.now();
  try {
    return await prisma.$transaction(async (tx) => {
      // Same order as runEntryClaimTx (service.ts ~10514-10521): live-mutex lock -> copy-cluster lock + INSERT INTENT ->
      // count others (excluding self, incl. INTENT < 180 s, + unexpired fences).
      if (useLock) await takeLiveMutexLock(tx as any, userId);
      const a = await ts(tx);
      const claimed = await lockedEntryClaimInsert(tx as any, { lockKey: `copy-cluster:${userId}:${agentId}`, cycleId, userId, venue: 'bitfinex' });
      const others = await countLiveMutexOthers(tx as any, userId, claimed.id);
      const b = await ts(tx);
      return { kind: useLock ? 'claim' : 'control', who: cycleId, agent: agentId.slice(0, 6), others, admitted: others === 0, t0, tLock: a.t, tEnd: b.t, pid: a.p };
    }, { timeout: 60_000, maxWait: 60_000 });
  } catch (e) { return { kind: 'claim', who: cycleId, t0, err: String((e as Error).message).slice(0, 160) }; }
}
async function fence(userId: string, fid: string): Promise<Row> {
  const t0 = Date.now();
  // Observation-only proxy: stamp the server clock inside the fence's locked section (right before its INSERT).
  let tLock: Date | undefined;
  const observed = {
    $transaction: (fn: any, opts?: any) => (prisma as any).$transaction((tx: any) => fn({
      $executeRawUnsafe: (...a: any[]) => tx.$executeRawUnsafe(...a),
      liveEntryFence: { create: async (a: any) => { tLock = (await ts(tx)).t; return tx.liveEntryFence.create(a); } },
    }), opts),
    liveEntryFence: (prisma as any).liveEntryFence,
  };
  try { await acquireLiveEntryFence(observed as any, userId, fid, 'B4R3_RACE'); }
  catch (e) { return { kind: 'fence', who: fid, t0, err: String((e as Error).message).slice(-200) }; }
  const f = await (prisma as any).liveEntryFence.findUnique({ where: { id: fid } });
  void f; return { kind: 'fence', who: fid, t0, tLock, tEnd: tLock };
}
const overlaps = (rows: Row[]) => {
  const r = rows.filter((x) => x.tLock && x.tEnd).sort((a, b) => +a.tLock! - +b.tLock!);
  let n = 0; for (let i = 1; i < r.length; i++) if (+r[i].tLock! < +r[i - 1].tEnd!) n++; return n;
};
const concurrentPairs = (rows: Row[]) => { // sessions whose client-side windows overlapped (they really raced)
  const r = rows.filter((x) => x.tEnd); let n = 0;
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) if (Math.abs(r[i].t0 - r[j].t0) < 200) n++; return n;
};
(async () => {
  const agents = ((await prisma.$queryRawUnsafe(`SELECT DISTINCT "agentId" FROM "SignalCycle" WHERE id LIKE 'b4r3-cy-%' ORDER BY 1`)) as any[]).map((x) => x.agentId);
  const cyc = (ai: number, g: number) => `b4r3-cy-${ai + 1}-${g}`;
  // reset this harness's own synthetic rows from any earlier attempt (throwaway branch only)
  await prisma.$executeRawUnsafe(`DELETE FROM "SignalCycleParticipant" WHERE "userId" LIKE 'b4r3-u%'`);
  await prisma.$executeRawUnsafe(`DELETE FROM "LiveEntryFence" WHERE "userId" LIKE 'b4r3-u%'`);
  await Promise.all(Array.from({ length: 25 }, () => prisma.$queryRawUnsafe('SELECT 1'))); // warm pool
  // A) locked race: 20 claims across 5 agents (tiles) + 1 fence, all launched together, user b4r3-u1
  const jobs: Promise<Row>[] = [];
  for (let i = 0; i < 20; i++) { jobs.push(claim('b4r3-u1', agents[i % agents.length], cyc(i % agents.length, Math.floor(i / agents.length) + 1), true)); if (i === 1) jobs.push(fence('b4r3-u1', 'b4r3-f1')); }
  const A = await Promise.all(jobs);
  const fenceRow = A.find((x) => x.kind === 'fence')!;
  const fenceAt = fenceRow.tEnd ? +fenceRow.tEnd : Infinity;
  console.log('fence', JSON.stringify({ err: fenceRow.err ?? null, fenceLockedSectionServer: fenceRow.tLock }));
  const claimsA = A.filter((x) => x.kind === 'claim');
  const resA = {
    sessions: A.length, errors: claimsA.filter((x) => x.err).map((x) => x.err), backends: new Set(claimsA.map((x) => x.pid)).size,
    admitted: claimsA.filter((x) => x.admitted).map((x) => x.who), refused: claimsA.filter((x) => x.admitted === false).length,
    othersHist: claimsA.reduce((m: any, x) => ((m[String(x.others)] = (m[String(x.others)] ?? 0) + 1), m), {}),
    admittedAfterFence: claimsA.filter((x) => x.admitted && +x.tLock! > fenceAt).length,
    overlappingLockedSections: overlaps(A.filter((x) => x.kind === 'claim')), claimsLockedAfterFence: claimsA.filter((x) => +x.tLock! > fenceAt).length, claimsAfterFenceSeeingFence: claimsA.filter((x) => +x.tLock! > fenceAt && (x.others ?? 0) >= 1).length, concurrentlyLaunchedPairs: concurrentPairs(claimsA),
    maxLockWaitMs: Math.max(...claimsA.filter((x) => x.tLock).map((x) => +x.tLock! - x.t0)),
  };
  // B) control: identical sequence WITHOUT the live-mutex lock, 10 claims across 5 agents, user b4r3-u2
  const B = await Promise.all(Array.from({ length: 10 }, (_, i) => claim('b4r3-u2', agents[i % agents.length], cyc(i % agents.length, 5 + Math.floor(i / agents.length)), false)));
  const resB = { sessions: B.length, errors: B.filter((x) => x.err).length, admitted: B.filter((x) => x.admitted).length, backends: new Set(B.map((x) => x.pid)).size };
  // C) INTENT window: a 200 s-old INTENT is ignored, a 100 s-old INTENT counts (user b4r3-u3)
  await prisma.$executeRawUnsafe(`INSERT INTO "SignalCycleParticipant" (id,"cycleId","userId",venue,status,"createdAt","updatedAt") VALUES ('b4r3-old',$1,'b4r3-u3','bitfinex','INTENT',now()-interval '200 seconds',now())`, cyc(0, 8));
  const C1 = await claim('b4r3-u3', agents[1], cyc(1, 8), true);
  await prisma.$executeRawUnsafe(`DELETE FROM "SignalCycleParticipant" WHERE "userId"='b4r3-u3' AND id<>'b4r3-old'`);
  await prisma.$executeRawUnsafe(`UPDATE "SignalCycleParticipant" SET "createdAt"=now()-interval '100 seconds' WHERE id='b4r3-old'`);
  const C2 = await claim('b4r3-u3', agents[2], cyc(2, 8), true);
  const resC = { intent200sOthers: C1.others, intent100sOthers: C2.others };
  const fenceRows = await (prisma as any).liveEntryFence.count({ where: { userId: 'b4r3-u1' } });
  console.log(JSON.stringify({ A: resA, fenceRows, B: resB, C: resC }, null, 1));
  const pass = !fenceRow.err && resA.errors.length === 0 && resA.admitted.length <= 1 && resA.admittedAfterFence === 0 && resA.overlappingLockedSections === 0
    && resB.admitted > 1 && resC.intent200sOthers === 0 && resC.intent100sOthers === 1;
  console.log(pass ? 'B4R3 PASS' : 'B4R3 FAIL');
  await prisma.$disconnect();
})().catch(async (e) => { console.error('HARNESS_ERROR', String(e?.message ?? e).replace(/postgres(ql)?:\/\/\S+/g, '<redacted>').slice(0, 400)); await prisma.$disconnect(); process.exit(1); });
