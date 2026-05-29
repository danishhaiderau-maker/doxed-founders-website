import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { ChainSlug } from '@prisma/client';
import { CONTRACT_CHAIN_FALLBACK, parseTokenInput } from '@dcf/utils';
import { GeckoterminalService } from '../geckoterminal/geckoterminal.service';
import {
  buildPreviewFromPair,
  CHAIN_SLUG_TO_DEX,
  DexScreenerPairInfo,
  DexScreenerPreview,
  parseDexScreenerUrl,
} from './dexscreener.types';

@Injectable()
export class DexscreenerService {
  private readonly logger = new Logger(DexscreenerService.name);
  private readonly baseUrl =
    process.env.DEXSCREENER_API_URL ?? 'https://api.dexscreener.com';

  constructor(private readonly geckoterminal: GeckoterminalService) {}

  async previewFromUrl(url: string): Promise<DexScreenerPreview> {
    try {
      return await this.previewFromDexScreener(url);
    } catch (dexError) {
      this.logger.warn(
        `DexScreener failed, trying GeckoTerminal: ${dexError instanceof Error ? dexError.message : dexError}`,
      );
      try {
        return await this.geckoterminal.previewFromDexUrl(url);
      } catch {
        throw dexError;
      }
    }
  }

  async previewFromContract(
    chainSlug: ChainSlug,
    contractAddress: string,
  ): Promise<DexScreenerPreview> {
    const chainId = CHAIN_SLUG_TO_DEX[chainSlug];
    if (!chainId) {
      throw new BadRequestException(`Unsupported chain: ${chainSlug}`);
    }

    const address = contractAddress.trim();
    if (!address) {
      throw new BadRequestException('Contract address is required');
    }

    const pairs = await this.fetchTokenPairs(chainId, address);
    if (pairs.length === 0) {
      throw new BadRequestException(
        'Token not found on DexScreener yet. Enter project name/ticker manually and submit — lookup is optional.',
      );
    }

    const pair = [...pairs].sort(
      (a, b) => (b.liquidity?.usd ?? 0) - (a.liquidity?.usd ?? 0),
    )[0];

    const dexscreenerUrl =
      pair.url?.trim() ||
      `https://dexscreener.com/${chainId}/${pair.pairAddress}`;
    return buildPreviewFromPair(dexscreenerUrl, pair);
  }

  async previewFromInput(input: string): Promise<DexScreenerPreview> {
    const trimmed = input.trim();
    const parsed = parseTokenInput(trimmed);

    if (!parsed || parsed.kind === 'url') {
      return this.previewFromUrl(trimmed);
    }

    const chains = parsed.chainHint
      ? [parsed.chainHint]
      : [...CONTRACT_CHAIN_FALLBACK];
    let lastError: Error | null = null;
    for (const chain of chains) {
      try {
        return await this.previewFromContract(chain as ChainSlug, parsed.address);
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
      }
    }
    throw lastError ?? new BadRequestException('Token not found for contract address');
  }

  private async fetchTokenPairs(
    chainId: string,
    address: string,
  ): Promise<DexScreenerPairInfo[]> {
    const urls = [
      `${this.baseUrl}/latest/dex/tokens/${chainId}/${address}`,
      `${this.baseUrl}/token-pairs/v1/${chainId}/${address}`,
    ];

    for (const apiUrl of urls) {
      try {
        const response = await fetch(apiUrl);
        if (!response.ok) continue;

        const data = (await response.json()) as
          | { pairs?: DexScreenerPairInfo[] }
          | DexScreenerPairInfo[];

        if (Array.isArray(data)) {
          return data;
        }
        if (data.pairs?.length) {
          return data.pairs;
        }
      } catch (err) {
        this.logger.warn(
          `Token lookup ${apiUrl}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    return [];
  }

  private async previewFromDexScreener(url: string): Promise<DexScreenerPreview> {
    const parsed = parseDexScreenerUrl(url);
    if (!parsed) {
      throw new BadRequestException(
        'Invalid DexScreener URL. Example: https://dexscreener.com/solana/...',
      );
    }

    const apiUrl = `${this.baseUrl}/latest/dex/pairs/${parsed.chainId}/${parsed.address}`;
    const response = await fetch(apiUrl);

    if (!response.ok) {
      throw new BadRequestException(
        `DexScreener lookup failed (${response.status})`,
      );
    }

    const data = (await response.json()) as {
      pairs?: DexScreenerPairInfo[];
      pair?: DexScreenerPairInfo;
    };

    const pair = data.pair ?? data.pairs?.[0];
    if (!pair) {
      throw new BadRequestException('No pair data found for this DexScreener link');
    }

    return buildPreviewFromPair(url.trim(), pair);
  }
}
