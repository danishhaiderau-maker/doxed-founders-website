import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import type {
  DexQuote,
  DexRouter,
  DexSwapResult,
  DexVolume,
} from './dex-router.interface';

/**
 * DEX stub for Phase 8 — implements DexRouter. Real AMM / Jupiter plugs in
 * via the same interface. This fixed-price sandbox writes a simulated ledger
 * fee; it is not production DCF Swap settlement.
 */
@Injectable()
export class DexStubService implements DexRouter {
  readonly routerId = 'stub' as const;
  private readonly logger = new Logger(DexStubService.name);

  /** Platform fee in basis points. 0.1% = 10 bps. */
  static readonly FEE_BPS = 10;

  constructor(private readonly prisma: PrismaService) {}

  /**
   * Fixed price for a launch. Phase 8 = launch.initialPrice (no AMM curve yet).
   * Returns USD per whole token.
   */
  async getPrice(launchId: string): Promise<DexQuote> {
    const launch = await this.prisma.tokenLaunch.findUnique({
      where: { id: launchId },
      select: { initialPrice: true, status: true },
    });
    if (!launch) throw new NotFoundException(`Launch ${launchId} not found`);

    return {
      launchId,
      priceUsd: Number(launch.initialPrice),
      feeBps: DexStubService.FEE_BPS,
      live: launch.status === 'LIVE',
      router: 'stub',
      note: 'Fixed-price demo swap — Jupiter DexRouter interface ready for later wiring.',
    };
  }

  /**
   * Execute a fixed-price swap. `inputAmount` is in USD (Phase 8 — no real
   * SOL/USDC settlement yet; the UI labels this clearly as a demo swap).
   *
   *   outputTokens = (inputUsd * (1 - feeBps/10000)) / priceUsd
   *   feeUsd       = inputUsd * feeBps/10000
   *
   * The fee is accrued to PlatformTreasury as a USD-denominated row on the
   * founder treasury ledger (actionKey = DEX_FEE) so the existing treasury
   * surfaces pick it up.
   */
  async swap(
    launchId: string,
    userId: string | null,
    inputAmount: number,
  ): Promise<DexSwapResult> {
    if (inputAmount <= 0) {
      throw new BadRequestException('Input amount must be positive');
    }

    const launch = await this.prisma.tokenLaunch.findUnique({
      where: { id: launchId },
      select: { id: true, initialPrice: true, status: true, projectId: true },
    });
    if (!launch) throw new NotFoundException(`Launch ${launchId} not found`);
    if (launch.status !== 'LIVE') {
      throw new BadRequestException(
        'Swaps are only enabled once the launch is LIVE (commitment window closed).',
      );
    }

    const priceUsd = Number(launch.initialPrice);
    if (priceUsd <= 0) {
      throw new BadRequestException('Launch price not set');
    }

    const feeUsd = (inputAmount * DexStubService.FEE_BPS) / 10000;
    const netInput = inputAmount - feeUsd;
    const outputAmount = netInput / priceUsd;

    const swap = await this.prisma.dexSwap.create({
      data: {
        launchId,
        userId,
        inputAmount,
        outputAmount,
        feeUsd,
      },
    });

    // Accrue fee to PlatformTreasury. Singleton row keyed by id='default'.
    await this.prisma.platformTreasury.upsert({
      where: { id: 'default' },
      create: { id: 'default' },
      update: {},
    });

    // Treasury ledger entry — uses the same FounderTreasuryLedgerEntry model
    // the rest of the DDollar economy writes to, so admin surfaces stay unified.
    await this.prisma.founderTreasuryLedgerEntry.create({
      data: {
        userId,
        amountDdollar: 0,
        actionKey: 'DEX_FEE',
        label: `Sandbox swap ledger fee — launch ${launchId}`,
        metadata: {
          launchId,
          swapId: swap.id,
          inputUsd: inputAmount,
          feeUsd,
          projectId: launch.projectId,
          router: 'stub',
        },
      },
    });

    this.logger.log(
      `dex swap launch=${launchId} user=${userId ?? 'anon'} input=${inputAmount} output=${outputAmount.toFixed(4)} fee=${feeUsd.toFixed(6)}`,
    );

    return {
      swapId: swap.id,
      inputAmount,
      outputAmount,
      feeUsd,
      priceUsd,
      router: 'stub',
    };
  }

  /**
   * Aggregate DEX volume + fees for a launch (for the launch progress panel).
   */
  async getVolume(launchId: string): Promise<DexVolume> {
    const rows = await this.prisma.dexSwap.findMany({
      where: { launchId },
      select: { inputAmount: true, outputAmount: true, feeUsd: true },
    });
    return {
      totalInputUsd: rows.reduce((s, r) => s + Number(r.inputAmount), 0),
      totalOutputTokens: rows.reduce((s, r) => s + Number(r.outputAmount), 0),
      totalFeeUsd: rows.reduce((s, r) => s + Number(r.feeUsd), 0),
      swapCount: rows.length,
    };
  }
}
