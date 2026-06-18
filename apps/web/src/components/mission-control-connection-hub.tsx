'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { getIntegrationConnectGuide } from '@dcf/utils';
import { IntegrationConnectGuidePanel } from '@/components/integration-connect-guide-panel';
import { fetchPlatformSyncStatus, type FounderPromoUserStatus, type PlatformSyncStatus } from '@/lib/api';
import {
  AI_STACK_HREF,
  CONNECT_ACCOUNTS_HREF,
  infraConnectHref,
  listCopilotActions,
  resolveAiTeamCards,
  resolveCopilotStack,
  type ProviderRow,
} from '@/lib/copilot-ai-stack';
import { FounderAiTeamStrip } from '@/components/founder-ai-team-strip';

const INFRA_KEYS = ['github', 'neon', 'vercel', 'railway'] as const;

type Props = {
  accessToken: string;
  providers: ProviderRow[];
  defaultProvider: string;
  buildWorker?: string;
  workerConnections?: { cursor?: boolean; openHands?: boolean };
  builderWorking?: boolean;
  contentDraftReady?: boolean;
  promo?: FounderPromoUserStatus | null;
  compact?: boolean;
  /** Hide Research/Builder/Content workforce strip — Cursor-first Mission Control */
  hideWorkforceAgents?: boolean;
  onRefresh?: () => void;
};

export function MissionControlConnectionHub({
  accessToken,
  providers,
  defaultProvider,
  buildWorker = 'NONE',
  workerConnections,
  builderWorking = false,
  contentDraftReady = false,
  promo,
  compact = false,
  hideWorkforceAgents = false,
  onRefresh,
}: Props) {
  const [status, setStatus] = useState<PlatformSyncStatus | null>(null);
  const [guideKey, setGuideKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchPlatformSyncStatus(accessToken));
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const stack = useMemo(
    () => resolveCopilotStack(providers, defaultProvider, buildWorker, workerConnections),
    [providers, defaultProvider, buildWorker, workerConnections],
  );

  const aiTeam = useMemo(
    () =>
      resolveAiTeamCards(stack, providers, {
        builderWorking,
        contentDraftReady,
      }),
    [stack, providers, builderWorking, contentDraftReady],
  );

  const copilotActions = useMemo(
    () => listCopilotActions(providers, defaultProvider, workerConnections),
    [providers, defaultProvider, workerConnections],
  );

  const promoProviders = useMemo(
    () => providers.filter((p) => p.connected && p.billingSource === 'platform_promo'),
    [providers],
  );

  const infra =
    status?.platforms.filter((p) => INFRA_KEYS.includes(p.key as (typeof INFRA_KEYS)[number])) ?? [];
  const connectedInfra = infra.filter((p) => p.connected).length;
  const cp = status?.controlPlane;
  const guide = guideKey ? getIntegrationConnectGuide(guideKey) : null;
  const guideLabel = infra.find((p) => p.key === guideKey)?.label ?? guideKey ?? '';

  function handleInfraClick(key: string, connected: boolean) {
    if (connected) return;
    setGuideKey(key);
  }

  return (
    <>
      {promo?.enabled && promo.eligible && !compact && (
        <div className="rounded-xl border border-emerald-500/40 bg-gradient-to-r from-emerald-950/40 to-violet-950/20 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs font-semibold text-emerald-200">Platform AI promo active</p>
              <p className="mt-0.5 text-[11px] text-zinc-400">
                Gemini, DeepSeek, Cursor & more billed by Doxxed Crypto — connect your own keys anytime in Settings.
              </p>
            </div>
            <div className="text-right text-[11px] font-medium text-emerald-300">
              <p>
                {(promo.tokensRemaining / 1_000_000).toFixed(2)}M tokens left
              </p>
              <p className="text-emerald-400/80">
                {promo.daysRemaining ?? 0} days remaining
              </p>
            </div>
          </div>
          {promoProviders.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {promoProviders.map((p) => (
                <span
                  key={p.key}
                  className="rounded-full border border-emerald-500/30 bg-emerald-950/50 px-2 py-0.5 text-[10px] font-medium text-emerald-200"
                >
                  {p.label.split(/[(\[]/)[0].trim()} · Promo
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className={`space-y-3 rounded-xl border border-zinc-800/80 bg-zinc-900/40 ${
          compact ? 'px-3 py-2.5' : 'px-4 py-4'
        }`}
      >
        {!compact && (
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                Connections
              </p>
              <p className="mt-0.5 text-xs text-zinc-500">
                Tap gray chips to connect — optional on Sovereign / vault-only path.
              </p>
            </div>
            <Link
              href={CONNECT_ACCOUNTS_HREF}
              className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
            >
              Connect portal →
            </Link>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold text-zinc-500">Infra</span>
          {infra.length === 0 ? (
            <span className="text-[10px] text-zinc-600">Loading…</span>
          ) : (
            infra.map((p) =>
              p.connected ? (
                <span
                  key={p.key}
                  title={p.detail ?? p.label}
                  className="rounded-full bg-zinc-800/80 px-2.5 py-0.5 text-[10px] font-medium text-zinc-300"
                >
                  {p.label} ✓
                </span>
              ) : (
                <Link
                  key={p.key}
                  href={infraConnectHref(p.key)}
                  onClick={(e) => {
                    e.preventDefault();
                    handleInfraClick(p.key, false);
                  }}
                  title={`Connect ${p.label} — step-by-step guide`}
                  className="rounded-full border border-dashed border-zinc-600 bg-zinc-900/60 px-2.5 py-0.5 text-[10px] font-medium text-zinc-400 transition hover:border-cyan-500/50 hover:text-cyan-200"
                >
                  + {p.label}
                </Link>
              ),
            )
          )}
          {infra.length > 0 && (
            <span className="text-[10px] text-zinc-600">
              {connectedInfra}/{infra.length} connected
            </span>
          )}
          {cp && (
            <span className="text-[10px] text-zinc-600">
              · Ask {cp.legs.find((l) => l.key === 'ask')?.connected ? '✓' : '○'} · Code{' '}
              {cp.legs.find((l) => l.key === 'code')?.connected ? '✓' : '○'}
            </span>
          )}
          {buildWorker && buildWorker !== 'NONE' && (
            <span className="rounded-full bg-violet-950/50 px-2 py-0.5 text-[10px] text-violet-300">
              Builder active
            </span>
          )}
          {status?.autopilotEnabled && (
            <span className="rounded-full bg-emerald-950/40 px-2 py-0.5 text-[10px] text-emerald-400">
              Autopilot on
            </span>
          )}
        </div>

        {!hideWorkforceAgents && <FounderAiTeamStrip agents={aiTeam} compact linkable />}

        {!hideWorkforceAgents && copilotActions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-zinc-800/80 pt-3">
            <span className="mr-1 text-[10px] font-semibold text-zinc-500">Send as</span>
            {copilotActions.map((action) => (
              <span
                key={action.id}
                className={`rounded-full px-2 py-0.5 text-[10px] ${
                  action.kind === 'build'
                    ? 'border border-emerald-600/30 bg-emerald-950/30 text-emerald-200'
                    : 'border border-violet-600/30 bg-violet-950/30 text-violet-200'
                }`}
                title="Pick this action below the chat box before sending"
              >
                {action.label}
              </span>
            ))}
            <Link href={AI_STACK_HREF} className="text-[10px] text-zinc-600 hover:text-violet-400">
              + connect more
            </Link>
          </div>
        )}

        {!compact && (
          <p className="text-[10px] text-zinc-600">
            <Link href={AI_STACK_HREF} className="text-cyan-500/80 hover:text-cyan-300">
              AI stack &amp; Founder Node
            </Link>
            {' · '}
            <Link href={CONNECT_ACCOUNTS_HREF} className="text-cyan-500/80 hover:text-cyan-300">
              Neon · Vercel · Railway tokens
            </Link>
          </p>
        )}
      </div>

      {guide && guideKey && (
        <IntegrationConnectGuidePanel
          providerLabel={guideLabel}
          guide={guide}
          onClose={() => setGuideKey(null)}
        >
          <Link
            href={infraConnectHref(guideKey)}
            className="inline-flex rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
            onClick={() => {
              setGuideKey(null);
              onRefresh?.();
            }}
          >
            Open connect portal for {guideLabel} →
          </Link>
        </IntegrationConnectGuidePanel>
      )}
    </>
  );
}