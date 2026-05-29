'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { AGENT_CATEGORY_LABELS } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AgentCard } from '@/components/agent-card';
import { fetchAgentHub, FounderAgentSummary } from '@/lib/api';

export default function AgentsPageClient() {
  const { data: session } = useSession();
  const [hub, setHub] = useState<Awaited<ReturnType<typeof fetchAgentHub>> | null>(null);
  const [category, setCategory] = useState<string>('');

  const load = useCallback(async () => {
    try {
      setHub(await fetchAgentHub(category || undefined));
    } catch {
      setHub(null);
    }
  }, [category]);

  useEffect(() => {
    load();
  }, [load]);

  const agents = hub?.agents ?? [];

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Agent Hub</h1>
            <p className="text-sm text-zinc-500">
              Founder → Project → Agent — accountable AI workforce, not anonymous bots
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-8 px-6 py-8">
        <section className="rounded-2xl border border-purple-500/30 bg-purple-950/15 p-6">
          <h2 className="text-lg font-semibold text-white">Founder workforce</h2>
          <p className="mt-2 max-w-2xl text-sm text-zinc-400">
            Install research, community, audit, and launch agents tied to verified founders and real
            projects. Every agent run produces specs, tasks, and GitHub-ready output — then sync to
            Build Room.
          </p>
          {session ? (
            <Link
              href="/founder-den?tab=agents"
              className="mt-4 inline-block rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-white"
            >
              Create agent in workspace →
            </Link>
          ) : (
            <Link href="/login?callbackUrl=/agents" className="mt-4 inline-block text-sm text-purple-300 underline">
              Sign in to create agents
            </Link>
          )}
        </section>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setCategory('')}
            className={`rounded-lg px-3 py-1.5 text-xs ${!category ? 'bg-zinc-700 text-white' : 'text-zinc-500'}`}
          >
            All
          </button>
          {(hub?.categories ?? []).map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              className={`rounded-lg px-3 py-1.5 text-xs ${category === c ? 'bg-purple-600 text-white' : 'text-zinc-500'}`}
            >
              {AGENT_CATEGORY_LABELS[c] ?? c}
            </button>
          ))}
        </div>

        <section>
          <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Top agents</h2>
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

        {hub?.templates && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">
              Workforce templates
            </h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {hub.templates.map((t) => (
                <div key={t.key} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
                  <p className="font-medium text-white">{t.label}</p>
                  <p className="mt-1 text-xs text-zinc-500">{t.description}</p>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
