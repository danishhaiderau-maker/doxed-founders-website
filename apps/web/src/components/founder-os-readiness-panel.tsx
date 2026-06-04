'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { resolveAiStackHealth } from '@dcf/utils';
import {
  fetchBuilderWorkerStatus,
  fetchFounderOnboardingStatus,
  type FounderOnboardingStatus,
} from '@/lib/api';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

type Props = {
  accessToken: string;
  onRefresh?: () => void;
};

export function FounderOsReadinessPanel({ accessToken, onRefresh }: Props) {
  const [onboarding, setOnboarding] = useState<FounderOnboardingStatus | null>(null);
  const [worker, setWorker] = useState<Awaited<ReturnType<typeof fetchBuilderWorkerStatus>> | null>(
    null,
  );
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ob, ws] = await Promise.all([
        fetchFounderOnboardingStatus(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
      ]);
      setOnboarding(ob);
      setWorker(ws);
    } catch {
      setOnboarding(null);
      setWorker(null);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const health = useMemo(
    () =>
      resolveAiStackHealth({
        llmConnected: worker?.llmConnected ?? false,
        buildWorker:
          (worker?.buildWorker as 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE' | 'NONE') ?? 'NONE',
        githubConnected: worker?.githubConnected ?? false,
      }),
    [worker],
  );

  const canAsk = worker?.llmConnected ?? false;
  const canBuild = worker?.buildWorker !== 'NONE' && worker?.buildWorker != null;
  const canPushCode = canBuild && (worker?.githubConnected ?? false);

  const incomplete = onboarding?.steps.filter((s) => !s.complete && !s.optional) ?? [];

  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-4 py-3 text-xs text-zinc-500">
        Checking Founder OS readiness…
      </div>
    );
  }

  const headline =
    health === 'healthy'
      ? 'Founder OS is fully active'
      : health === 'needs_attention'
        ? 'Founder OS is partially active'
        : 'Founder OS needs setup';

  const summary =
    health === 'healthy'
      ? 'Your agents can plan, chat with an LLM, and push code via the Builder Agent.'
      : canAsk && !canPushCode
        ? 'You can chat and queue work, but connect GitHub + Cursor in Settings to push code from the browser.'
        : !canAsk && canBuild
          ? 'Code agent is connected — add an LLM in Settings for smarter Ask answers.'
          : 'Complete the steps below so Copilot, agents, and Builder can run end-to-end.';

  return (
    <div
      className={`rounded-xl border px-4 py-3 ${
        health === 'healthy'
          ? 'border-emerald-500/30 bg-emerald-950/20'
          : health === 'needs_attention'
            ? 'border-amber-500/30 bg-amber-950/15'
            : 'border-zinc-800/80 bg-zinc-900/40'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p
            className={`text-sm font-semibold ${
              health === 'healthy'
                ? 'text-emerald-200'
                : health === 'needs_attention'
                  ? 'text-amber-200'
                  : 'text-white'
            }`}
          >
            {headline}
          </p>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-zinc-400">{summary}</p>
        </div>
        <button
          type="button"
          onClick={() => {
            void load();
            onRefresh?.();
          }}
          className="shrink-0 rounded-lg border border-zinc-700 px-2.5 py-1 text-[11px] text-zinc-400 hover:text-white"
        >
          Refresh
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
        <span
          className={`rounded-full px-2 py-0.5 ${
            canAsk ? 'bg-violet-950/60 text-violet-200' : 'bg-zinc-800 text-zinc-500'
          }`}
        >
          Ask / LLM {canAsk ? '✓' : '○'}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 ${
            canBuild ? 'bg-emerald-950/50 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
          }`}
        >
          Builder agent {canBuild ? '✓' : '○'}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 ${
            worker?.githubConnected ? 'bg-emerald-950/50 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
          }`}
        >
          GitHub repo {worker?.githubConnected ? '✓' : '○'}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 ${
            canPushCode ? 'bg-cyan-950/50 text-cyan-300' : 'bg-zinc-800 text-zinc-500'
          }`}
        >
          Push code from browser {canPushCode ? '✓' : '○'}
        </span>
      </div>

      {onboarding?.brainHint && !onboarding.brainReady && (
        <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-950/20 px-3 py-2 text-xs text-amber-100/90">
          {onboarding.brainHint}
        </p>
      )}

      {incomplete.length > 0 && (
        <ul className="mt-3 space-y-1.5 border-t border-zinc-800/60 pt-3">
          {incomplete.map((step) => (
            <li key={step.id} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-zinc-400">{step.label}</span>
              {step.href ? (
                <Link href={step.href} className="font-medium text-violet-400 hover:underline">
                  Set up →
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {onboarding?.requiredComplete && !canPushCode && (
        <p className="mt-2 text-[11px] text-zinc-500">
          Tip: In Copilot use <strong className="text-zinc-400">Build</strong> (not Ask) after Cursor +
          GitHub are connected to run code on your repo.
        </p>
      )}

      <Link
        href={AI_STACK_HREF}
        className="mt-3 inline-block text-xs font-medium text-cyan-400 hover:text-cyan-300"
      >
        Open AI stack & integrations →
      </Link>
    </div>
  );
}
