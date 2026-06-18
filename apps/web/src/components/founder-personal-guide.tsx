'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FOUNDER_PHASE_OPTIONS,
  FOUNDER_PRIORITY_OPTIONS,
  INFRASTRUCTURE_PATHWAYS,
  buildGuidePrompt,
  getIntegrationConnectGuide,
  listMissingRequirements,
  recommendPathway,
  type FounderPhase,
  type FounderPriority,
  type InfrastructurePathway,
} from '@dcf/utils';
import { IntegrationConnectGuidePanel } from '@/components/integration-connect-guide-panel';
import { fetchPlatformSyncStatus, type PlatformSyncStatus } from '@/lib/api';
import { CONNECT_ACCOUNTS_HREF, infraConnectHref } from '@/lib/copilot-ai-stack';

const STORAGE_KEY = 'dcf-founder-guide-prefs-v1';

type SavedPrefs = {
  phase?: FounderPhase;
  priority?: FounderPriority;
  pathwayId?: string;
  dismissed?: boolean;
};

type Props = {
  accessToken: string;
  onPrompt?: (prompt: string) => void;
  compact?: boolean;
};

function loadPrefs(): SavedPrefs {
  if (typeof window === 'undefined') return {};
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '{}') as SavedPrefs;
  } catch {
    return {};
  }
}

function savePrefs(prefs: SavedPrefs) {
  if (typeof window === 'undefined') return;
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

function connectedMap(status: PlatformSyncStatus | null): Record<string, boolean> {
  const platforms = status?.platforms ?? [];
  const byKey = Object.fromEntries(platforms.map((p) => [p.key, p.connected]));
  const hasLlm = (status?.chatProviders ?? []).some((p) => p.connected);
  return {
    github: Boolean(byKey.github),
    neon: Boolean(byKey.neon),
    vercel: Boolean(byKey.vercel),
    railway: Boolean(byKey.railway),
    cursor: Boolean(byKey.cursor),
    llm: hasLlm,
  };
}

export function FounderPersonalGuide({ accessToken, onPrompt, compact }: Props) {
  const [status, setStatus] = useState<PlatformSyncStatus | null>(null);
  const [phase, setPhase] = useState<FounderPhase | null>(null);
  const [priority, setPriority] = useState<FounderPriority | null>(null);
  const [pathway, setPathway] = useState<InfrastructurePathway | null>(null);
  const [guideKey, setGuideKey] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

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

  useEffect(() => {
    const saved = loadPrefs();
    if (saved.dismissed) setCollapsed(true);
    if (saved.phase) setPhase(saved.phase);
    if (saved.priority) setPriority(saved.priority);
    if (saved.pathwayId) {
      const p = INFRASTRUCTURE_PATHWAYS.find((x) => x.id === saved.pathwayId);
      if (p) setPathway(p);
    }
  }, []);

  const connected = useMemo(() => connectedMap(status), [status]);
  const recommended = useMemo(
    () => (phase && priority ? recommendPathway(phase, priority) : null),
    [phase, priority],
  );
  const activePath = pathway ?? recommended;
  const missing = useMemo(
    () => (activePath ? listMissingRequirements(activePath, connected) : []),
    [activePath, connected],
  );
  const topGap = missing[0] ?? null;
  const guide = guideKey ? getIntegrationConnectGuide(guideKey) : null;

  function persist(next: Partial<SavedPrefs>) {
    const merged = { ...loadPrefs(), ...next };
    savePrefs(merged);
  }

  function selectPath(p: InfrastructurePathway) {
    setPathway(p);
    persist({ pathwayId: p.id, phase: phase ?? undefined, priority: priority ?? undefined });
  }

  if (collapsed && compact) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="rounded-lg border border-cyan-500/30 bg-cyan-950/20 px-3 py-1.5 text-[11px] font-medium text-cyan-200 hover:bg-cyan-950/40"
      >
        Open setup guide
      </button>
    );
  }

  return (
    <div
      className={`rounded-xl border border-cyan-500/25 bg-gradient-to-br from-cyan-950/20 to-violet-950/10 ${
        compact ? 'px-3 py-2.5' : 'px-4 py-4'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-cyan-300/90">
            Your personal setup guide
          </p>
          <p className="mt-0.5 text-xs text-zinc-400">
            Like a product manager — I ask what you want, then show the right stack (Sovereign vs cloud).
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setCollapsed(true);
            persist({ dismissed: true });
          }}
          className="text-[10px] text-zinc-600 hover:text-zinc-400"
        >
          Minimize
        </button>
      </div>

      {!phase && (
        <div className="mt-3">
          <p className="text-xs font-medium text-white">What are you trying to do right now?</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {FOUNDER_PHASE_OPTIONS.map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => {
                  setPhase(opt.id);
                  persist({ phase: opt.id });
                }}
                className="rounded-lg border border-zinc-700/80 bg-zinc-900/50 px-3 py-2.5 text-left transition hover:border-cyan-500/40 hover:bg-zinc-900"
              >
                <p className="text-sm font-semibold text-white">{opt.label}</p>
                <p className="mt-1 text-[10px] leading-snug text-zinc-500">{opt.hint}</p>
              </button>
            ))}
          </div>
        </div>
      )}

      {phase && !priority && (
        <div className="mt-3">
          <p className="text-xs font-medium text-white">What matters most to you?</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {FOUNDER_PRIORITY_OPTIONS.map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => {
                  setPriority(opt.id);
                  persist({ priority: opt.id });
                }}
                className="rounded-lg border border-zinc-700/80 bg-zinc-900/50 px-3 py-2 text-left text-xs transition hover:border-violet-500/40"
              >
                <span className="font-semibold text-white">{opt.label}</span>
                <span className="mt-0.5 block text-[10px] text-zinc-500">{opt.hint}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {phase && priority && activePath && (
        <div className="mt-3 space-y-3">
          <div className="rounded-lg border border-violet-500/30 bg-violet-950/20 px-3 py-2.5">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300">
              Recommended · {activePath.optionLabel}
            </p>
            <p className="mt-1 text-sm font-semibold text-white">{activePath.title}</p>
            <p className="mt-1 text-xs text-zinc-400">{activePath.tagline}</p>
            <p className="mt-2 text-[11px] text-emerald-200/90">{activePath.costNote}</p>
          </div>

          {topGap && (
            <div className="rounded-lg border border-amber-500/35 bg-amber-950/25 px-3 py-2.5">
              <p className="text-xs font-semibold text-amber-100">
                Next: connect {topGap.label}
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-amber-100/85">{topGap.why}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Link
                  href={topGap.connectHref}
                  className="rounded-lg bg-amber-600/90 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-amber-500"
                >
                  Connect {topGap.label} →
                </Link>
                {topGap.guideKey && (
                  <button
                    type="button"
                    onClick={() => setGuideKey(topGap.guideKey!)}
                    className="rounded-lg border border-amber-500/40 px-3 py-1.5 text-[11px] text-amber-200 hover:bg-amber-950/40"
                  >
                    Step-by-step guide
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {INFRASTRUCTURE_PATHWAYS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => selectPath(p)}
                className={`rounded-lg border px-2.5 py-1 text-[10px] font-medium transition ${
                  activePath.id === p.id
                    ? 'border-cyan-500/50 bg-cyan-950/40 text-cyan-200'
                    : 'border-zinc-700 text-zinc-500 hover:border-zinc-600 hover:text-zinc-300'
                }`}
              >
                {p.optionLabel}: {p.id === 'sovereign' ? 'Sovereign' : p.id === 'hybrid' ? 'Hybrid' : 'Cloud'}
              </button>
            ))}
          </div>

          <ol className="list-decimal space-y-1 pl-4 text-[11px] text-zinc-400">
            {activePath.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>

          <div className="flex flex-wrap gap-2">
            {onPrompt && (
              <button
                type="button"
                onClick={() => onPrompt(buildGuidePrompt(activePath, phase))}
                className="rounded-lg bg-violet-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-violet-500"
              >
                Ask Founder Brain to guide me
              </button>
            )}
            <Link
              href={CONNECT_ACCOUNTS_HREF}
              className="rounded-lg border border-zinc-700 px-3 py-1.5 text-[11px] text-zinc-400 hover:text-white"
            >
              Connect portal
            </Link>
            <button
              type="button"
              onClick={() => {
                setPhase(null);
                setPriority(null);
                setPathway(null);
                persist({ phase: undefined, priority: undefined, pathwayId: undefined, dismissed: false });
              }}
              className="text-[11px] text-zinc-600 hover:text-zinc-400"
            >
              Start over
            </button>
          </div>
        </div>
      )}

      {guide && guideKey && (
        <IntegrationConnectGuidePanel
          providerLabel={guideKey}
          guide={guide}
          onClose={() => setGuideKey(null)}
        >
          <Link
            href={infraConnectHref(guideKey)}
            className="inline-flex rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
          >
            Open connect portal →
          </Link>
        </IntegrationConnectGuidePanel>
      )}
    </div>
  );
}
