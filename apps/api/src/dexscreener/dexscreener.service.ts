import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { GeckoterminalService } from '../geckoterminal/geckoterminal.service';
import {
  buildPreviewFromPair,
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
