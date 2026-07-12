/**
 * JupiterDexRouter — placeholder implementing DexRouter for a future Jupiter
 * quote + swap path. Not registered by default; TokenLaunchModule keeps
 * DexStubService as the active DEX_ROUTER until Jupiter keys / counsel land.
 *
 * Calling any method throws a clear "not wired" error so accidental wiring
 * fails loudly instead of silently using stub math under a Jupiter label.
 */
import { Injectable } from '@nestjs/common';
import type {
  DexQuote,
  DexRouter,
  DexSwapResult,
  DexVolume,
} from './dex-router.interface';

@Injectable()
export class JupiterDexRouter implements DexRouter {
  readonly routerId = 'jupiter' as const;

  private notWired(op: string): never {
    throw new Error(
      `JupiterDexRouter.${op}() is not wired yet — set DEX_ROUTER=jupiter only after Jupiter API keys + counsel-gated mainnet policy land. Use DexStubService for demos.`,
    );
  }

  async getPrice(_launchId: string): Promise<DexQuote> {
    this.notWired('getPrice');
  }

  async swap(
    _launchId: string,
    _userId: string | null,
    _inputAmount: number,
  ): Promise<DexSwapResult> {
    this.notWired('swap');
  }

  async getVolume(_launchId: string): Promise<DexVolume> {
    this.notWired('getVolume');
  }
}
