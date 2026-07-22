import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { HttpException } from '@nestjs/common';
import { FounderPromoService } from './founder-promo.service';

type Row = {
  id: string;
  requestId: string;
  userId: string;
  status: 'RESERVED' | 'RECONCILED' | 'RELEASED' | 'UNCERTAIN';
  reservedWeightedUnits: number;
  actualWeightedUnits: number | null;
  expiresAt: Date;
  createdAt: Date;
};

function serviceHarness(initial: Row[] = []) {
  const rows = [...initial];
  const tx = {
    $executeRaw: async () => 1,
    platformSettings: {
      findUnique: async () => ({
        founderPromoAiEnabled: true,
        founderPromoTokenCap: 200_000,
        founderPromoWindowDays: 7,
      }),
    },
    founder: { findUnique: async () => null },
    user: {
      findUnique: async () => ({
        createdAt: new Date('2026-07-20T00:00:00.000Z'),
        xVerified: true,
      }),
    },
    aiTokenUsageLog: {
      aggregate: async () => ({ _sum: { promptTokens: null, completionTokens: null } }),
    },
    aiManagedReservation: {
      findUnique: async ({ where }: { where: { requestId: string } }) =>
        rows.find((row) => row.requestId === where.requestId) ?? null,
      updateMany: async () => ({ count: 0 }),
      findMany: async () => rows.filter((row) => row.status !== 'RELEASED'),
      create: async ({ data }: { data: Omit<Row, 'id' | 'status' | 'actualWeightedUnits' | 'createdAt'> }) => {
        const row: Row = {
          ...data,
          id: `reservation-${rows.length + 1}`,
          status: 'RESERVED',
          actualWeightedUnits: null,
          createdAt: new Date(),
        };
        rows.push(row);
        return row;
      },
    },
  };
  const prisma = {
    $transaction: async <T>(callback: (client: typeof tx) => Promise<T>) => callback(tx),
  };
  return {
    rows,
    service: new FounderPromoService(prisma as never, {} as never, {} as never),
  };
}

describe('FounderPromoService managed reservations', () => {
  it('creates one idempotent reservation before provider access', async () => {
    const { service, rows } = serviceHarness();
    const input = {
      userId: 'user-1',
      requestId: 'request-1',
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
      tier: 'code',
      estimatedInputTokens: 1_000,
      maxOutputTokens: 2_000,
    };
    const first = await service.reserveManagedUsage(input);
    const retry = await service.reserveManagedUsage(input);
    assert.equal(first.reservedWeightedUnits, 7_000);
    assert.equal(retry.id, first.id);
    assert.equal(rows.length, 1);
  });

  it('rejects before provider access when active usage cannot cover the request', async () => {
    const { service } = serviceHarness([
      {
        id: 'used-1',
        requestId: 'used-request',
        userId: 'user-1',
        status: 'RECONCILED',
        reservedWeightedUnits: 199_000,
        actualWeightedUnits: 199_000,
        expiresAt: new Date('2026-07-22T01:00:00.000Z'),
        createdAt: new Date('2026-07-22T00:00:00.000Z'),
      },
    ]);
    await assert.rejects(
      () =>
        service.reserveManagedUsage({
          userId: 'user-1',
          requestId: 'request-over-cap',
          provider: 'deepseek',
          model: 'deepseek-v4-pro',
          tier: 'code',
          estimatedInputTokens: 1_000,
          maxOutputTokens: 1,
        }),
      (error: unknown) =>
        error instanceof HttpException && error.getStatus() === 429,
    );
  });

  it('exposes DeepSeek as the only managed cloud brain', async () => {
    const prisma = {
      platformSettings: {
        findUnique: async () => ({
          founderPromoAiEnabled: true,
          founderPromoTokenCap: 200_000,
          founderPromoWindowDays: 7,
          founderPromoAiCredentialsEnc: 'encrypted',
          platformBrainDeepseekKeyEnc: null,
          updatedAt: new Date('2026-07-22T00:00:00.000Z'),
        }),
      },
    };
    const crypto = {
      decrypt: () => JSON.stringify({
        deepseek: 'deepseek-secret',
        glm: 'legacy-glm-secret',
        gemini: 'legacy-gemini-secret',
      }),
    };
    const service = new FounderPromoService(
      prisma as never,
      crypto as never,
      {} as never,
    );

    assert.equal(await service.resolvePromoApiKey('user-1', 'glm'), null);
    assert.equal(await service.hasPromoProvider('user-1', 'gemini'), false);
    assert.equal(await service.getDecryptedPlatformGlmKey(), null);
    assert.deepEqual(
      (await service.getAvailableBrains()).map((brain) => brain.key),
      ['DEEPSEEK', 'RULE_BASED'],
    );
  });
});
