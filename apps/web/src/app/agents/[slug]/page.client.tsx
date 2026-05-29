'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { AGENT_CATEGORY_LABELS, AGENT_RUN_CREDITS } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { fetchAgent, installAgent, runAgent, FounderAgentSummary } from '@/lib/api';

export default function AgentDetailClient({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const [agent, setAgent] = useState<FounderAgentSummary | null>(null);
  const [prompt, setPrompt] = useState('');
  const [output, setOutput] = useState<Awaited<ReturnType<typeof runAgent>>['output'] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAgent(await fetchAgent(slug, session?.accessToken));
    } catch {
      setAgent(null);
    }
  }, [slug, session?.accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  if (!agent) {
    return (
      <main className="min-h-screen bg-[#050508] px-6 py-16 text-center text-zinc-500">
        Loading agent…
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">{agent.name}</h1>
            <p className="text-sm text-zinc-500">
              {AGENT_CATEGORY_LABELS[agent.category]} · by{' '}
              <Link href={`/founder/${agent.founder.slug}`} className="text-purple-300 hover:underline">
                {agent.founder.name}
              </Link>
              {agent.project && (
                <>
                  {' '}
                  →{' '}
                  <Link href={`/project/${agent.project.slug}`} className="text-emerald-400 hover:underline">
                    {agent.project.name}
                  </Link>
                </>
              )}
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-6 px-6 py-8">
        {msg && <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">{msg}</p>}

        <section className="grid gap-4 sm:grid-cols-4">
          {[
            { label: 'Followers', value: agent.followerCount },
            { label: 'Usage', value: agent.usageCount },
            { label: 'Rating', value: agent.rating || '—' },
            { label: 'Revenue', value: `${agent.revenueCredits} cr` },
          ].map((s) => (
            <div key={s.label} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-center">
              <p className="text-lg font-bold text-white">{s.value}</p>
              <p className="text-xs text-zinc-500">{s.label}</p>
            </div>
          ))}
        </section>

        {agent.description && (
          <p className="text-sm text-zinc-400">{agent.description}</p>
        )}

        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
          <h2 className="font-semibold text-white">Run agent</h2>
          <p className="mt-1 text-xs text-zinc-500">{AGENT_RUN_CREDITS} Founder Credits per run</p>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe what you need — e.g. Build a Solana wallet tracker MVP spec"
            rows={4}
            className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            {session?.accessToken ? (
              <>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      const r = await runAgent(agent.id, prompt, session.accessToken!);
                      setOutput(r.output);
                      setMsg(`Run complete (${r.creditsSpent} credits)`);
                    } catch (e) {
                      setMsg(e instanceof Error ? e.message : 'Run failed');
                    }
                  }}
                  className="rounded-lg bg-purple-600 px-4 py-2 text-sm text-white"
                >
                  Run ({AGENT_RUN_CREDITS} credits)
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    await installAgent(agent.id, session.accessToken!);
                    setMsg('Agent installed to your workspace');
                    load();
                  }}
                  className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300"
                >
                  {agent.installed ? 'Installed ✓' : 'Install'}
                </button>
              </>
            ) : (
              <Link href={`/login?callbackUrl=/agents/${slug}`} className="text-sm text-purple-300 underline">
                Sign in to run
              </Link>
            )}
          </div>
        </section>

        {output && (
          <section className="space-y-4 rounded-2xl border border-purple-500/30 bg-purple-950/10 p-5">
            <h2 className="font-semibold text-white">{output.title}</h2>
            <p className="text-sm text-zinc-400">{output.summary}</p>
            <div>
              <p className="text-xs font-semibold uppercase text-zinc-500">Tasks</p>
              <ul className="mt-2 list-inside list-disc text-sm text-zinc-300">
                {output.tasks.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
            {output.githubIssues.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase text-zinc-500">GitHub issues</p>
                <ul className="mt-2 text-sm text-zinc-300">
                  {output.githubIssues.map((i) => (
                    <li key={i}>• {i}</li>
                  ))}
                </ul>
              </div>
            )}
            <Link href="/founder-den?tab=build" className="inline-block text-sm text-emerald-400 hover:underline">
              Open Founder Copilot →
            </Link>
          </section>
        )}
      </div>
    </main>
  );
}
