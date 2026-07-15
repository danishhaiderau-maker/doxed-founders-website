/**
 * Integration test for the Idea Validator service (Phase 6).
 *
 * Verifies the application-level invariants without needing a real DB or
 * AI Gateway:
 *   1. Idempotency — the same ideaText within the 24h window reuses the
 *      existing row instead of creating a new one (unless force=true).
 *   2. Persistence — a fresh idea creates an IdeaCheck row with status
 *      PENDING, then transitions through RUNNING → COMPLETED with a
 *      result payload.
 *   3. Status polling — getCheck + listChecks return the expected rows.
 *
 * Prisma + AI Gateway + Browser Research are stubbed. The stubs mirror
 * the slice of the interface the service consumes; behaviour matches the
 * real Prisma client per the schema.
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { IdeaValidatorService } from './idea-validator.service';

type IdeaCheckRow = {
  id: string;
  userId: string;
  projectId: string | null;
  applicationId: string | null;
  ideaText: string;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  searchQueries: unknown;
  resultJson: unknown;
  differentiationScore: number | null;
  similarProjectsJson: unknown;
  suggestedOssJson: unknown;
  errorMessage: string | null;
  dismissed: boolean;
  viewed: boolean;
  completedAt: Date | null;
  createdAt: Date;
};

function makeStubPrisma() {
  const rows: IdeaCheckRow[] = [];
  let counter = 0;
  return {
    ideaCheck: {
      create: async ({ data }: { data: Partial<IdeaCheckRow> }) => {
        const row: IdeaCheckRow = {
          id: `check-${++counter}`,
          userId: data.userId ?? '',
          projectId: data.projectId ?? null,
          applicationId: data.applicationId ?? null,
          ideaText: data.ideaText ?? '',
          status: data.status ?? 'PENDING',
          searchQueries: null,
          resultJson: null,
          differentiationScore: null,
          similarProjectsJson: null,
          suggestedOssJson: null,
          errorMessage: null,
          dismissed: false,
          viewed: false,
          completedAt: null,
          createdAt: new Date(),
        };
        rows.push(row);
        return row;
      },
      findFirst: async ({
        where,
        orderBy,
      }: {
        where: {
          userId?: string;
          ideaText?: string;
          status?: { in: string[] };
          createdAt?: { gte: Date };
          id?: string;
        };
        orderBy?: { createdAt?: 'desc' | 'asc' };
      }) => {
        let filtered = [...rows];
        if (where.userId) filtered = filtered.filter((r) => r.userId === where.userId);
        if (where.ideaText) filtered = filtered.filter((r) => r.ideaText === where.ideaText);
        if (where.id) filtered = filtered.filter((r) => r.id === where.id);
        if (where.status?.in) {
          filtered = filtered.filter((r) => where.status!.in.includes(r.status));
        }
        if (where.createdAt?.gte) {
          filtered = filtered.filter((r) => r.createdAt.getTime() >= where.createdAt!.gte!.getTime());
        }
        if (orderBy?.createdAt === 'desc') {
          filtered.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
        }
        return filtered[0] ?? null;
      },
      findMany: async ({
        where,
        orderBy,
        take,
        select,
        distinct,
      }: {
        where?: { userId?: string; status?: string; viewed?: boolean; dismissed?: boolean };
        orderBy?: { createdAt?: 'desc' | 'asc' };
        take?: number;
        select?: unknown;
        distinct?: string[];
      }) => {
        let out = where?.userId ? rows.filter((r) => r.userId === where.userId) : [...rows];
        if (where?.status) out = out.filter((r) => r.status === where.status);
        if (where?.viewed !== undefined) out = out.filter((r) => r.viewed === where.viewed);
        if (where?.dismissed !== undefined) out = out.filter((r) => r.dismissed === where.dismissed);
        if (orderBy?.createdAt === 'desc') {
          out.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
        }
        if (take !== undefined) out = out.slice(0, take);
        if (distinct?.includes('userId')) {
          const seen = new Set<string>();
          out = out.filter((r) => (seen.has(r.userId) ? false : (seen.add(r.userId), true)));
        }
        // `select` only projects userId in the test — keep the row shape so
        // assertions stay simple.
        if (select && (select as { userId?: boolean }).userId) {
          return out.map((r) => ({ userId: r.userId }));
        }
        return out;
      },
      update: async ({
        where,
        data,
      }: {
        where: { id?: string };
        data: Partial<IdeaCheckRow>;
      }) => {
        const r = rows.find((x) => x.id === where.id);
        if (r) Object.assign(r, data);
        return r;
      },
      updateMany: async ({
        where,
        data,
      }: {
        where: { id?: string; userId?: string };
        data: Partial<IdeaCheckRow>;
      }) => {
        let count = 0;
        for (const r of rows) {
          if (
            (!where.id || r.id === where.id) &&
            (!where.userId || r.userId === where.userId)
          ) {
            Object.assign(r, data);
            count++;
          }
        }
        return { count };
      },
    },
    _rows: rows,
  };
}

describe('IdeaValidatorService — idempotency + persistence', () => {
  let stub: ReturnType<typeof makeStubPrisma>;
  let svc: IdeaValidatorService;

  beforeEach(() => {
    stub = makeStubPrisma();
    svc = new IdeaValidatorService(
      stub as never,
      {
        // AI Proxy stub — never actually invoked because we override runResearch below.
        decideRoute: async () => ({ tier: 'auto' }),
        invoke: async () => ({ ok: true, status: 200, body: '' }),
      } as never,
      { runResearch: async () => [] } as never,
    );
    // Replace the private research pipeline with a deterministic stub so the
    // test exercises the orchestration transitions without a real model call.
    (svc as unknown as {
      runResearch: (auth: unknown, rowId: string, ideaText: string) => Promise<void>;
    }).runResearch = async (_auth, rowId) => {
      await stub.ideaCheck.update({ where: { id: rowId }, data: { status: 'RUNNING' } });
      await stub.ideaCheck.update({
        where: { id: rowId },
        data: {
          status: 'COMPLETED',
          resultJson: { verdict: 'novel', summary: 'stubbed' },
          differentiationScore: 95,
          completedAt: new Date(),
        },
      });
    };
  });

  it('creates a fresh PENDING row for a new idea', async () => {
    const row = await svc.checkIdea(
      { userId: 'user-1', nodeId: 'api' },
      { ideaText: 'A novel DEX with MEV protection' },
    );
    assert.equal(row.userId, 'user-1');
    // The service returns the row as PENDING, but the fire-and-forget
    // research may have already advanced it by the time the awaited
    // promise resolves. The contract: the row exists with the right
    // userId + ideaText, and after a tick it must be COMPLETED with a
    // result payload.
    assert.equal(row.ideaText, 'A novel DEX with MEV protection');
    assert.ok(
      row.status === 'PENDING' || row.status === 'RUNNING' || row.status === 'COMPLETED',
      `unexpected initial status ${row.status}`,
    );
    await new Promise((r) => setTimeout(r, 20));
    const completed = stub._rows.find((r) => r.id === row.id)!;
    assert.equal(completed.status, 'COMPLETED');
    assert.equal(completed.differentiationScore, 95);
  });

  it('reuses an existing PENDING/RUNNING/COMPLETED row within 24h', async () => {
    const first = await svc.checkIdea(
      { userId: 'user-1', nodeId: 'api' },
      { ideaText: 'Same idea text for idempotency' },
    );
    await new Promise((r) => setTimeout(r, 20));
    // Second call with identical text — should NOT create a new row.
    const reused = await svc.checkIdea(
      { userId: 'user-1', nodeId: 'api' },
      { ideaText: 'Same idea text for idempotency' },
    );
    assert.equal(reused.id, first.id);
    assert.equal(stub._rows.length, 1);
  });

  it('force=true bypasses idempotency and creates a new row', async () => {
    await svc.checkIdea(
      { userId: 'user-2', nodeId: 'api' },
      { ideaText: 'force test idea' },
    );
    await new Promise((r) => setTimeout(r, 20));
    await svc.checkIdea(
      { userId: 'user-2', nodeId: 'api' },
      { ideaText: 'force test idea', force: true },
    );
    assert.equal(stub._rows.length, 2);
  });

  it('idempotency is per-user — different users get distinct rows', async () => {
    await svc.checkIdea(
      { userId: 'alice', nodeId: 'api' },
      { ideaText: 'shared idea text' },
    );
    await svc.checkIdea(
      { userId: 'bob', nodeId: 'api' },
      { ideaText: 'shared idea text' },
    );
    assert.equal(stub._rows.length, 2);
  });

  it('getCheck returns by id+user and rejects other users', async () => {
    const row = await svc.checkIdea(
      { userId: 'owner', nodeId: 'api' },
      { ideaText: 'my idea' },
    );
    const own = await svc.getCheck(row.id, 'owner');
    assert.ok(own);
    const other = await svc.getCheck(row.id, 'intruder');
    assert.equal(other, null);
  });

  it('listChecks returns user rows newest-first', async () => {
    await svc.checkIdea({ userId: 'u', nodeId: 'api' }, { ideaText: 'a' });
    await new Promise((r) => setTimeout(r, 5));
    await svc.checkIdea({ userId: 'u', nodeId: 'api' }, { ideaText: 'b' });
    await new Promise((r) => setTimeout(r, 5));
    await svc.checkIdea({ userId: 'u', nodeId: 'api' }, { ideaText: 'c' });
    const list = await svc.listChecks('u', 10);
    assert.equal(list.length, 3);
    // newest first
    assert.ok(list[0]!.createdAt.getTime() >= list[list.length - 1]!.createdAt.getTime());
  });

  it('usersWithUnviewedCompletedChecks returns distinct userIds', async () => {
    await svc.checkIdea({ userId: 'alice', nodeId: 'api' }, { ideaText: 'a1' });
    await svc.checkIdea({ userId: 'bob', nodeId: 'api' }, { ideaText: 'b1' });
    await new Promise((r) => setTimeout(r, 30));
    const userIds = await svc.usersWithUnviewedCompletedChecks();
    assert.ok(userIds.includes('alice'));
    assert.ok(userIds.includes('bob'));
  });
});
