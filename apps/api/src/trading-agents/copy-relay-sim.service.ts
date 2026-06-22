import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import {
  COPY_RELAY_SIM_DEFAULT_BALANCE_USD,
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  emptyCopyRelaySimState,
  readCopyRelaySimState,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
} from '@dcf/utils';
import { SignalCycleStatus, TradingAgentInstanceStatus, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { BitfinexTradingClient } from '../exchanges/bitfinex-api.client';
import { BitfinexSimTradingClient } from '../exchanges/bitfinex-sim-trading.client';
import { BotBridgeService } from './bot-bridge.service';
import { mapBotStateToAgentStats, type BotApiState } from './bot-state.mapper';
import { mapSubscriberExchangeLiveBook } from './subscriber-exchange-live.mapper';
import { foldParticipantExecutionMeta } from './participant-execution-meta.util';
import { applyDashboardPatch } from './instance-view.mapper';
import { resolveShowcaseTradeDetails } from './relay-fidelity.mapper';

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

  async resetRelaySim(userId: string, slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance || instance.exchangeProvider !== 'bitfinex') {
      throw new BadRequestException('Connect Bitfinex (hire live copy) before resetting relay simulation.');
    }

    const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
    const prev = readCopyRelaySimState(dash);
    const sim: CopyRelaySimState = {
      ...emptyCopyRelaySimState(COPY_RELAY_SIM_DEFAULT_BALANCE_USD),
      active: prev.active,
      startedAt: new Date().toISOString(),
      stoppedAt: prev.stoppedAt,
      sessionPnlUsd: 0,
      showcasePnlUsd: 0,
      showcaseTradeCount: 0,
    };

    const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchState().catch(() => null) : null;
    if (bot && typeof bot === 'object') {
      const stats = mapBotStateToAgentStats(bot as BotApiState);
      sim.showcasePnlUsd = stats.sessionPnlUsd;
      sim.showcaseTradeCount = stats.tradeCount;
    }

    await this.prisma.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        dashboardState: applyDashboardPatch(dash, {
          copyRelaySim: sim,
          copyRelayReconcile: null,
        }) as unknown as Prisma.InputJsonValue,
        lastError: prev.active
          ? 'Relay sim refreshed at $500 — paper ledger cleared for this session.'
          : instance.lastError,
      },
    });

    this.dropSimClient(userId);
    this.logger.log(`Copy relay sim reset for ${userId}`);
    return { ok: true, copyRelaySim: sim };
  }

  async exportRelaySimAuditCsv(userId: string, slug: string): Promise<string> {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');

    const instance = await this.prisma.tradingAgentInstance.findUnique({
      where: { agentId_userId: { agentId: agent.id, userId } },
    });
    if (!instance) throw new NotFoundException('No instance');

    const sim = readCopyRelaySimState(instance.dashboardState);
    const startedAt = sim.startedAt ? new Date(sim.startedAt) : new Date(0);

    const participants = await this.prisma.signalCycleParticipant.findMany({
      where: {
        userId,
        cycle: { agentId: agent.id },
        createdAt: { gte: startedAt },
      },
      include: {
        cycle: { select: { tradeId: true, showcaseExitReason: true } },
        events: { orderBy: { createdAt: 'asc' } },
      },
      orderBy: { updatedAt: 'asc' },
      take: 500,
    });

    const esc = (v: unknown) => {
      const s = v == null ? '' : String(v);
      return s.includes(',') || s.includes('"') ? `"${s.replace(/"/g, '""')}"` : s;
    };

    const bot = this.botBridge.isEnabled() ? await this.botBridge.fetchStateForExecution(true).catch(() => null) : null;

    const lines = [
      'closed_at_melbourne,trade_id,local_bot_trade_id,match_kind,status,direction,local_bot_entry,relay_entry,entry_lag_sec,local_bot_exit,relay_exit,exit_lag_sec,local_bot_entry_at_melbourne,local_bot_exit_at_melbourne,relay_entry_at_melbourne,relay_exit_at_melbourne,relay_pnl_usd,relay_pnl_pct,exit_reason,created_at_melbourne',
    ];

    for (const p of participants) {
      const fill = p.events.find((e) => e.eventType === 'FILLED');
      const exit = p.events.find((e) => e.eventType === 'EXIT');
      const fillPayload =
        fill?.payload && typeof fill.payload === 'object'
          ? (fill.payload as Record<string, unknown>)
          : {};
      const exitPayload =
        exit?.payload && typeof exit.payload === 'object'
          ? (exit.payload as Record<string, unknown>)
          : {};
      const showcase = resolveShowcaseTradeDetails(bot, p.cycle.tradeId);
      const relayEntryAt = fill?.createdAt ?? null;
      const relayExitAt = exit?.createdAt ?? null;
      const entryLag =
        showcase?.entryAt && relayEntryAt
          ? Math.round((relayEntryAt.getTime() - Date.parse(showcase.entryAt)) / 1000)
          : '';
      const exitLag =
        showcase?.exitAt && relayExitAt
          ? Math.round((relayExitAt.getTime() - Date.parse(showcase.exitAt)) / 1000)
          : '';
      lines.push(
        [
          esc(p.updatedAt.toISOString()),
          esc(p.cycle.tradeId),
          esc(showcase?.matchedTradeId ?? ''),
          esc(showcase?.matchKind ?? 'none'),
          esc(p.status),
          esc(fillPayload.direction ?? ''),
          esc(showcase?.entry ?? ''),
          esc(p.fillPrice != null ? Number(p.fillPrice) : fillPayload.fill_price ?? ''),
          esc(entryLag),
          esc(showcase?.exit ?? ''),
          esc(p.exitPrice != null ? Number(p.exitPrice) : exitPayload.exit_price ?? ''),
          esc(exitLag),
          esc(showcase?.entryAt ?? ''),
          esc(showcase?.exitAt ?? ''),
          esc(relayEntryAt?.toISOString() ?? ''),
          esc(relayExitAt?.toISOString() ?? ''),
          esc(p.pnlUsd != null ? Number(p.pnlUsd) : ''),
          esc(p.pnlMarginPct != null ? Number(p.pnlMarginPct) : ''),
          esc(p.cycle.showcaseExitReason ?? exitPayload.exit_reason ?? ''),
          esc(p.createdAt.toISOString()),
        ].join(','),
      );
    }

    return `${lines.join('\n')}\n`;
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
        cycle: { agentId },
        createdAt: { gte: startedAt },
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
        dashboardState: applyDashboardPatch(dash, {
          copyRelaySim: {
            ...readCopyRelaySimState(dash),
            ...sim,
            ledger,
            reconcile,
            sessionPnlUsd,
            showcasePnlUsd,
          },
          copyRelayReconcile: reconcile,
        }) as unknown as Prisma.InputJsonValue,
      },
    });
  }
}
