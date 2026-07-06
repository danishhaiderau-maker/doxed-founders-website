import { Injectable } from '@nestjs/common';
import { MAX_TRUST_WEIGHT, computeTrustWeight } from '@dcf/utils';
import { TrustWeightService } from '../trust-center/trust-weight.service';

@Injectable()
export class AntiSybilService {
  constructor(private readonly trustWeight: TrustWeightService) {}

  async forUser(userId: string): Promise<number> {
    return this.trustWeight.forUser(userId);
  }

  /** Trust-weighted paper USD — sybil accounts contribute less to rankings. */
  effectivePaperUsd(amountUsd: number, trustWeight: number): number {
    const normalized = Math.max(0, amountUsd) * (trustWeight / MAX_TRUST_WEIGHT);
    return Math.round(normalized * 100) / 100;
  }

  /** Expose raw formula for tests and observability. */
  computeTrustWeight(input: Parameters<typeof computeTrustWeight>[0]): number {
    return computeTrustWeight(input);
  }
}
