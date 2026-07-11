import { Injectable, Logger } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import type { MemoryEntry, MemoryQuery, MemoryStore } from './memory-engine.types';

/**
 * Optional metadata passed alongside a `set`. Different stores consume
 * different fields:
 *   - conversation: userId, role (defaults to `key`), tokens
 *   - project:      source, confidence
 *   - founder:      source
 *   - workspace:    expiresAt (ephemeral rows)
 */
export type MemoryMetadata = {
  userId?: string;
  role?: string;
  tokens?: number;
  source?: string;
  confidence?: number;
  expiresAt?: Date;
};

/**
 * Memory Engine — kernel service #3 (docs/KERNEL.md §3).
 *
 * Four memory stores, each backed by its own Prisma table:
 *   - conversation: per-session chat history (ConversationMemory)
 *   - project:      per-repo operational intelligence (ProjectMemory)
 *   - founder:      per-user cross-project preferences (FounderMemory)
 *   - workspace:    ephemeral current state (WorkspaceMemory)
 *
 * The store argument routes get/set/query/forget to the right table. `scope`
 * is the per-store partition key: sessionId / projectId / userId / workspaceId.
 * Every method is best-effort and logs at debug level so a memory-store hiccup
 * never breaks an AI call (the context builder treats empty results as "no
 * memory yet").
 */
@Injectable()
export class MemoryEngineService {
  private readonly logger = new Logger(MemoryEngineService.name);

  constructor(private readonly prisma: PrismaService) {}

  async get(
    store: MemoryStore,
    scope: string,
    key: string,
  ): Promise<MemoryEntry | null> {
    try {
      switch (store) {
        case 'conversation': {
          // `key` is the row id (cuid) for the conversation store.
          const row = await this.prisma.conversationMemory.findFirst({
            where: { id: key, sessionId: scope },
          });
          return row ? this.convToEntry(row) : null;
        }
        case 'project': {
          const row = await this.prisma.projectMemory.findUnique({
            where: { projectId_key: { projectId: scope, key } },
          });
          return row ? this.kvToEntry('project', scope, row) : null;
        }
        case 'founder': {
          const row = await this.prisma.founderMemory.findUnique({
            where: { userId_key: { userId: scope, key } },
          });
          return row ? this.kvToEntry('founder', scope, row) : null;
        }
        case 'workspace': {
          const row = await this.prisma.workspaceMemory.findUnique({
            where: { workspaceId_key: { workspaceId: scope, key } },
          });
          if (!row) return null;
          if (row.expiresAt && row.expiresAt.getTime() < Date.now()) {
            // Lazy sweep: expired workspace rows are deleted on read.
            await this.prisma.workspaceMemory.delete({ where: { id: row.id } }).catch(() => {});
            return null;
          }
          return this.kvToEntry('workspace', scope, row);
        }
      }
    } catch (err) {
      this.logger.debug(
        `MemoryEngine.get store=${store} scope=${scope} key=${key} failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }

  async set(
    store: MemoryStore,
    scope: string,
    key: string,
    value: unknown,
    metadata?: MemoryMetadata,
  ): Promise<void> {
    try {
      switch (store) {
        case 'conversation': {
          // Append a chat turn. `key` is used as the role when metadata.role
          // is not supplied (callers like the AI Gateway pass role as key).
          await this.prisma.conversationMemory.create({
            data: {
              sessionId: scope,
              userId: metadata?.userId ?? null,
              role: metadata?.role ?? key ?? 'user',
              content: typeof value === 'string' ? value : JSON.stringify(value),
              tokens: metadata?.tokens ?? 0,
            },
          });
          return;
        }
        case 'project': {
          await this.prisma.projectMemory.upsert({
            where: { projectId_key: { projectId: scope, key } },
            create: {
              projectId: scope,
              key,
              value: this.toJson(value),
              source: metadata?.source ?? 'system',
              confidence: metadata?.confidence ?? 0.5,
            },
            update: {
              value: this.toJson(value),
              source: metadata?.source ?? 'system',
              confidence: metadata?.confidence ?? 0.5,
            },
          });
          return;
        }
        case 'founder': {
          await this.prisma.founderMemory.upsert({
            where: { userId_key: { userId: scope, key } },
            create: {
              userId: scope,
              key,
              value: this.toJson(value),
              source: metadata?.source ?? 'system',
            },
            update: {
              value: this.toJson(value),
              source: metadata?.source ?? 'system',
            },
          });
          return;
        }
        case 'workspace': {
          await this.prisma.workspaceMemory.upsert({
            where: { workspaceId_key: { workspaceId: scope, key } },
            create: {
              workspaceId: scope,
              key,
              value: this.toJson(value),
              expiresAt: metadata?.expiresAt ?? null,
            },
            update: {
              value: this.toJson(value),
              expiresAt: metadata?.expiresAt ?? null,
            },
          });
          return;
        }
      }
    } catch (err) {
      this.logger.debug(
        `MemoryEngine.set store=${store} scope=${scope} key=${key} failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  async query(query: MemoryQuery): Promise<MemoryEntry[]> {
    const store = query.store;
    const scope = query.scope;
    const limit = Math.max(1, Math.min(query.limit ?? 50, 200));
    try {
      if (!store) {
        // No store specified: fan out across the three KV stores (conversation
        // is append-only chat history and doesn't make sense to mix in here).
        const [project, founder, workspace] = await Promise.all([
          scope ? this.queryKv('project', scope, query.keyPrefix, limit) : [],
          scope ? this.queryKv('founder', scope, query.keyPrefix, limit) : [],
          scope ? this.queryKv('workspace', scope, query.keyPrefix, limit) : [],
        ]);
        return [...project, ...founder, ...workspace];
      }
      if (store === 'conversation') {
        const rows = await this.prisma.conversationMemory.findMany({
          where: scope ? { sessionId: scope } : undefined,
          orderBy: { createdAt: 'desc' },
          take: limit,
        });
        return rows.map((r) => this.convToEntry(r)).reverse();
      }
      if (!scope) return [];
      return this.queryKv(store, scope, query.keyPrefix, limit);
    } catch (err) {
      this.logger.debug(
        `MemoryEngine.query query=${JSON.stringify(query)} failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return [];
    }
  }

  async forget(
    store: MemoryStore,
    scope: string,
    key: string,
  ): Promise<void> {
    try {
      switch (store) {
        case 'conversation': {
          // `key` is the row id; if omitted/empty, clear the whole session.
          if (key) {
            await this.prisma.conversationMemory.deleteMany({
              where: { id: key, sessionId: scope },
            });
          } else {
            await this.prisma.conversationMemory.deleteMany({
              where: { sessionId: scope },
            });
          }
          return;
        }
        case 'project': {
          await this.prisma.projectMemory.deleteMany({
            where: { projectId: scope, key },
          });
          return;
        }
        case 'founder': {
          await this.prisma.founderMemory.deleteMany({
            where: { userId: scope, key },
          });
          return;
        }
        case 'workspace': {
          await this.prisma.workspaceMemory.deleteMany({
            where: { workspaceId: scope, key },
          });
          return;
        }
      }
    } catch (err) {
      this.logger.debug(
        `MemoryEngine.forget store=${store} scope=${scope} key=${key} failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────

  private async queryKv(
    store: 'project' | 'founder' | 'workspace',
    scope: string,
    keyPrefix: string | undefined,
    limit: number,
  ): Promise<MemoryEntry[]> {
    const where: Record<string, unknown> = {};
    if (store === 'project') where.projectId = scope;
    if (store === 'founder') where.userId = scope;
    if (store === 'workspace') where.workspaceId = scope;
    if (keyPrefix) where.key = { startsWith: keyPrefix };

    if (store === 'project') {
      const rows = await this.prisma.projectMemory.findMany({
        where: where as Prisma.ProjectMemoryWhereInput,
        orderBy: { updatedAt: 'desc' },
        take: limit,
      });
      return rows.map((r) => this.kvToEntry('project', scope, r));
    }
    if (store === 'founder') {
      const rows = await this.prisma.founderMemory.findMany({
        where: where as Prisma.FounderMemoryWhereInput,
        orderBy: { updatedAt: 'desc' },
        take: limit,
      });
      return rows.map((r) => this.kvToEntry('founder', scope, r));
    }
    const rows = await this.prisma.workspaceMemory.findMany({
      where: where as Prisma.WorkspaceMemoryWhereInput,
      orderBy: { updatedAt: 'desc' },
      take: limit,
    });
    // Drop expired rows from the result set (lazy sweep).
    const now = Date.now();
    return rows
      .filter((r) => !r.expiresAt || r.expiresAt.getTime() >= now)
      .map((r) => this.kvToEntry('workspace', scope, r));
  }

  private convToEntry(row: {
    id: string;
    sessionId: string;
    role: string;
    content: string;
    tokens: number;
    createdAt: Date;
  }): MemoryEntry {
    return {
      store: 'conversation',
      scope: row.sessionId,
      key: row.id,
      value: { role: row.role, content: row.content, tokens: row.tokens },
      createdAt: row.createdAt,
      updatedAt: row.createdAt,
    };
  }

  private kvToEntry(
    store: 'project' | 'founder' | 'workspace',
    scope: string,
    row: {
      id: string;
      key: string;
      value: Prisma.JsonValue;
      createdAt: Date;
      updatedAt: Date;
    },
  ): MemoryEntry {
    return {
      store,
      scope,
      key: row.key,
      value: row.value,
      createdAt: row.createdAt,
      updatedAt: row.updatedAt,
    };
  }

  private toJson(value: unknown): Prisma.InputJsonValue {
    return value as unknown as Prisma.InputJsonValue;
  }
}
