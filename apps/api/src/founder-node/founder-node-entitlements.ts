import type { FounderPromoStatus } from '../founder-os/founder-promo.service';

export type FounderIdeEntitlements = {
  plan: 'free' | 'builder' | 'team';
  priceCentsMonthly: number | null;
  team: {
    id: string;
    name: string;
    role: 'owner' | 'admin' | 'member';
  } | null;
  features: {
    coordination: boolean;
    remoteControl: boolean;
    rolesAndAudit: boolean;
  };
  managedTokens: {
    unit: 'weighted_tokens';
    weightsVersion: 'founder-wtu-v1';
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
    plan: status.plan,
    priceCentsMonthly: status.priceCentsMonthly,
    team: status.teamId && status.teamName && status.teamRole
      ? { id: status.teamId, name: status.teamName, role: status.teamRole }
      : null,
    features: {
      coordination: status.coordination,
      remoteControl: status.remoteControl,
      rolesAndAudit: status.rolesAndAudit,
    },
    managedTokens: {
      unit: status.unit,
      weightsVersion: status.weightsVersion,
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
