/**
 * Integration test for the Memory Engine (Phase 1 — kernel service #3).
 *
 * Verifies restart/recovery:
 *   1. State written via `set()` survives a "restart" (a fresh service
 *      instance pointed at the same backing store).
 *   2. `get()` retrieves persisted entries on the new instance.
 *   3. Workspace `expiresAt` is honoured — expired rows read as null.
 *   4. Conversation history is append-only.
 *
 * The Prisma client is stubbed with an in-memory store. Two separate
 * `MemoryEngineService` instances built on the same store simulate a
 * process restart (NestJS container teardown + bootstrap).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryEngineService } from './memory-engine.service';

type ConvRow = {
  id: string;
  sessionId: string;
  userId: string | null;
  role: string;
  content: string;
  tokens: number;
  createdAt: Date;
};

type KvRow = {
  id: string;
  scope: string;
  key: string;
  value: unknown;
  source: string | null;
  confidence: number | null;
  expiresAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
};

function makeStubPrisma() {
  const conv: ConvRow[] = [];
  const project: KvRow[] = [];
  const founder: KvRow[] = [];
  const workspace: KvRow[] = [];
  let idCounter = 0;
  const nextId = () => `row-${++idCounter}`;

  const kvTable = (rows: KvRow[], scopeField: string) => ({
    findUnique: async ({
      where,
    }: {
      where: { [k: string]: Record<string, string> };
    }) => {
      const w = where[`${scopeField}_key`];
      if (!w) return null;
      return (
        rows.find(
          (r) => r.scope === w[scopeField] && r.key === w.key,
        ) ?? null
      );
    },
    findMany: async ({
      where,
      orderBy,
      take,
    }: {
      where?: Record<string, unknown>;
      orderBy?: { updatedAt?: 'desc' | 'asc' };
      take?: number;
    }) => {
      let out = [...rows];
      if (where?.[scopeField]) {
        out = out.filter((r) => r.scope === where[scopeField]);
      }
      if (where?.key && typeof where.key === 'object' && 'startsWith' in (where.key as object)) {
        const prefix = (where.key as { startsWith: string }).startsWith;
        out = out.filter((r) => r.key.startsWith(prefix));
      }
      if (orderBy?.updatedAt === 'desc') {
        out.sort((a, b) => b.updatedAt.getTime() - a.updatedAt.getTime());
      }
      if (take !== undefined) out = out.slice(0, take);
      return out;
    },
    upsert: async ({
      where,
      create,
      update,
    }: {
      where: { [k: string]: Record<string, string> };
      create: Partial<KvRow>;
      update: Partial<KvRow>;
    }) => {
      const w = where[`${scopeField}_key`];
      const existing = rows.find(
        (r) => r.scope === w[scopeField] && r.key === w.key,
      );
      if (existing) {
        Object.assign(existing, update, { updatedAt: new Date() });
        return existing;
      }
      const row: KvRow = {
        id: nextId(),
        scope: create.scope ?? w[scopeField],
        key: create.key ?? w.key,
        value: create.value,
        source: create.source ?? null,
        confidence: create.confidence ?? null,
        expiresAt: create.expiresAt ?? null,
        createdAt: new Date(),
        updatedAt: new Date(),
      };
      rows.push(row);
      return row;
    },
    deleteMany: async ({
      where,
    }: {
      where: { [k: string]: string };
    }) => {
      const before = rows.length;
      for (let i = rows.length - 1; i >= 0; i--) {
        const r = rows[i]!;
        let match = true;
        if (where.id !== undefined) match = match && r.id === where.id;
        if (where[scopeField] !== undefined)
          match = match && r.scope === where[scopeField];
        if (where.key !== undefined) match = match && r.key === where.key;
        if (match) rows.splice(i, 1);
      }
      return { count: before - rows.length };
    },
    delete: async ({ where }: { where: { id: string } }) => {
      const idx = rows.findIndex((r) => r.id === where.id);
      if (idx >= 0) {
        const [removed] = rows.splice(idx, 1);
        return removed;
      }
      return null;
    },
  });

  return {
    conversationMemory: {
      create: async ({ data }: { data: Partial<ConvRow> }) => {
        const row: ConvRow = {
          id: nextId(),
          sessionId: data.sessionId ?? '',
          userId: data.userId ?? null,
          role: data.role ?? 'user',
          content: data.content ?? '',
          tokens: data.tokens ?? 0,
          createdAt: new Date(),
        };
        conv.push(row);
        return row;
      },
      findFirst: async ({
        where,
      }: {
        where: { id?: string; sessionId?: string };
      }) => {
        return (
          conv.find(
            (r) =>
              (!where.id || r.id === where.id) &&
              (!where.sessionId || r.sessionId === where.sessionId),
          ) ?? null
        );
      },
      findMany: async ({
        where,
        orderBy,
        take,
      }: {
        where?: { sessionId?: string };
        orderBy?: { createdAt?: 'desc' | 'asc' };
        take?: number;
      }) => {
        let out = where?.sessionId
          ? conv.filter((r) => r.sessionId === where.sessionId)
          : [...conv];
        if (orderBy?.createdAt === 'desc') {
          out.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
        }
        if (take !== undefined) out = out.slice(0, take);
        return out;
      },
      deleteMany: async ({
        where,
      }: {
        where: { id?: string; sessionId?: string };
      }) => {
        const before = conv.length;
        for (let i = conv.length - 1; i >= 0; i--) {
          const r = conv[i]!;
          let match = true;
          if (where.id !== undefined) match = match && r.id === where.id;
          if (where.sessionId !== undefined)
            match = match && r.sessionId === where.sessionId;
          if (match) conv.splice(i, 1);
        }
        return { count: before - conv.length };
      },
    },
    projectMemory: kvTable(project, 'projectId'),
    founderMemory: kvTable(founder, 'userId'),
    workspaceMemory: kvTable(workspace, 'workspaceId'),
    // Exposed for assertions.
    _conv: conv,
    _project: project,
    _founder: founder,
    _workspace: workspace,
  };
}

describe('MemoryEngineService — restart / recovery', () => {
  it('project state survives a service restart (same backing store)', async () => {
    const prisma = makeStubPrisma();
    const svc1 = new MemoryEngineService(prisma as never);
    await svc1.set('project', 'proj-1', 'lastBranch', 'main', {
      source: 'git',
      confidence: 0.9,
    });
    // Simulate restart: a new service instance, same Prisma store.
    const svc2 = new MemoryEngineService(prisma as never);
    const recovered = await svc2.get('project', 'proj-1', 'lastBranch');
    assert.ok(recovered, 'project state must be recovered after restart');
    assert.equal(recovered!.value, 'main');
    assert.equal(recovered!.store, 'project');
  });

  it('founder preferences survive a restart', async () => {
    const prisma = makeStubPrisma();
    const svc1 = new MemoryEngineService(prisma as never);
    await svc1.set('founder', 'user-1', 'theme', 'dark', { source: 'ui' });
    const svc2 = new MemoryEngineService(prisma as never);
    const recovered = await svc2.get('founder', 'user-1', 'theme');
    assert.ok(recovered);
    assert.equal(recovered!.value, 'dark');
  });

  it('workspace ephemeral state honours expiresAt', async () => {
    const prisma = makeStubPrisma();
    const svc = new MemoryEngineService(prisma as never);
    // Already-expired entry.
    await svc.set('workspace', 'ws-1', 'temp', 'gone', {
      expiresAt: new Date(Date.now() - 1000),
    });
    const got = await svc.get('workspace', 'ws-1', 'temp');
    assert.equal(got, null, 'expired workspace entry must read as null');
  });

  it('non-expired workspace state survives a restart', async () => {
    const prisma = makeStubPrisma();
    const svc1 = new MemoryEngineService(prisma as never);
    await svc1.set('workspace', 'ws-1', 'draft', 'hello', {
      expiresAt: new Date(Date.now() + 60_000),
    });
    const svc2 = new MemoryEngineService(prisma as never);
    const recovered = await svc2.get('workspace', 'ws-1', 'draft');
    assert.ok(recovered);
    assert.equal(recovered!.value, 'hello');
  });

  it('conversation history is append-only and survives a restart', async () => {
    const prisma = makeStubPrisma();
    const svc1 = new MemoryEngineService(prisma as never);
    await svc1.set('conversation', 'sess-1', 'user', 'hi', {
      role: 'user',
      tokens: 2,
    });
    await svc1.set('conversation', 'sess-1', 'assistant', 'hello!', {
      role: 'assistant',
      tokens: 3,
    });
    const svc2 = new MemoryEngineService(prisma as never);
    const history = await svc2.query({
      store: 'conversation',
      scope: 'sess-1',
      limit: 10,
    });
    assert.equal(history.length, 2);
    // The service returns rows sorted desc by createdAt then reversed; when
    // timestamps tie (same millisecond) the order is unstable, so verify
    // contents as a set.
    const contents = history
      .map((h) => (h.value as { content: string }).content)
      .sort();
    assert.deepEqual(contents, ['hello!', 'hi']);
  });

  it('forget() removes a key and the deletion survives a restart', async () => {
    const prisma = makeStubPrisma();
    const svc1 = new MemoryEngineService(prisma as never);
    await svc1.set('project', 'p', 'temp', 'x');
    await svc1.forget('project', 'p', 'temp');
    const svc2 = new MemoryEngineService(prisma as never);
    const got = await svc2.get('project', 'p', 'temp');
    assert.equal(got, null);
  });

  it('query() with no store fans out across project + founder + workspace', async () => {
    const prisma = makeStubPrisma();
    const svc = new MemoryEngineService(prisma as never);
    await svc.set('project', 'scope-1', 'k1', 1);
    await svc.set('founder', 'scope-1', 'k2', 2);
    await svc.set('workspace', 'scope-1', 'k3', 3);
    const all = await svc.query({ scope: 'scope-1' });
    assert.equal(all.length, 3);
  });

  it('error path returns null instead of throwing (best-effort contract)', async () => {
    const prisma = makeStubPrisma();
    const svc = new MemoryEngineService(prisma as never);
    // Force findUnique to throw by mutating the stub.
    prisma.projectMemory.findUnique = async () => {
      throw new Error('simulated DB outage');
    };
    const got = await svc.get('project', 'p', 'k');
    assert.equal(got, null);
  });
});
