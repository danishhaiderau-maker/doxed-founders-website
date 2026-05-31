import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
  OnModuleInit,
} from '@nestjs/common';
import {
  PaperLimitOrderSide,
  PaperLimitOrderStatus,
  PaperLimitTrigger,
  PaperTradeSide,
  Prisma,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { PaperTradingService } from './paper-trading.service';

const POLL_MS = 60_000;

@Injectable()
export class PaperLimitOrderService implements OnModuleInit {
  private readonly logger = new Logger(PaperLimitOrderService.name);
  private processing = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly paperTrading: PaperTradingService,
  ) {}

  onModuleInit() {
    void this.processOpenOrders();
    setInterval(() => this.processOpenOrders(), POLL_MS);
  }

  async list(userId: string) {
    const orders = await this.prisma.paperLimitOrder.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: 50,
      include: { project: { select: { ticker: true, name: true } } },
    });
    return orders.map((o) => ({
      id: o.id,
      side: o.side,
      trigger: o.trigger,
      targetPriceUsd: Number(o.targetPriceUsd),
      amountUsd: o.amountUsd != null ? Number(o.amountUsd) : null,
      sellPercent: Number(o.sellPercent),
      status: o.status,
      ticker: o.project?.ticker ?? null,
      projectName: o.project?.name ?? null,
      dexscreenerUrl: o.dexscreenerUrl,
      filledAt: o.filledAt?.toISOString() ?? null,
      createdAt: o.createdAt.toISOString(),
    }));
  }

  async create(input: {
    userId: string;
    side: PaperLimitOrderSide;
    trigger: PaperLimitTrigger;
    targetPriceUsd: number;
    projectId?: string;
    amountUsd?: number;
    sellPercent?: number;
    dexscreenerUrl?: string;
  }) {
    if (input.targetPriceUsd <= 0) {
      throw new BadRequestException('Target price must be positive');
    }

    if (input.side === PaperLimitOrderSide.SELL) {
      if (!input.projectId) {
        throw new BadRequestException('projectId required for sell limit orders');
      }
      const position = await this.prisma.paperPosition.findFirst({
        where: { projectId: input.projectId, portfolio: { userId: input.userId } },
      });
      if (!position) {
        throw new BadRequestException('No open position for this project');
      }
    } else {
      if (!input.dexscreenerUrl?.trim()) {
        throw new BadRequestException('dexscreenerUrl required for buy limit orders');
      }
      if (!input.amountUsd || input.amountUsd < 1) {
        throw new BadRequestException('amountUsd required for buy limit orders (min $1)');
      }
      if (input.trigger !== PaperLimitTrigger.LTE) {
        throw new BadRequestException('Buy limits use LTE trigger (buy when price drops to target)');
      }
    }

    const order = await this.prisma.paperLimitOrder.create({
      data: {
        userId: input.userId,
        projectId: input.projectId,
        side: input.side,
        trigger: input.trigger,
        targetPriceUsd: new Prisma.Decimal(input.targetPriceUsd),
        amountUsd: input.amountUsd != null ? new Prisma.Decimal(input.amountUsd) : undefined,
        sellPercent: new Prisma.Decimal(input.sellPercent ?? 100),
        dexscreenerUrl: input.dexscreenerUrl?.trim(),
      },
    });

    return { id: order.id, status: order.status };
  }

  async cancel(userId: string, orderId: string) {
    const order = await this.prisma.paperLimitOrder.findFirst({
      where: { id: orderId, userId, status: PaperLimitOrderStatus.OPEN },
    });
    if (!order) throw new NotFoundException('Open limit order not found');

    await this.prisma.paperLimitOrder.update({
      where: { id: orderId },
      data: { status: PaperLimitOrderStatus.CANCELLED },
    });
    return { cancelled: true };
  }

  async processOpenOrders() {
    if (this.processing) return;
    this.processing = true;

    try {
      const orders = await this.prisma.paperLimitOrder.findMany({
        where: { status: PaperLimitOrderStatus.OPEN },
        include: { project: true },
        take: 100,
      });

      for (const order of orders) {
        try {
          await this.tryFill(order.id);
        } catch (err) {
          this.logger.warn(
            `Limit order ${order.id} check failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      }
    } finally {
      this.processing = false;
    }
  }

  private async tryFill(orderId: string) {
    const order = await this.prisma.paperLimitOrder.findUnique({
      where: { id: orderId },
      include: { project: true },
    });
    if (!order || order.status !== PaperLimitOrderStatus.OPEN) return;

    let price = 0;
    if (order.projectId && order.project) {
      const metrics = await this.prisma.projectMetrics.findUnique({
        where: { projectId: order.projectId },
      });
      price = Number(metrics?.priceUsd ?? 0);
      if (!price) {
        const snap = await this.paperTrading.getProjectLivePrice(order.projectId);
        price = snap.priceUsd;
      }
    } else if (order.dexscreenerUrl) {
      const preview = await this.paperTrading.previewToken(order.dexscreenerUrl);
      price = Number(preview.marketPreview.priceUsd ?? 0);
    }

    if (!price || price <= 0) return;

    const target = Number(order.targetPriceUsd);
    const hit =
      order.trigger === PaperLimitTrigger.GTE ? price >= target : price <= target;
    if (!hit) return;

    if (order.side === PaperLimitOrderSide.SELL && order.projectId) {
      const pct = Number(order.sellPercent);
      if (pct >= 99.99) {
        await this.paperTrading.closePosition(order.userId, order.projectId, {
          comment: `Limit ${order.trigger} @ ${target}`,
        });
      } else {
        await this.paperTrading.closePosition(order.userId, order.projectId, {
          sellPercent: pct,
          comment: `Limit ${order.trigger} @ ${target}`,
        });
      }
    } else if (order.side === PaperLimitOrderSide.BUY && order.dexscreenerUrl) {
      await this.paperTrading.executeTrade({
        userId: order.userId,
        dexscreenerUrl: order.dexscreenerUrl,
        side: PaperTradeSide.BUY,
        amountUsd: Number(order.amountUsd ?? 0),
        comment: `Limit buy @ ${target}`,
      });
    }

    await this.prisma.paperLimitOrder.update({
      where: { id: orderId },
      data: { status: PaperLimitOrderStatus.FILLED, filledAt: new Date() },
    });
  }
}
