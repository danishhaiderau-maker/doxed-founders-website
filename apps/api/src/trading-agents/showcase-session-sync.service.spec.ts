import assert from 'node:assert/strict';
import test from 'node:test';
import { ShowcaseSessionSyncService } from './showcase-session-sync.service';

test('epoch_change while Cheetah is ACTIVE preserves copy sessions (no USER_RELAY_STOP)', async () => {
  const calls: string[] = [];
  const prisma = {
    tradingAgent: {
      findUnique: async () => ({
        id: 'agent-1',
        dashboardState: {
          showcaseSessionEpoch: 'v15|1000|1111',
          showcaseSessionBotVersion: 'v15',
          showcaseSessionFreshResetTs: 1111,
        },
      }),
      update: async () => {
        calls.push('persist-epoch');
        return {};
      },
    },
    tradingAgentInstance: {
      findFirst: async () => ({ id: 'cheetah-active', status: 'ACTIVE' }),
    },
  };
  const instances = {
    resetAllUserCopySessions: async () => {
      calls.push('USER_RELAY_STOP');
      return { resetCount: 1 };
    },
  };
  const botBridge = {
    isEnabled: () => true,
    invalidateCache: () => calls.push('invalidate'),
  };
  const service = new ShowcaseSessionSyncService(
    prisma as never,
    botBridge as never,
    instances as never,
  );
  await service.syncFromBotState({
    bot_version: 'v16-new',
    bot_start_time: 2222,
    last_fresh_reset_ts: 3333,
  } as never);
  assert.equal(calls.includes('USER_RELAY_STOP'), false);
  assert.equal(calls.includes('persist-epoch'), true);
});

test('epoch_change while Cheetah is not ACTIVE still resets copy sessions', async () => {
  const calls: string[] = [];
  const prisma = {
    tradingAgent: {
      findUnique: async () => ({
        id: 'agent-1',
        dashboardState: {
          showcaseSessionEpoch: 'v15|1000|1111',
          showcaseSessionBotVersion: 'v15',
          showcaseSessionFreshResetTs: 1111,
        },
      }),
      update: async () => {
        calls.push('persist-epoch');
        return {};
      },
    },
    tradingAgentInstance: {
      findFirst: async () => null,
    },
    signalCycle: {
      updateMany: async () => {
        calls.push('expire-cycles');
        return { count: 0 };
      },
    },
  };
  const instances = {
    resetAllUserCopySessions: async () => {
      calls.push('USER_RELAY_STOP');
      return { resetCount: 1 };
    },
  };
  const botBridge = {
    isEnabled: () => true,
    invalidateCache: () => calls.push('invalidate'),
  };
  const service = new ShowcaseSessionSyncService(
    prisma as never,
    botBridge as never,
    instances as never,
  );
  await service.syncFromBotState({
    bot_version: 'v16-new',
    bot_start_time: 2222,
    last_fresh_reset_ts: 3333,
  } as never);
  assert.equal(calls.includes('USER_RELAY_STOP'), true);
  assert.equal(calls.includes('persist-epoch'), true);
});
