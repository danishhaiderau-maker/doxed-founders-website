/**
 * Install TradingAgentInstance write hooks on PrismaService so pause/arm
 * invalidation lives **outside** bitfinex-policy hash-locked files
 * (HM 9754e839380f / RK 22:14).
 *
 * Covers the four named pause/arm writers (all persist via
 * tradingAgentInstance.update):
 * - setInstancePaused
 * - failClosedStuckLiveInstances
 * - pauseRelayForSustainedFidelityBreach
 * - pauseRelayForPositionMismatch
 */
import {
  invalidateRelayStatusCache,
  invalidateRelayStatusCacheForUser,
  tradingAgentInstanceWriteTouchesArmOrPause,
} from './relay-status-cache';

type AnyFn = (...args: never[]) => Promise<unknown>;

export function installRelayStatusCachePrismaHooks(prisma: {
  tradingAgentInstance: { update: AnyFn; updateMany: AnyFn };
}): void {
  const delegate = prisma.tradingAgentInstance as {
    update: AnyFn;
    updateMany: AnyFn;
  };
  const originalUpdate = delegate.update.bind(delegate);
  const originalUpdateMany = delegate.updateMany.bind(delegate);

  delegate.update = (async (...args: never[]) => {
    const result = await originalUpdate(...args);
    maybeInvalidate(args[0], result);
    return result;
  }) as AnyFn;

  delegate.updateMany = (async (...args: never[]) => {
    const result = await originalUpdateMany(...args);
    maybeInvalidate(args[0], null);
    return result;
  }) as AnyFn;
}

function maybeInvalidate(args: unknown, result: unknown): void {
  const a = args && typeof args === 'object' ? (args as Record<string, unknown>) : {};
  if (!tradingAgentInstanceWriteTouchesArmOrPause(a.data)) return;
  const userIds = new Set<string>();
  const where = a.where && typeof a.where === 'object' ? (a.where as Record<string, unknown>) : {};
  if (typeof where.userId === 'string') userIds.add(where.userId);
  const compound = where.agentId_userId;
  if (compound && typeof compound === 'object') {
    const uid = (compound as Record<string, unknown>).userId;
    if (typeof uid === 'string') userIds.add(uid);
  }
  if (result && typeof result === 'object') {
    const uid = (result as Record<string, unknown>).userId;
    if (typeof uid === 'string') userIds.add(uid);
  }
  if (userIds.size === 0) {
    // Fail closed: unknown user scope → drop entire table.
    invalidateRelayStatusCache();
    return;
  }
  for (const uid of userIds) invalidateRelayStatusCacheForUser(uid);
}
