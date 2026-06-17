/** Phase 5 — unified publish pipeline (social + host toggles). */

import type { PlatformConnectionsMap } from './platform-connections';
import { getPlatformToggles } from './platform-connections';
import type { PublishDestinations } from './publish-everywhere';

export type PublishHostTarget = {
  provider: string;
  label: string;
  publishEnabled: boolean;
};

export type UnifiedPublishPlan = {
  social: PublishDestinations;
  hostTargets: PublishHostTarget[];
  hostRedeployProviders: string[];
  notes: string[];
};

const HOST_PUBLISH_KEYS = ['vercel', 'railway', 'render'] as const;

const HOST_LABELS: Record<string, string> = {
  vercel: 'Vercel',
  railway: 'Railway',
  render: 'Render',
};

export function resolveUnifiedPublishPlan(
  connections: PlatformConnectionsMap,
  overrides: Partial<PublishDestinations> = {},
): UnifiedPublishPlan {
  const hostTargets: PublishHostTarget[] = HOST_PUBLISH_KEYS.map((key) => ({
    provider: key,
    label: HOST_LABELS[key] ?? key,
    publishEnabled: getPlatformToggles(connections, key).publish,
  }));

  const hostRedeployProviders = hostTargets.filter((h) => h.publishEnabled).map((h) => h.provider);

  const social: PublishDestinations = {
    buildFeed: overrides.buildFeed ?? true,
    x: overrides.x ?? true,
    community: overrides.community ?? true,
  };

  const notes: string[] = [];
  if (hostRedeployProviders.length > 0) {
    notes.push(
      `Host publish toggles ON for ${hostRedeployProviders.join(', ')} — redeploy via Git push or Autopilot after social publish.`,
    );
  }
  if (getPlatformToggles(connections, 'github').publish) {
    notes.push('GitHub publish toggle ON — ship commits to trigger connected hosts.');
  }
  if (getPlatformToggles(connections, 'founder_node').publish) {
    notes.push('Founder Node publish toggle ON — vault snapshot included in publish graph.');
  }

  return { social, hostTargets, hostRedeployProviders, notes };
}

export type PublishHostResult = {
  provider: string;
  ok: boolean;
  skipped?: boolean;
  detail?: string;
};

export function buildHostPublishResults(plan: UnifiedPublishPlan): PublishHostResult[] {
  return plan.hostTargets.map((h) => ({
    provider: h.provider,
    ok: h.publishEnabled,
    skipped: !h.publishEnabled,
    detail: h.publishEnabled
      ? 'Queued — connect Autopilot redeploy or push to GitHub to rebuild'
      : 'Publish toggle off in Connect Hub',
  }));
}
