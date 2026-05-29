'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  connectGitHubRepo,
  connectIntegration,
  copilotHandsFree,
  createFounderBounty,
  disconnectIntegration,
  fetchFounderOsDashboard,
  FounderOsDashboard,
  IntegrationProviderConfig,
  publishSuggestedUpdate,
  runCursorBuildRoom,
  syncGitHubCommits,
} from '@/lib/api';

const COPILOT_ACTIONS = [
  { label: 'Generate weekly update', prompt: 'Generate this week\'s update.' },
  { label: 'Publish everywhere', prompt: 'Publish latest progress everywhere.' },
  { label: 'Create GitHub issues', prompt: 'Create GitHub issues from roadmap.' },
  { label: 'Prepare Raise Room', prompt: 'Prepare launch roadmap for Raise Room.' },
  { label: 'Launch readiness', prompt: 'Create launch readiness report.' },
  { label: 'Tokenomics draft', prompt: 'Create tokenomics draft for community allocation.' },
];

type FounderOsPanelProps = {
  accessToken: string;
  founderCredits?: number;
  communityRewardPool?: number;
  projectId?: string;
  onRefresh?: () => void;
  /** When false, actions require activating a founder profile first. */
  founderActive?: boolean;
};

export function FounderOsPanel({
  accessToken,
  founderCredits = 0,
  communityRewardPool = 0,
  projectId,
  onRefresh,
  founderActive = true,
}: FounderOsPanelProps) {
  const [data, setData] = useState<FounderOsDashboard | null>(null);
  const [repoInput, setRepoInput] = useState('');
  const [bountyTitle, setBountyTitle] = useState('');
  const [bountyDesc, setBountyDesc] = useState('');
  const [bountyCredits, setBountyCredits] = useState('500');
  const [buildTitle, setBuildTitle] = useState('');
  const [buildPrompt, setBuildPrompt] = useState('');
  const [connectProvider, setConnectProvider] = useState<IntegrationProviderConfig | null>(null);
  const [connectFields, setConnectFields] = useState<Record<string, string>>({});
  const [publishDest, setPublishDest] = useState({ buildFeed: true, x: true, community: true });
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
    if (!founderActive) {
      setMsg('Activate your founder profile first (Project section below)');
      return;
    }
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
    if (!founderActive) {
      setMsg('Activate your founder profile first (Project section below)');
      return;
    }
    try {
      const result = await syncGitHubCommits(accessToken);
      setMsg(`Synced ${result.commits.length} commits — review and publish everywhere`);
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Sync failed');
    }
  }

  async function handlePublish(suggestionId: string) {
    try {
      const result = await publishSuggestedUpdate(suggestionId, accessToken, publishDest);
      const parts: string[] = [];
      if (result.destinations?.buildFeed?.ok) parts.push('build feed');
      if (result.destinations?.x?.ok) parts.push('X');
      if (result.destinations?.community?.ok) parts.push('community');
      setMsg(parts.length ? `Published to ${parts.join(', ')}` : 'Published');
      load();
      onRefresh?.();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Publish failed');
    }
  }

  async function handleConnectProvider() {
    if (!connectProvider) return;
    try {
      if (connectProvider.connectType === 'toggle') {
        await connectIntegration({ provider: connectProvider.key }, accessToken);
        setMsg(`${connectProvider.label} connected`);
      } else {
        const result = await connectIntegration(
          {
            provider: connectProvider.key,
            token: connectFields.token,
            projectName: connectFields.projectName,
          },
          accessToken,
        );
        setMsg(
          result.webhookUrl
            ? `${connectProvider.label} connected — webhook: ${result.webhookUrl}`
            : `${connectProvider.label} connected (${result.accountName})`,
        );
      }
      setConnectProvider(null);
      setConnectFields({});
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Connect failed');
    }
  }

  async function handleDisconnect(provider: string) {
    try {
      await disconnectIntegration(provider, accessToken);
      setMsg('Disconnected');
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Disconnect failed');
    }
  }

  async function handleBuildRoom() {
    if (!founderActive) {
      setMsg('Activate your founder profile first (Project section below)');
      return;
    }
    if (!buildPrompt.trim()) return;
    try {
      const result = await runCursorBuildRoom(
        { title: buildTitle.trim() || 'Build session', prompt: buildPrompt.trim() },
        accessToken,
      );
      setMsg(`Build room draft ready (${result.creditsSpent} credits) — publish everywhere below`);
      setBuildPrompt('');
      setBuildTitle('');
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Build room failed');
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
  const providers = data?.integrationProviders ?? [];

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-4">
          <p className="text-[10px] uppercase text-violet-300">Founder Credits</p>
          <p className="mt-1 text-2xl font-bold text-white">
            {(data?.founderCredits ?? founderCredits).toLocaleString()}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Bounties · build room · rewards</p>
        </div>
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-4">
          <p className="text-[10px] uppercase text-emerald-300">Community Pool</p>
          <p className="mt-1 text-2xl font-bold text-white">
            {(data?.communityRewardPool ?? communityRewardPool).toLocaleString()}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Mark helpful replies to distribute</p>
        </div>
        <div className="rounded-xl border border-sky-500/30 bg-sky-950/20 p-4">
          <p className="text-[10px] uppercase text-sky-300">Stack hub</p>
          <p className="mt-1 text-sm font-semibold text-white">
            {apps.filter((a) => a.connected).length} / {apps.length} connected
          </p>
          <p className="mt-1 text-xs text-zinc-500">One dashboard — fewer tools to pay for</p>
        </div>
      </div>

      {msg && <p className="text-sm text-emerald-300">{msg}</p>}

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <p className="text-xs uppercase tracking-widest text-zinc-500">Connected stack</p>
        <p className="mt-1 text-xs text-zinc-600">
          Link GitHub, Vercel, Railway, Neon, DigitalOcean, Supabase — deploy webhooks auto-draft updates.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {apps.map((a) => (
            <div
              key={a.provider}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs ${
                a.connected
                  ? 'bg-emerald-500/10 text-emerald-100 ring-1 ring-emerald-500/25'
                  : 'bg-zinc-800/80 text-zinc-500'
              }`}
            >
              <div>
                <span className="font-medium">{a.label}</span>
                {a.accountName && <span className="ml-2 text-zinc-500">· {a.accountName}</span>}
              </div>
              {a.connected && a.provider !== 'github' && a.provider !== 'x' ? (
                <button type="button" onClick={() => handleDisconnect(a.provider)} className="text-zinc-400 hover:text-white">
                  ×
                </button>
              ) : (
                <span>{a.connected ? '✓' : '—'}</span>
              )}
            </div>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {providers
            .filter((p) => p.connectType === 'token' || p.connectType === 'toggle')
            .map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => {
                  setConnectProvider(p);
                  setConnectFields({});
                }}
                className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300 hover:border-sky-500/50"
              >
                + {p.label}
              </button>
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

      <div className="rounded-xl border border-indigo-500/30 bg-indigo-950/15 p-4">
        <p className="text-sm font-semibold text-indigo-200">Founder Copilot · What would you like to do?</p>
        <p className="mt-1 text-xs text-zinc-500">
          Action workflows — not a blank prompt. Syncs GitHub, queue, Raise Room, and publish everywhere.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {COPILOT_ACTIONS.map((a) => (
            <button
              key={a.label}
              type="button"
              onClick={async () => {
                if (!founderActive) {
                  setMsg('Activate your founder profile first');
                  return;
                }
                try {
                  const result = await copilotHandsFree(a.prompt, accessToken);
                  setMsg(result.answer);
                  load();
                  onRefresh?.();
                } catch (err) {
                  setMsg(err instanceof Error ? err.message : 'Action failed');
                }
              }}
              className="rounded-lg border border-indigo-500/30 bg-black/30 px-3 py-1.5 text-xs text-indigo-100 hover:border-indigo-400/50 hover:text-white"
            >
              {a.label}
            </button>
          ))}
        </div>
        <details className="mt-4">
          <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-300">
            Manual session draft (50 credits)
          </summary>
          <input
            value={buildTitle}
            onChange={(e) => setBuildTitle(e.target.value)}
            placeholder="Session title"
            className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <textarea
            value={buildPrompt}
            onChange={(e) => setBuildPrompt(e.target.value)}
            placeholder="What did you ship? One line per item…"
            rows={3}
            className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={handleBuildRoom}
            className="mt-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white"
          >
            Generate update (50 credits)
          </button>
        </details>
      </div>

      {(data?.pendingSuggestions?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4">
          <p className="text-sm font-semibold text-amber-200">Suggested update — publish everywhere</p>
          <div className="mt-3 flex flex-wrap gap-3 text-xs text-zinc-400">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={publishDest.buildFeed}
                onChange={(e) => setPublishDest({ ...publishDest, buildFeed: e.target.checked })}
              />
              Build feed
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={publishDest.x}
                onChange={(e) => setPublishDest({ ...publishDest, x: e.target.checked })}
              />
              X
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={publishDest.community}
                onChange={(e) => setPublishDest({ ...publishDest, community: e.target.checked })}
              />
              Project room
            </label>
          </div>
          {data!.pendingSuggestions.map((s) => (
            <div key={s.id} className="mt-3 rounded-lg border border-zinc-800 bg-black/20 p-3">
              <p className="font-medium text-white">{s.headline}</p>
              {s.source && (
                <p className="mt-1 text-[10px] uppercase text-zinc-600">via {s.source}</p>
              )}
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
                Publish everywhere
              </button>
            </div>
          ))}
        </div>
      )}

      {connectProvider && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-zinc-700 bg-zinc-900 p-5">
            <p className="font-semibold text-white">Connect {connectProvider.label}</p>
            <p className="mt-1 text-xs text-zinc-500">{connectProvider.billTip}</p>
            {connectProvider.fields.map((f) => (
              <input
                key={f.key}
                type={f.secret ? 'password' : 'text'}
                value={connectFields[f.key] ?? ''}
                onChange={(e) => setConnectFields({ ...connectFields, [f.key]: e.target.value })}
                placeholder={f.placeholder}
                className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
            ))}
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                onClick={handleConnectProvider}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white"
              >
                Connect
              </button>
              <button
                type="button"
                onClick={() => setConnectProvider(null)}
                className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-400"
              >
                Cancel
              </button>
            </div>
          </div>
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
