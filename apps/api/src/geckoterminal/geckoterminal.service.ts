import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import {
  DexScreenerPreview,
  mapChainSlug,
  parseDexScreenerUrl,
} from '../dexscreener/dexscreener.types';
import {
  GeckoPoolResponse,
  mapDexScreenerChainToGecko,
} from './geckoterminal.types';

@Injectable()
export class GeckoterminalService {
  private readonly logger = new Logger(GeckoterminalService.name);
  private readonly baseUrl =
    process.env.GECKOTERMINAL_API_URL ?? 'https://api.geckoterminal.com/api/v2';

  async previewFromDexUrl(url: string): Promise<DexScreenerPreview> {
    const parsed = parseDexScreenerUrl(url);
    if (!parsed) {
      throw new BadRequestException('Invalid DexScreener URL for GeckoTerminal lookup');
    }

    const network = mapDexScreenerChainToGecko(parsed.chainId);
    if (!network) {
      throw new BadRequestException(
        `Chain "${parsed.chainId}" is not supported by GeckoTerminal fallback`,
      );
    }

    const apiUrl = `${this.baseUrl}/networks/${network}/pools/${parsed.address}?include=base_token,dex`;
    const response = await fetch(apiUrl, {
      headers: { Accept: 'application/json' },
    });

    if (response.status === 429) {
      throw new BadRequestException('GeckoTerminal rate limit (30/min). Try again shortly.');
    }

    if (!response.ok) {
      throw new BadRequestException(
        `GeckoTerminal lookup failed (${response.status})`,
      );
    }

    const payload = (await response.json()) as GeckoPoolResponse;
    const attrs = payload.data?.attributes;
    if (!attrs) {
      throw new BadRequestException('No pool data from GeckoTerminal');
    }

    const baseTokenId = payload.data.relationships?.base_token?.data?.id;
    const baseToken = payload.included?.find(
      (item) => item.type === 'token' && item.id === baseTokenId,
    )?.attributes;

    if (!baseToken) {
      throw new BadRequestException('No base token data from GeckoTerminal');
    }

    const chainSlug = mapChainSlug(parsed.chainId);
    const parseNum = (value?: string) => {
      if (value == null || value === '') return undefined;
      const n = Number(value);
      return Number.isFinite(n) ? n : undefined;
    };

    this.logger.log(`GeckoTerminal fallback used for ${network}/${parsed.address}`);

    return {
      dexscreenerUrl: url.trim(),
      pairAddress: attrs.address,
      projectName: baseToken.name,
      ticker: baseToken.symbol,
      contractAddress: baseToken.address,
      chainSlug,
      logoUrl: baseToken.image_url,
      summary: `${baseToken.name} (${baseToken.symbol}) on ${parsed.chainId}. Prices via GeckoTerminal.`,
      marketPreview: {
        priceUsd: attrs.base_token_price_usd,
        marketCap: parseNum(attrs.market_cap_usd),
        fdv: parseNum(attrs.fdv_usd),
        volume24h: parseNum(attrs.volume_usd?.h24),
        liquidityUsd: parseNum(attrs.reserve_in_usd),
        priceChange24h: parseNum(attrs.price_change_percentage?.h24),
      },
    };
  }
}
