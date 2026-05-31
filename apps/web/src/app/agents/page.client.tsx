'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import {
  AGENT_CATEGORY_LABELS,
  AGENT_RUN_CREDITS,
  WORKFORCE_TEMPLATES,
  formatRelativeTime,
} from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AgentCard } from '@/components/agent-card';
import {
  WorkforceTemplateCard,
  workforceTemplateHref,
} from '@/components/workforce-template-card';
import {
  AgentActivityItem,
  fetchAgentActivityRecent,
  fetchAgentHub,
  FounderAgentSummary,
} from '@/lib/api';

const ORCHESTRATOR_STEPS = [
  { step: '1', title: 'You ask Copilot', detail: 'One prompt — Copilot routes the work.' },
  { step: '2', title: 'Agents execute', detail: 'Research, build, market, launch — behind the scenes.' },
  { step: '3', title: 'Results land in Founder OS', detail: 'Tasks, issues, and updates sync to your queue.' },
];

export default function AgentsPageClient() {
  const { data: session } = useSession();
  const signedIn = Boolean(session?.accessToken);
  const [hub, setHub] = useState<Awaited<ReturnType<typeof fetchAgentHub>> | null>(null);
  const [activity, setActivity] = useState<AgentActivityItem[]>([]);
  const [category, setCategory] = useState<string>('');

  const load = useCallback(async () => {
    try {
      const [hubData, recent] = await Promise.all([
        fetchAgentHub(category || undefined),
        fetchAgentActivityRecent(12),
      ]);
      setHub(hubData);
      setActivity(recent);
    } catch {
      setHub(null);
      setActivity([]);
    }
  }, [category]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 90_000);
    return () => clearInterval(interval);
  }, [load]);

  const agents = hub?.agents ?? [];
  const templates = hub?.templates ?? WORKFORCE_TEMPLATES;

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Agent Hub</h1>
            <p className="text-sm text-zinc-500">
              Copilot orchestrates your workforce — you ask, agents work, results sync to Founder OS
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-10 px-6 py-8">
        {/* Copilot-first hero */}
        <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/30 to-[#0a0a0e] p-6 sm:p-8">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">
            Founder Copilot · Orchestrator
          </p>
          <h2 className="mt-2 text-xl font-bold text-white sm:text-2xl">
            Your startup&apos;s AI workforce — one Copilot, many agents
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-400">
            Pick a workforce template below to open Founder Copilot with a ready-made prompt. Workers
            now execute tools when connected — GitHub issues, Cursor agents, and build queue sync.
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            {signedIn ? (
              <>
                <Link
                  href="/founder-den?tab=activity"
                  className="rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-violet-500"
                >
                  Open Founder Copilot →
                </Link>
                <Link
                  href="/founder-den?tab=agents"
                  className="rounded-lg border border-zinc-700 px-4 py-2.5 text-sm text-zinc-300 hover:border-violet-500/40 hover:text-white"
                >
                  Manage my agents
                </Link>
              </>
            ) : (
              <Link
                href="/login?callbackUrl=/founder-den?tab=activity"
                className="rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white"
              >
                Sign in to open Copilot
              </Link>
            )}
          </div>

          <ol className="mt-8 grid gap-3 sm:grid-cols-3">
            {ORCHESTRATOR_STEPS.map((item) => (
              <li
                key={item.step}
                className="rounded-xl border border-zinc-800/80 bg-black/20 px-4 py-3"
              >
                <span className="text-[10px] font-bold text-violet-400">Step {item.step}</span>
                <p className="mt-1 text-sm font-medium text-white">{item.title}</p>
                <p className="mt-0.5 text-xs text-zinc-500">{item.detail}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Workforce templates → Copilot deep links */}
        <section>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">
                Workforce templates
              </h2>
              <p className="mt-1 text-xs text-zinc-600">
                Click any agent — opens Founder Copilot with a starter prompt
              </p>
            </div>
            <p className="text-[10px] text-zinc-600">
              Agent runs · {AGENT_RUN_CREDITS} credits · auto GitHub + Cursor when connected
            </p>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((t) => (
              <WorkforceTemplateCard
                key={t.key}
                template={t}
                href={workforceTemplateHref(t.key, signedIn)}
              />
            ))}
          </div>
        </section>

        {/* Activity feed */}
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">
                Live activity
              </h2>
              <p className="mt-1 text-xs text-zinc-600">Recent agent runs across the platform</p>
            </div>
            <button
              type="button"
              onClick={() => load()}
              className="text-xs text-zinc-500 hover:text-white"
            >
              Refresh
            </button>
          </div>
          {activity.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">No agent runs yet — be the first to ask Copilot.</p>
          ) : (
            <ul className="mt-4 divide-y divide-zinc-800/80">
              {activity.map((item) => (
                <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {item.source === 'copilot' ? (
                        <Link
                          href="/founder-den?tab=build"
                          className="text-sm font-medium text-violet-300 hover:text-violet-200"
                        >
                          {item.agentName}
                        </Link>
                      ) : (
                        <Link
                          href={`/agents/${item.agentSlug}`}
                          className="text-sm font-medium text-violet-300 hover:text-violet-200"
                        >
                          {item.agentName}
                        </Link>
                      )}
                      {item.source === 'copilot' && (
                        <span className="rounded bg-violet-950/50 px-1.5 py-0.5 text-[9px] text-violet-300">
                          Copilot
                        </span>
                      )}
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] uppercase text-zinc-500">
                        {AGENT_CATEGORY_LABELS[item.category] ?? item.category}
                      </span>
                    </div>
                    <p className="mt-0.5 text-sm text-zinc-300">{item.outputTitle}</p>
                    {item.outputSummary && (
                      <p className="mt-0.5 text-xs text-zinc-500 line-clamp-2">{item.outputSummary}</p>
                    )}
                    <p className="mt-1 text-[10px] text-zinc-600">
                      {item.founder.name}
                      {item.project ? ` · ${item.project.name}` : ''}
                      {' · '}
                      {formatRelativeTime(item.createdAt)}
                    </p>
                  </div>
                  <Link
                    href={workforceTemplateHref(item.template, signedIn, item.project?.name)}
                    className="shrink-0 rounded-lg border border-zinc-700 px-2.5 py-1 text-[10px] text-zinc-400 hover:border-violet-500/40 hover:text-violet-200"
                  >
                    Run similar →
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Marketplace */}
        <section>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setCategory('')}
              className={`rounded-lg px-3 py-1.5 text-xs ${!category ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
            >
              All
            </button>
            {(hub?.categories ?? []).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setCategory(c)}
                className={`rounded-lg px-3 py-1.5 text-xs ${category === c ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                {AGENT_CATEGORY_LABELS[c] ?? c}
              </button>
            ))}
          </div>

          <h2 className="mt-6 text-sm font-semibold uppercase tracking-widest text-zinc-500">
            Public agents
          </h2>
          {agents.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">Loading agents…</p>
          ) : (
            <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {agents.map((a: FounderAgentSummary) => (
                <AgentCard key={a.id} agent={a} />
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
