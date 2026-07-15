/**
 * Integration test for the Flight Recorder (Phase 1 — kernel service #2).
 *
 * Verifies the persistence contract used by the Routing Engine and the AI
 * Gateway:
 *   1. `record()` inserts a RoutingDecision row with all fields mapped.
 *   2. `updateOutcome()` patches the outcome-signal columns in place.
 *   3. `updateUsage()` patches usage columns (latency / cost / tokens).
 *   4. `findRecent()` returns rows newest-first.
 *
 * The Prisma client is replaced with an in-memory stub so the test runs
 * offline (no DATABASE_URL required). The stub mirrors the slice of the
 * `PrismaService` interface that `FlightRecorderService` exercises — the
 * real Prisma client has the same method shapes per the schema.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { FlightRecorderService } from './flight-recorder.service';
import type { RecordInput } from './flight-recorder.types';

type RoutingDecisionRow = {
  id: string;
  requestId: string;
  userId: string;
  workspaceId: string | null;
  intent: string;
  profile: string;
  candidates: unknown;
  chosenProvider: string;
  chosenModel: string;
  cacheLevel: string;
  cacheKey: string | null;
  promptHash: string;
  tokenCountPrompt: number | null;
  tokenCountCompletion: number | null;
  latencyMs: number | null;
  costUsd: number | null;
  accepted: boolean | null;
  retried: boolean | null;
  edited: boolean | null;
  rating: number | null;
  createdAt: Date;
};

function makeStubPrisma() {
  const rows: RoutingDecisionRow[] = new Array() as RoutingDecisionRow[];
  let counter = 0;
  return {
    routingDecision: {
      create: async ({ data }: { data: Partial<RoutingDecisionRow> }) => {
        const row: RoutingDecisionRow = {
          id: `row-${++counter}`,
          requestId: data.requestId ?? '',
          userId: data.userId ?? '',
          workspaceId: data.workspaceId ?? null,
          intent: data.intent ?? '',
          profile: data.profile ?? '',
          candidates: data.candidates ?? [],
          chosenProvider: data.chosenProvider ?? '',
          chosenModel: data.chosenModel ?? '',
          cacheLevel: data.cacheLevel ?? '',
          cacheKey: data.cacheKey ?? null,
          promptHash: data.promptHash ?? '',
          tokenCountPrompt: data.tokenCountPrompt ?? null,
          tokenCountCompletion: data.tokenCountCompletion ?? null,
          latencyMs: data.latencyMs ?? null,
          costUsd: data.costUsd ?? null,
          accepted: null,
          retried: null,
          edited: null,
          rating: null,
          createdAt: new Date(),
        };
        rows.push(row);
        return row;
      },
      updateMany: async ({
        where,
        data,
      }: {
        where: { requestId?: string };
        data: Partial<RoutingDecisionRow>;
      }) => {
        let count = 0;
        for (const r of rows) {
          if (!where.requestId || r.requestId === where.requestId) {
            Object.assign(r, data);
            count++;
          }
        }
        return { count };
      },
      findMany: async ({
        where,
        orderBy,
        take,
      }: {
        where?: { userId?: string };
        orderBy?: { createdAt?: 'desc' | 'asc' };
        take?: number;
      }) => {
        let out = where?.userId
          ? rows.filter((r) => r.userId === where.userId)
          : [...rows];
        if (orderBy?.createdAt === 'desc') {
          out = out.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
        }
        if (take !== undefined) out = out.slice(0, take);
        return out;
      },
    },
    // exposed for assertions
    _rows: rows,
  };
}

describe('FlightRecorderService — persistence', () => {
  it('record() persists a RoutingDecision row with all fields', async () => {
    const stub = makeStubPrisma();
    const svc = new FlightRecorderService(stub as never);
    const input: RecordInput = {
      requestId: 'req-1',
      userId: 'user-1',
      workspaceId: 'ws-1',
      intent: 'code',
      profile: 'balanced',
      candidates: [{ provider: 'openai', model: 'gpt-4', score: 0.9 }],
      chosenProvider: 'openai',
      chosenModel: 'gpt-4',
      cacheLevel: 'miss',
      cacheKey: 'v1:abc',
      promptHash: 'v1:abc',
      tokenCountPrompt: 120,
      tokenCountCompletion: 80,
      latencyMs: 450,
      costUsd: 0.012,
    };
    const row = await svc.record(input);
    assert.equal(row.requestId, 'req-1');
    assert.equal(row.userId, 'user-1');
    assert.equal(row.chosenModel, 'gpt-4');
    assert.equal(row.cacheKey, 'v1:abc');
    assert.equal(stub._rows.length, 1);
    assert.equal(stub._rows[0]!.intent, 'code');
  });

  it('updateOutcome() patches accepted/retried/edited/rating in place', async () => {
    const stub = makeStubPrisma();
    const svc = new FlightRecorderService(stub as never);
    await svc.record({
      requestId: 'req-2',
      userId: 'user-1',
      intent: 'reasoning',
      profile: 'turbo',
      candidates: [],
      chosenProvider: 'deepseek',
      chosenModel: 'deepseek-r1',
      cacheLevel: 'miss',
      promptHash: 'v1:def',
    });
    assert.equal(stub._rows[0]!.accepted, null);
    await svc.updateOutcome('req-2', {
      accepted: true,
      retried: false,
      edited: false,
      rating: 5,
    });
    assert.equal(stub._rows[0]!.accepted, true);
    assert.equal(stub._rows[0]!.retried, false);
    assert.equal(stub._rows[0]!.edited, false);
    assert.equal(stub._rows[0]!.rating, 5);
  });

  it('updateOutcome() with empty payload is a no-op', async () => {
    const stub = makeStubPrisma();
    const svc = new FlightRecorderService(stub as never);
    await svc.record({
      requestId: 'req-3',
      userId: 'u',
      intent: 'agent',
      profile: 'autonomous',
      candidates: [],
      chosenProvider: 'p',
      chosenModel: 'm',
      cacheLevel: 'hit',
      promptHash: 'h',
    });
    await svc.updateOutcome('req-3', {});
    assert.equal(stub._rows[0]!.accepted, null);
  });

  it('updateUsage() patches latency / cost / tokens', async () => {
    const stub = makeStubPrisma();
    const svc = new FlightRecorderService(stub as never);
    await svc.record({
      requestId: 'req-4',
      userId: 'u',
      intent: 'simple_qa',
      profile: 'turbo',
      candidates: [],
      chosenProvider: 'p',
      chosenModel: 'm',
      cacheLevel: 'miss',
      promptHash: 'h',
    });
    await svc.updateUsage('req-4', {
      latencyMs: 200,
      costUsd: 0.005,
      tokenCountPrompt: 50,
      tokenCountCompletion: 25,
    });
    assert.equal(stub._rows[0]!.latencyMs, 200);
    assert.equal(stub._rows[0]!.costUsd, 0.005);
    assert.equal(stub._rows[0]!.tokenCountPrompt, 50);
    assert.equal(stub._rows[0]!.tokenCountCompletion, 25);
  });

  it('findRecent() returns rows newest-first, filtered by user', async () => {
    const stub = makeStubPrisma();
    const svc = new FlightRecorderService(stub as never);
    for (let i = 0; i < 5; i++) {
      await svc.record({
        requestId: `req-${i}`,
        userId: i % 2 === 0 ? 'user-a' : 'user-b',
        intent: 'code',
        profile: 'balanced',
        candidates: [],
        chosenProvider: 'p',
        chosenModel: 'm',
        cacheLevel: 'miss',
        promptHash: `h-${i}`,
      });
    }
    const aRows = await svc.findRecent({ userId: 'user-a' });
    assert.equal(aRows.length, 3);
    // newest-first → req-4 (last inserted) before req-0 (first inserted)
    assert.ok(aRows[0]!.createdAt.getTime() >= aRows[aRows.length - 1]!.createdAt.getTime());
    for (const r of aRows) assert.equal(r.userId, 'user-a');

    const limited = await svc.findRecent({ limit: 1 });
    assert.equal(limited.length, 1);
  });
});
