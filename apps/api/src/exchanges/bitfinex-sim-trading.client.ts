import {
  BITFINEX_BTC_PERP_SYMBOL,
  BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE,
  type BitfinexActiveOrder,
  type BitfinexPositionDetail,
  BitfinexTradingClient,
  type EnsureDerivativesResult,
} from './bitfinex-api.client';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import type { CopyRelaySimLedger, CopyRelaySimOrder } from '@dcf/utils';

const MIN_POSITION_BTC = 0.00004;
const SIM_FEE_BPS = 4;

function orderAmount(direction: 'LONG' | 'SHORT', qty: number): number {
  return direction === 'LONG' ? Math.abs(qty) : -Math.abs(qty);
}

function closeAmount(positionDirection: 'LONG' | 'SHORT', qty: number): number {
  return positionDirection === 'LONG' ? -Math.abs(qty) : Math.abs(qty);
}

function weightedAvgEntry(
  currentAmt: number,
  currentBase: number,
  deltaAmt: number,
  fillPrice: number,
): number {
  const newAmt = currentAmt + deltaAmt;
  if (Math.abs(newAmt) < MIN_POSITION_BTC) return fillPrice;
  if (Math.abs(currentAmt) < MIN_POSITION_BTC) return fillPrice;
  const currentNotional = Math.abs(currentAmt) * currentBase;
  const deltaNotional = Math.abs(deltaAmt) * fillPrice;
  return (currentNotional + deltaNotional) / (Math.abs(currentAmt) + Math.abs(deltaAmt));
}

function unrealizedPnlUsd(position: CopyRelaySimLedger['position'], mark: number): number {
  if (!position || Math.abs(position.amount) < MIN_POSITION_BTC) return 0;
  const qty = Math.abs(position.amount);
  return position.amount > 0
    ? (mark - position.basePrice) * qty
    : (position.basePrice - mark) * qty;
}

/**
 * Paper Bitfinex book — real public mark prices, simulated orders/fills/position.
 * Mirrors merged BTC-PERP behavior for Option B virtual-lot relay testing.
 */
export class BitfinexSimTradingClient {
  private ledger: CopyRelaySimLedger;
  private readonly liveMark: BitfinexTradingClient;

  constructor(ledger: CopyRelaySimLedger, liveMark: BitfinexTradingClient) {
    this.ledger = { ...ledger, orders: [...ledger.orders] };
    this.liveMark = liveMark;
  }

  getLedger(): CopyRelaySimLedger {
    return {
      ...this.ledger,
      orders: [...this.ledger.orders],
      position: this.ledger.position ? { ...this.ledger.position } : null,
    };
  }

  async getMarkPrice(symbol = BITFINEX_BTC_PERP_SYMBOL): Promise<number> {
    return this.liveMark.getMarkPrice(symbol);
  }

  async validateCredentials(_creds: ExchangeCredentials) {
    return { ok: true, message: 'Bitfinex sim relay (paper book)' };
  }

  async getDerivativesAvailableUsd(_creds: ExchangeCredentials): Promise<number> {
    const mark = await this.getMarkPrice().catch(() => 0);
    const locked = this.ledger.position
      ? (Math.abs(this.ledger.position.amount) * mark) / BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE
      : 0;
    return Math.max(0, this.ledger.derivativesUsd - locked);
  }

  async ensureDerivativesMargin(
    _creds: ExchangeCredentials,
    minUsd: number,
  ): Promise<EnsureDerivativesResult> {
    const available = this.ledger.derivativesUsd;
    return {
      derivativesUsd: available,
      transferredUsd: 0,
      message:
        available < minUsd * 0.9
          ? `Sim relay needs ~$${minUsd.toFixed(0)} paper margin (have $${available.toFixed(2)}).`
          : undefined,
    };
  }

  async listActiveOrders(
    _creds: ExchangeCredentials,
    _symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<BitfinexActiveOrder[]> {
    return this.ledger.orders
      .filter((o) => o.orderType === 'LIMIT')
      .map((o) => ({
        id: o.id,
        symbol: o.symbol,
        amount: o.amount,
        amountOrig: o.amount,
        price: o.price,
        status: 'ACTIVE',
        orderType: 'LIMIT',
      }));
  }

  async findOrder(
    _creds: ExchangeCredentials,
    orderId: number,
  ): Promise<BitfinexActiveOrder | null> {
    const o = this.ledger.orders.find((row) => row.id === orderId);
    if (!o) return null;
    return {
      id: o.id,
      symbol: o.symbol,
      amount: o.amount,
      amountOrig: o.amount,
      price: o.price,
      status: 'ACTIVE',
      orderType: o.orderType,
    };
  }

  async getOpenPositionDetail(
    _creds: ExchangeCredentials,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<BitfinexPositionDetail | null> {
    const p = this.ledger.position;
    if (!p || Math.abs(p.amount) < MIN_POSITION_BTC || p.symbol !== symbol) return null;
    const mark = await this.getMarkPrice(symbol).catch(() => p.basePrice);
    const pnlUsd = unrealizedPnlUsd(p, mark);
    const notional = Math.abs(p.amount) * p.basePrice;
    const pnlPct = notional > 0 ? (pnlUsd / notional) * 100 * BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE : 0;
    return {
      symbol,
      amount: p.amount,
      basePrice: p.basePrice,
      pnlUsd,
      pnlPct,
      direction: p.amount > 0 ? 'LONG' : 'SHORT',
    };
  }

  /** Paper book has no exchange ledger — realized P&L attribution falls back to reconstruction. */
  async getRealizedPnlSince(
    _creds: ExchangeCredentials,
    _sinceMs: number,
  ): Promise<number> {
    return 0;
  }

  async submitLimitOrder(
    _creds: ExchangeCredentials,
    input: {
      symbol?: string;
      direction: 'LONG' | 'SHORT';
      qty: number;
      price: number;
      leverage?: number;
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    // Bitfinex BTC-PERP: same-direction limits collapse to one working order on the book.
    this.ledger.orders = this.ledger.orders.filter(
      (o) => !(o.orderType === 'LIMIT' && o.direction === input.direction),
    );
    const id = this.ledger.nextOrderId++;
    const amount = orderAmount(input.direction, input.qty);
    const order: CopyRelaySimOrder = {
      id,
      symbol,
      direction: input.direction,
      qty: input.qty,
      price: input.price,
      amount,
      orderType: 'LIMIT',
      createdAtMs: Date.now(),
    };
    this.ledger.orders.push(order);
    const mark = await this.getMarkPrice(symbol);
    await this.processFillsOnMark(mark);
    return id;
  }

  async submitStopOrder(
    _creds: ExchangeCredentials,
    input: {
      symbol?: string;
      positionDirection: 'LONG' | 'SHORT';
      qty: number;
      stopPrice: number;
      leverage?: number;
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const id = this.ledger.nextOrderId++;
    const amount = closeAmount(input.positionDirection, input.qty);
    const order: CopyRelaySimOrder = {
      id,
      symbol,
      direction: input.positionDirection,
      qty: input.qty,
      price: input.stopPrice,
      amount,
      orderType: 'STOP',
      createdAtMs: Date.now(),
    };
    this.ledger.orders.push(order);
    return id;
  }

  async submitMarketClose(
    _creds: ExchangeCredentials,
    input: {
      symbol?: string;
      positionDirection: 'LONG' | 'SHORT';
      qty: number;
      leverage?: number;
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const mark = await this.getMarkPrice(symbol);
    const closeQty = Math.min(input.qty, Math.abs(this.ledger.position?.amount ?? input.qty));
    if (closeQty < MIN_POSITION_BTC) return this.ledger.nextOrderId++;
    this.applyClose(input.positionDirection, closeQty, mark);
    return this.ledger.nextOrderId++;
  }

  async cancelOrder(_creds: ExchangeCredentials, orderId: number): Promise<void> {
    this.ledger.orders = this.ledger.orders.filter((o) => o.id !== orderId);
  }

  async cancelOrphanStopOrders(_creds: ExchangeCredentials, keepOrderId?: number): Promise<number> {
    let n = 0;
    this.ledger.orders = this.ledger.orders.filter((o) => {
      if (o.orderType !== 'STOP') return true;
      if (keepOrderId != null && o.id === keepOrderId) return true;
      n += 1;
      return false;
    });
    return n;
  }

  /** Advance paper book against live mark — call every relay tick. */
  async processFillsOnMark(mark?: number): Promise<void> {
    const px = mark ?? (await this.getMarkPrice());
    const remaining: CopyRelaySimOrder[] = [];

    for (const order of this.ledger.orders) {
      if (order.orderType === 'LIMIT') {
        const isEntry = order.amount < 0 ? order.direction === 'SHORT' : order.direction === 'LONG';
        const entryFill =
          isEntry &&
          ((order.direction === 'SHORT' && px >= order.price) ||
            (order.direction === 'LONG' && px <= order.price));
        if (entryFill) {
          this.applyFill(order.amount, order.qty, order.price);
          continue;
        }
        remaining.push(order);
        continue;
      }

      if (order.orderType === 'STOP') {
        const stopHit =
          order.direction === 'SHORT'
            ? px >= order.price
            : px <= order.price;
        if (stopHit) {
          this.applyClose(order.direction, order.qty, px);
          continue;
        }
        remaining.push(order);
      }
    }

    this.ledger.orders = remaining;
  }

  private applyFill(deltaAmt: number, qty: number, fillPrice: number) {
    const fee = (qty * fillPrice * SIM_FEE_BPS) / 10_000;
    this.ledger.feesUsd += fee;
    this.ledger.derivativesUsd -= fee;

    const p = this.ledger.position;
    if (!p || Math.abs(p.amount) < MIN_POSITION_BTC) {
      this.ledger.position = {
        symbol: BITFINEX_BTC_PERP_SYMBOL,
        amount: deltaAmt,
        basePrice: fillPrice,
      };
      return;
    }

    const newAmt = p.amount + deltaAmt;
    if (Math.abs(newAmt) < MIN_POSITION_BTC) {
      const pnl =
        p.amount > 0
          ? (fillPrice - p.basePrice) * Math.abs(deltaAmt)
          : (p.basePrice - fillPrice) * Math.abs(deltaAmt);
      this.ledger.realizedPnlUsd += pnl;
      this.ledger.derivativesUsd += pnl;
      this.ledger.position = null;
      return;
    }

    this.ledger.position = {
      symbol: p.symbol,
      amount: newAmt,
      basePrice: weightedAvgEntry(p.amount, p.basePrice, deltaAmt, fillPrice),
    };
  }

  private applyClose(positionDirection: 'LONG' | 'SHORT', qty: number, mark: number) {
    const p = this.ledger.position;
    if (!p || Math.abs(p.amount) < MIN_POSITION_BTC) return;

    const closeQty = Math.min(qty, Math.abs(p.amount));
    const fee = (closeQty * mark * SIM_FEE_BPS) / 10_000;
    this.ledger.feesUsd += fee;
    this.ledger.derivativesUsd -= fee;

    const pnl =
      positionDirection === 'LONG'
        ? (mark - p.basePrice) * closeQty
        : (p.basePrice - mark) * closeQty;
    this.ledger.realizedPnlUsd += pnl;
    this.ledger.derivativesUsd += pnl;

    const signedClose = positionDirection === 'LONG' ? -closeQty : closeQty;
    const newAmt = p.amount + signedClose;
    if (Math.abs(newAmt) < MIN_POSITION_BTC) {
      this.ledger.position = null;
    } else {
      this.ledger.position = { ...p, amount: newAmt };
    }
  }

  sessionPnlUsd(mark: number): number {
    return (
      this.ledger.realizedPnlUsd +
      unrealizedPnlUsd(this.ledger.position, mark) -
      this.ledger.feesUsd
    );
  }
}
