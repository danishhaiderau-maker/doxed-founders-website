import type { TradingAgentDashboardState } from '@dcf/utils';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';

type LiveBook = TradingAgentDashboardState['liveBook'];

const isActualExchangePosition = (position: LiveBook['positions'][number]) =>
  position.leg === 'Exchange net (actual)' || position.leg === 'Bitfinex net';

/**
 * A Bitfinex BTC perpetual account has one merged net position. The relay may
 * retain several virtual lots for independent source exits, but those lots are
 * accounting records and must never be rendered as extra exchange positions.
 */
export function selectLiveExecutionBook(book: LiveBook): LiveBook {
  const seenOrderIds = new Set<string>();
  const seenTradeIds = new Set<string>();

  return {
    activeSignals: [],
    positions: book.positions.filter(isActualExchangePosition).slice(0, 1),
    pendingOrders: book.pendingOrders.filter((order) => {
      const id = order.tradeId?.trim();
      if (!id) return true;
      if (seenOrderIds.has(id)) return false;
      seenOrderIds.add(id);
      return true;
    }),
    expiredOrders: [],
    trades: book.trades.filter((trade) => {
      const id = trade.tradeId.trim();
      if (!id) return true;
      if (seenTradeIds.has(id)) return false;
      seenTradeIds.add(id);
      return true;
    }),
  };
}

/**
 * A hired live-copy customer should land on their exchange desk. A previously
 * stored showcase selection is deliberately ignored on a new page load so the
 * global paper bot cannot be mistaken for the customer's Bitfinex account.
 */
export function resolveInitialAgentDesk(input: {
  storedDesk: AgentDeskId | null;
  isLiveSession: boolean;
  relaySimActive: boolean;
}): AgentDeskId {
  if (input.relaySimActive) return 'relay-sim';
  if (input.isLiveSession) {
    return input.storedDesk === 'relay-sim' ? 'relay-sim' : 'live';
  }
  return input.storedDesk ?? 'showcase';
}

export function shouldShowShowcaseReference(activeDesk: AgentDeskId): boolean {
  return activeDesk === 'relay-sim';
}
