import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import {
  COPY_RELAY_SIM_DEFAULT_BALANCE_USD,
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  emptyCopyRelaySimState,
  readCopyRelaySimState,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
} from '@dcf/utils';
import { SignalCycleStatus, TradingAgentInstanceStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
import { BitfinexSimTradingClient } from '../exchanges/bitfinex-sim-trading.client';
import { BotBridgeService } from './bot-bridge.service';
import { mapBotStateToAgentStats, type BotApiState } from './bot-state.mapper';
import { mapSubscriberExchangeLiveBook } from './subscriber-exchange-live.mapper';
import { foldParticipantExecutionMeta } from './participant-execution-meta.util';

@Injectable()
export class CopyRelaySimService {
  private readonly logger = new Logger(CopyRelaySimService.name);
  private readonly bitfinex = new BitfinexTradingClient();
  private readonly simClients = new Map<string, BitfinexSimTradingClient>();

  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
  ) {}

  getSimClient(userId: string, ledger: CopyRelaySimState['ledger']): BitfinexSimTradingClient {
    const client = new BitfinexSimTradingClient(ledger, this.bitfinex);
    this.simClients.set(userId, client);
    return client;
  }

  dropSimClient(userId: string) {
    this.simClients.delete(userId);
  }

  async startRelaySim(userId: string, slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance || instance.exchangeProvider !== 'bitfinex') {
      throw new BadRequestException('Connect Bitfinex (hire live copy) before starting relay simulation.');
    }

    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const sim = emptyCopyRelaySimState(COPY_RELAY_SIM_DEFAULT_BALANCE_USD);
    sim.active = true;
    sim.startedAt = new Date().toISOString();
    sim.ledger = emptyCopyRelaySimState(COPY_RELAY_SIM_DEFAULT_BALANCE_USD).ledger;

    const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchState().catch(() => null) : null;
    if (bot && typeof bot === 'object') {
      const stats = mapBotStateToAgentStats(bot as BotApiState);
      sim.showcasePnlUsd = stats.sessionPnlUsd;
      sim.showcaseTradeCount = stats.tradeCount;
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        status: TradingAgentInstanceStatus.PAUSED,
        lastError: 'Relay simulation active — live orders blocked; paper book mirrors Option B relay.',
        dashboardState: {
          ...dash,
          copyRelaySim: sim,
          relaySimChannel: true,
        },
      },
    });

    this.dropSimClient(userId);
    this.logger.log(`Copy relay sim started for ${userId}`);
    return { ok: true, copyRelaySim: sim };
  }

  async stopRelaySim(userId: string, slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance) throw new NotFoundException('No instance');

    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const prev = readCopyRelaySimState(dash);
    const sim: CopyRelaySimState = {
      ...prev,
      active: false,
      stoppedAt: new Date().toISOString(),
    };

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: { ...dash, copyRelaySim: sim, relaySimChannel: false },
        lastError: 'Relay simulation stopped. Press Start to resume live copy when ready.',
      },
    });

    this.dropSimClient(userId);
    return { ok: true, copyRelaySim: sim };
  }

  async getRelaySimStatus(userId: string, slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance) throw new NotFoundException('No instance');

    const sim = readCopyRelaySimState(instance.dashboardState);
    const mark = await this.bitfinex.getMarkPrice().catch(() => null);
    const liveBook = sim.active
      ? await this.buildSimLiveBook(userId, agent.id, sim, mark)
      : null;

    return {
      copyRelaySim: sim,
      relaySimLiveBook: liveBook,
      markPrice: mark,
      instanceStatus: instance.status,
    };
  }

  async buildSimLiveBook(
    userId: string,
    agentId: string,
    sim: CopyRelaySimState,
    markPrice: number | null,
  ) {
    const client = this.getSimClient(userId, sim.ledger);
    const creds = { apiKey: 'sim', apiSecret: 'sim' };
    await client.processFillsOnMark(markPrice ?? undefined);
    const orders = await client.listActiveOrders(creds);
    const position = await client.getOpenPositionDetail(creds);
    const startedAt = sim.startedAt ? new Date(sim.startedAt) : new Date(0);

    const participants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        createdAt: { gte: startedAt },
        cycle: { agentId },
      },
      include: {
        cycle: {
          select: {
            tradeId: true,
            status: true,
            intentEnvelope: true,
            showcaseExitReason: true,
            createdAt: true,
          },
        },
        events: {
          orderBy: { createdAt: 'asc' },
          select: { eventType: true, payload: true },
        },
      },
      orderBy: { updatedAt: 'desc' },
      take: 80,
    });

    return mapSubscriberExchangeLiveBook({
      orders,
      position,
      markPrice: markPrice ?? undefined,
      participants: participants.map((p) => {
        const meta = foldParticipantExecutionMeta(p.events);
        return {
          status: p.status,
          fillPrice: p.fillPrice != null ? Number(p.fillPrice) : null,
          exitPrice: p.exitPrice != null ? Number(p.exitPrice) : null,
          pnlUsd: p.pnlUsd != null ? Number(p.pnlUsd) : null,
          pnlMarginPct: p.pnlMarginPct != null ? Number(p.pnlMarginPct) : null,
          limitPrice: meta.limitPrice,
          qty: meta.qty,
          stopLoss: meta.stopPrice,
          takeProfit: meta.profitLockFloor,
          updatedAt: p.updatedAt,
          createdAt: p.createdAt,
          cycle: p.cycle,
        };
      }),
    });
  }

  buildReconcileSnapshot(input: {
    exchangePositionQty: number;
    ledgerOpenQty: number;
    openLots: number;
    pendingLots: number;
    markPrice: number | null;
  }): CopyRelayReconcileSnapshot {
    const deltaBtc = input.exchangePositionQty - input.ledgerOpenQty;
    return {
      exchangePositionQty: input.exchangePositionQty,
      ledgerOpenQty: input.ledgerOpenQty,
      deltaBtc,
      alert: Math.abs(deltaBtc) > COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
      openLots: input.openLots,
      pendingLots: input.pendingLots,
      markPrice: input.markPrice,
      updatedAt: new Date().toISOString(),
    };
  }

  async persistSimState(
    instanceId: string,
    userId: string,
    sim: CopyRelaySimState,
    reconcile: CopyRelayReconcileSnapshot | null,
  ) {
    const instance = await this.prisma.tradingAgentInstance.findUnique({ where: { id: instanceId } });
    if (!instance) return;
    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const client = this.simClients.get(userId);
    const mark = await this.bitfinex.getMarkPrice().catch(() => 0);
    const ledger = client?.getLedger() ?? sim.ledger;
    const sessionPnlUsd = client ? client.sessionPnlUsd(mark) : sim.sessionPnlUsd;
    const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchState().catch(() => null) : null;
    const showcasePnlUsd =
      bot && typeof bot === 'object'
        ? mapBotStateToAgentStats(bot as BotApiState).sessionPnlUsd
        : sim.showcasePnlUsd;

    await this.prisma.tradingAgentInstance.update({
      where: { id: instanceId },
      data: {
        dashboardState: {
          ...dash,
          copyRelaySim: {
            ...sim,
            ledger,
            reconcile,
            sessionPnlUsd,
            showcasePnlUsd,
          },
          copyRelayReconcile: reconcile,
        },
      },
    });
  }
}
