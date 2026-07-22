import type { FounderPromoStatus } from '../founder-os/founder-promo.service';

export type FounderIdeEntitlements = {
  plan: 'free' | 'founder_pro' | 'launch_partner';
  managedTokens: {
    cap: number;
    used: number;
    remaining: number;
    eligible: boolean;
    resetsOrExpiresAt: string | null;
    daysRemaining: number | null;
  };
  personalProviders: {
    used: number | null;
    limit: number | null;
    localModelsCountTowardLimit: false;
  };
  message: string | null;
};

export function toFounderIdeEntitlements(
  status: FounderPromoStatus,
): FounderIdeEntitlements {
  return {
    plan: 'free',
    managedTokens: {
      cap: status.tokenCap,
      used: status.tokensUsed,
      remaining: status.tokensRemaining,
      eligible: status.eligible,
      resetsOrExpiresAt: status.expiresAt,
      daysRemaining: status.daysRemaining,
    },
    personalProviders: {
      used: null,
      limit: null,
      localModelsCountTowardLimit: false,
    },
    message: status.message,
  };
}
