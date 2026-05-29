'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  connectGitHubRepo,
  createFounderBounty,
  fetchFounderOsDashboard,
  FounderOsDashboard,
  publishSuggestedUpdate,
  syncGitHubCommits,
} from '@/lib/api';

type FounderOsPanelProps = {
  accessToken: string;
  founderCredits?: number;
  communityRewardPool?: number;
  projectId?: string;
  onRefresh?: () => void;
};

export function FounderOsPanel({
  accessToken,
  founderCredits = 0,
  communityRewardPool = 0,
  projectId,
  onRefresh,
}: FounderOsPanelProps) {
  const [data, setData] = useState<FounderOsDashboard | null>(null);
  const [repoInput, setRepoInput] = useState('');
  const [bountyTitle, setBountyTitle] = useState('');
  const [bountyDesc, setBountyDesc] = useState('');
  const [bountyCredits, setBountyCredits] = useState('500');
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchFounderOsDashboard(accessToken));
    } catch {
      setData(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleConnectGitHub() {
    if (!repoInput.trim()) return;
    try {
      await connectGitHubRepo(repoInput.trim(), accessToken);
      setMsg('GitHub connected');
      load();
      onRefresh?.();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Connect failed');
    }
  }

  async function handleSyncGitHub() {
    try {
      const result = await syncGitHubCommits(accessToken);
      setMsg(`Synced ${result.commits.length} commits — review suggested update below`);
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Sync failed');
    }
  }

  async function handlePublish(suggestionId: string) {
    try {
      await publishSuggestedUpdate(suggestionId, accessToken);
      setMsg('Published to build feed');
      load();
      onRefresh?.();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Publish failed');
    }
  }

  async function handleCreateBounty() {
    if (!projectId) return;
    try {
      await createFounderBounty(
        projectId,
        {
          title: bountyTitle.trim(),
          description: bountyDesc.trim(),
          rewardCredits: Number(bountyCredits),
          rewardPoints: 100,
        },
        accessToken,
      );
      setMsg('Bounty posted');
      setBountyTitle('');
      setBountyDesc('');
      load();
      onRefresh?.();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Bounty failed');
    }
  }

  const apps = data?.connectedApps ?? [];

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-4">
          <p className="text-[10px] uppercase text-violet-300">Founder Credits</p>
          <p className="mt-1 text-2xl font-bold text-white">
            {(data?.founderCredits ?? founderCredits).toLocaleString()}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Bounties · demand tests · rewards</p>
        </div>
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-4">
          <p className="text-[10px] uppercase text-emerald-300">Community Pool</p>
          <p className="mt-1 text-2xl font-bold text-white">
            {(data?.communityRewardPool ?? communityRewardPool).toLocaleString()}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Mark helpful replies to distribute</p>
        </div>
        <div className="rounded-xl border border-sky-500/30 bg-sky-950/20 p-4">
          <p className="text-[10px] uppercase text-sky-300">Founder OS</p>
          <p className="mt-1 text-sm font-semibold text-white">Build → translate → publish</p>
          <p className="mt-1 text-xs text-zinc-500">One workflow, not five apps</p>
        </div>
      </div>

      {msg && <p className="text-sm text-emerald-300">{msg}</p>}

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <p className="text-xs uppercase tracking-widest text-zinc-500">Connected apps</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {apps.map((a) => (
            <span
              key={a.provider}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                a.connected
                  ? 'bg-emerald-500/15 text-emerald-200 ring-1 ring-emerald-500/30'
                  : 'bg-zinc-800 text-zinc-500'
              }`}
            >
              {a.label} {a.connected ? '✓' : '—'}
            </span>
          ))}
        </div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            value={repoInput}
            onChange={(e) => setRepoInput(e.target.value)}
            placeholder="GitHub owner/repo"
            className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={handleConnectGitHub}
            className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-black"
          >
            Connect GitHub
          </button>
          <button
            type="button"
            onClick={handleSyncGitHub}
            className="rounded-lg border border-emerald-500/40 px-4 py-2 text-sm text-emerald-200"
          >
            Sync commits
          </button>
        </div>
      </div>

      {(data?.pendingSuggestions?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4">
          <p className="text-sm font-semibold text-amber-200">Suggested update — publish everywhere</p>
          {data!.pendingSuggestions.map((s) => (
            <div key={s.id} className="mt-3 rounded-lg border border-zinc-800 bg-black/20 p-3">
              <p className="font-medium text-white">{s.headline}</p>
              <p className="mt-2 text-xs text-zinc-400 whitespace-pre-wrap">{s.body}</p>
              <details className="mt-2 text-xs text-zinc-500">
                <summary className="cursor-pointer text-sky-300">Trader view</summary>
                <p className="mt-1 whitespace-pre-wrap">{s.traderSummary}</p>
              </details>
              <button
                type="button"
                onClick={() => handlePublish(s.id)}
                className="mt-3 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white"
              >
                Publish to build feed
              </button>
            </div>
          ))}
        </div>
      )}

      {projectId && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <p className="text-sm font-semibold text-white">Post a bounty</p>
          <p className="mt-1 text-xs text-zinc-500">Need design, dev, research? Pay in Founder Credits.</p>
          <input
            value={bountyTitle}
            onChange={(e) => setBountyTitle(e.target.value)}
            placeholder="Title — e.g. Landing page design"
            className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <textarea
            value={bountyDesc}
            onChange={(e) => setBountyDesc(e.target.value)}
            placeholder="Description"
            rows={2}
            className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <div className="mt-2 flex gap-2">
            <input
              value={bountyCredits}
              onChange={(e) => setBountyCredits(e.target.value)}
              type="number"
              min={100}
              className="w-32 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={handleCreateBounty}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white"
            >
              Create bounty
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
