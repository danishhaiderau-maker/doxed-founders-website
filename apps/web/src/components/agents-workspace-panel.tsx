'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { AGENT_CATEGORY_LABELS, AGENT_RUN_CREDITS, WORKFORCE_TEMPLATES } from '@dcf/utils';
import { AgentCard } from '@/components/agent-card';
import {
  WorkforceTemplateCard,
  workforceTemplateHref,
} from '@/components/workforce-template-card';
import {
  createAgent,
  fetchAgentHub,
  fetchMyAgents,
  FounderAgentSummary,
} from '@/lib/api';

export function AgentsWorkspacePanel({
  accessToken,
  founderActive,
}: {
  accessToken: string;
  founderActive: boolean;
}) {
  const [top, setTop] = useState<FounderAgentSummary[]>([]);
  const [mine, setMine] = useState<{ created: FounderAgentSummary[]; installed: FounderAgentSummary[] } | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('RESEARCH');
  const [template, setTemplate] = useState('RESEARCHER');
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    const hub = await fetchAgentHub();
    setTop(hub.agents.slice(0, 6));
    if (founderActive) {
      setMine(await fetchMyAgents(accessToken));
    }
  }, [accessToken, founderActive]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  async function handleCreate() {
    if (!name.trim()) return;
    try {
      await createAgent({ name, description, category, template }, accessToken);
      setMsg('Agent created and listed in the store');
      setName('');
      setDescription('');
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Create failed');
    }
  }

  return (
    <div className="space-y-6">
      {msg && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">
          {msg}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-zinc-400">
          Your AI workforce — attached to your founder profile and project.
        </p>
        <Link href="/agents" className="text-sm text-purple-300 hover:underline">
          Open Agent Store →
        </Link>
      </div>

      {founderActive && (
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
          <h3 className="font-semibold text-white">Create public agent</h3>
          <p className="mt-1 text-xs text-zinc-500">Runs cost {AGENT_RUN_CREDITS} credits · LLM when Builder key connected</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Agent name"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <select
              value={template}
              onChange={(e) => {
                setTemplate(e.target.value);
                const t = WORKFORCE_TEMPLATES.find((x) => x.key === e.target.value);
                if (t) setCategory(t.category);
              }}
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            >
              {WORKFORCE_TEMPLATES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this agent do?"
              rows={2}
              className="sm:col-span-2 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
          </div>
          <button
            type="button"
            onClick={handleCreate}
            className="mt-3 rounded-lg bg-purple-600 px-4 py-2 text-sm text-white"
          >
            Publish agent
          </button>
        </section>
      )}

      {mine && mine.created.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Your agents</h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {mine.created.map((a) => (
              <AgentCard key={a.id} agent={a} />
            ))}
          </div>
        </section>
      )}

      {mine && mine.installed.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Installed</h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {mine.installed.map((a) => (
              <AgentCard key={a.id} agent={a} />
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">
          Quick-start templates
        </h3>
        <p className="mt-1 text-xs text-zinc-600">Opens Founder Copilot with a workforce prompt</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {WORKFORCE_TEMPLATES.map((t) => (
            <WorkforceTemplateCard
              key={t.key}
              template={t}
              href={workforceTemplateHref(t.key, founderActive)}
              compact
            />
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Agent store</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {top.map((a) => (
            <AgentCard key={a.id} agent={a} />
          ))}
        </div>
        <p className="mt-3 text-xs text-zinc-600">
          Categories: {Object.values(AGENT_CATEGORY_LABELS).slice(0, 5).join(' · ')}…
        </p>
      </section>
    </div>
  );
}
