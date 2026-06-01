'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  fetchRepoStarterTemplates,
  requestLaunchpadAccess,
  scaffoldGitHubRepo,
  type ProjectRoom,
  type RepoStarterTemplateSummary,
} from '@/lib/api';

const CHECK_LABELS: Record<string, string> = {
  founderVideo: 'Founder intro video',
  buildLogs: '2+ build log posts',
  demandValidated: '$10k+ simulated demand',
  simulatedRaiseComplete: '$50k+ raise momentum',
  communityScore: '5+ project followers',
};

type Props = {
  accessToken: string;
  room: ProjectRoom | null;
  onRefresh?: () => void;
  onTabChange?: (tab: 'funding' | 'community' | 'build') => void;
  onMessage?: (msg: string) => void;
};

export function LaunchPipelinePanel({
  accessToken,
  room,
  onRefresh,
  onTabChange,
  onMessage,
}: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [templates, setTemplates] = useState<RepoStarterTemplateSummary[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState('next-web3-dapp');
  const [repoName, setRepoName] = useState('my-dapp');
  const [err, setErr] = useState<string | null>(null);

  const launch = room?.launchpadAccess;
  const readiness = launch?.launchReadiness ?? room?.launchReadiness ?? 0;

  const loadTemplates = useCallback(async () => {
    try {
      const list = await fetchRepoStarterTemplates();
      setTemplates(list);
      if (list[0]) {
        setSelectedTemplate(list[0].key);
        setRepoName(list[0].defaultRepoName);
      }
    } catch {
      setTemplates([]);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  async function handleScaffold() {
    setBusy('scaffold');
    setErr(null);
    try {
      const result = await scaffoldGitHubRepo(
        { templateKey: selectedTemplate, repoName: repoName.trim() },
        accessToken,
      );
      onMessage?.(`Repo created: ${result.repoFullName} — commits auto-sync to Founder OS`);
      onRefresh?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Scaffold failed — connect GitHub OAuth first');
    } finally {
      setBusy(null);
    }
  }

  async function handleLaunchpadRequest() {
    if (!room?.id) return;
    setBusy('launchpad');
    setErr(null);
    try {
      await requestLaunchpadAccess(room.id, accessToken);
      onMessage?.('Launchpad access requested — project marked launch-ready');
      onRefresh?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Launchpad requirements not met yet');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-indigo-500/30 bg-indigo-950/15 p-5">
        <h3 className="text-lg font-semibold text-white">Launch pipeline</h3>
        <p className="mt-1 text-sm text-zinc-400">
          Commit → auto-sync → build feed → demand proof → launchpad. One view of your path to go-live.
        </p>

        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-zinc-800 bg-black/20 p-4">
            <p className="text-[10px] uppercase text-zinc-500">Launch readiness</p>
            <p className="mt-1 text-3xl font-bold text-indigo-200">{readiness}%</p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-black/20 p-4">
            <p className="text-[10px] uppercase text-zinc-500">Launchpad</p>
            <p className="mt-1 text-sm font-semibold text-white">
              {launch?.unlocked ? 'Requirements met' : 'In progress'}
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-black/20 p-4">
            <p className="text-[10px] uppercase text-zinc-500">Stage</p>
            <p className="mt-1 text-sm font-semibold text-white">
              {room?.lifecycleStage?.replace(/_/g, ' ') ?? '—'}
            </p>
          </div>
        </div>

        {launch && (
          <ul className="mt-5 space-y-2">
            {Object.entries(launch.checks).map(([key, ok]) => (
              <li
                key={key}
                className={`flex items-center justify-between rounded-lg border px-3 py-2 text-sm ${
                  ok
                    ? 'border-emerald-500/30 bg-emerald-950/20 text-emerald-100'
                    : 'border-zinc-800 text-zinc-400'
                }`}
              >
                <span>{CHECK_LABELS[key] ?? key}</span>
                <span>{ok ? '✓' : '—'}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!launch?.unlocked || busy === 'launchpad'}
            onClick={handleLaunchpadRequest}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy === 'launchpad' ? 'Requesting…' : 'Request launchpad access'}
          </button>
          <button
            type="button"
            onClick={() => onTabChange?.('funding')}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300 hover:text-white"
          >
            Open Raise Room →
          </button>
          <button
            type="button"
            onClick={() => onTabChange?.('community')}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300 hover:text-white"
          >
            Publish build log →
          </button>
          <Link
            href={room ? `/project/${room.slug}` : '/founder-den'}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300 hover:text-white"
          >
            Project room →
          </Link>
        </div>
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
        <h3 className="font-semibold text-white">Repo starter templates</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Create a GitHub repo from a DCF scaffold — memory files and Founder OS auto-sync included.
        </p>
        {templates.length === 0 ? (
          <p className="mt-4 text-xs text-zinc-500">Loading templates…</p>
        ) : (
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {templates.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => {
                  setSelectedTemplate(t.key);
                  setRepoName(t.defaultRepoName);
                }}
                className={`rounded-xl border p-3 text-left text-sm ${
                  selectedTemplate === t.key
                    ? 'border-violet-500/50 bg-violet-950/30'
                    : 'border-zinc-800 hover:border-zinc-600'
                }`}
              >
                <p className="font-medium text-white">{t.label}</p>
                <p className="mt-1 text-xs text-zinc-500">{t.description}</p>
              </button>
            ))}
          </div>
        )}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            value={repoName}
            onChange={(e) => setRepoName(e.target.value)}
            placeholder="repo-name"
            className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <button
            type="button"
            disabled={busy === 'scaffold' || !repoName.trim()}
            onClick={handleScaffold}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy === 'scaffold' ? 'Creating…' : 'Create from template'}
          </button>
        </div>
        <p className="mt-2 text-xs text-zinc-600">
          Requires GitHub OAuth in onboarding or AI Stack. Connect OAuth, then scaffold.
        </p>
      </section>

      {err && <p className="text-sm text-red-300">{err}</p>}
    </div>
  );
}
