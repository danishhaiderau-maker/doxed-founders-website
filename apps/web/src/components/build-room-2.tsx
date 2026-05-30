'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  BuildQueueItem,
  BuildRoomData,
  CommandBarIntent,
  dismissBuildQueueItem,
  fetchBuildRoom,
  publishGitHubIssues,
  runCommandBar,
  updateBuildQueueItem,
} from '@/lib/api';
import { FounderCopilotBriefing } from '@/components/founder-copilot-briefing';
import { FounderCopilotBar } from '@/components/founder-copilot-bar';
import { FounderOsPanel } from '@/components/founder-os-panel';
import { HandsFreeModal, shouldShowHandsFreeIntro } from '@/components/hands-free-modal';
import { copilotHandsFree } from '@/lib/api';

type BuildRoomTab = 'ideas' | 'tasks' | 'issues' | 'commits' | 'deployments' | 'prs';

const TABS: { id: BuildRoomTab; label: string }[] = [
  { id: 'ideas', label: 'Ideas' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'issues', label: 'Issues' },
  { id: 'commits', label: 'Commits' },
  { id: 'deployments', label: 'Deployments' },
  { id: 'prs', label: 'PRs' },
];

const COMMANDS: { intent: CommandBarIntent; label: string; placeholder: string }[] = [
  { intent: 'roadmap', label: 'Create roadmap', placeholder: 'mobile app, Q2 launch…' },
  { intent: 'release_notes', label: 'Release notes', placeholder: 'optional focus…' },
  { intent: 'weekly_summary', label: 'Weekly summary', placeholder: 'optional theme…' },
];

function ItemRow({
  item,
  onDone,
  onDismiss,
}: {
  item: BuildQueueItem;
  onDone: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  return (
    <li className="rounded-lg border border-zinc-800 bg-black/30 px-3 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-white">{item.title}</p>
          <p className="mt-0.5 text-[10px] uppercase text-zinc-600">
            {item.kind} · {item.status} · {item.source.replace('_', ' ')}
          </p>
          {item.description && (
            <p className="mt-1 text-xs text-zinc-500 line-clamp-2">{item.description}</p>
          )}
          {item.githubIssueUrl && (
            <a
              href={item.githubIssueUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-block text-[10px] text-sky-400 hover:underline"
            >
              View on GitHub →
            </a>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          {item.status !== 'DONE' && (
            <button
              type="button"
              onClick={() => onDone(item.id)}
              className="rounded border border-emerald-500/40 px-2 py-0.5 text-[10px] text-emerald-300"
            >
              Done
            </button>
          )}
          <button
            type="button"
            onClick={() => onDismiss(item.id)}
            className="rounded border border-zinc-700 px-2 py-0.5 text-[10px] text-zinc-500"
          >
            Dismiss
          </button>
        </div>
      </div>
    </li>
  );
}

export type BuildRoom2Props = {
  accessToken: string;
  founderCredits?: number;
  communityRewardPool?: number;
  projectId?: string;
  onRefresh?: () => void;
  founderActive?: boolean;
  onMessage?: (msg: string) => void;
};

export function BuildRoom2({
  accessToken,
  founderCredits,
  communityRewardPool,
  projectId,
  onRefresh,
  founderActive = true,
  onMessage,
}: BuildRoom2Props) {
  const [tab, setTab] = useState<BuildRoomTab>('ideas');
  const [room, setRoom] = useState<BuildRoomData | null>(null);
  const [cmdIntent, setCmdIntent] = useState<CommandBarIntent>('roadmap');
  const [cmdPrompt, setCmdPrompt] = useState('');
  const [cmdBusy, setCmdBusy] = useState(false);
  const [publishBusy, setPublishBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showHandsFree, setShowHandsFree] = useState(false);

  useEffect(() => {
    if (shouldShowHandsFreeIntro()) setShowHandsFree(true);
  }, []);

  const load = useCallback(async () => {
    try {
      setRoom(await fetchBuildRoom(accessToken));
    } catch {
      setRoom(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDone(id: string) {
    await updateBuildQueueItem(id, { status: 'DONE' }, accessToken);
    load();
  }

  async function handleDismiss(id: string) {
    await dismissBuildQueueItem(id, accessToken);
    load();
  }

  async function handleCommand() {
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    setCmdBusy(true);
    try {
      const result = await runCommandBar(cmdIntent, cmdPrompt.trim() || undefined, accessToken);
      onMessage?.(`${result.result.title} (${result.creditsSpent} credits)`);
      setCmdPrompt('');
      load();
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Command failed');
    } finally {
      setCmdBusy(false);
    }
  }

  async function handlePublishIssues() {
    setPublishBusy(true);
    try {
      const result = await publishGitHubIssues(accessToken);
      onMessage?.(`Created ${result.created} GitHub issue(s) on ${result.repoFullName}`);
      load();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Publish failed');
    } finally {
      setPublishBusy(false);
    }
  }

  async function handleHandsFreeExample(example: string) {
    setShowHandsFree(false);
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    try {
      const result = await copilotHandsFree(example, accessToken);
      onMessage?.(result.answer);
      load();
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Hands-free failed');
    }
  }

  async function copyCursor() {
    if (!room?.cursorCopy) return;
    await navigator.clipboard.writeText(room.cursorCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const stats = room?.stats;

  return (
    <div className="space-y-6">
      {showHandsFree && (
        <HandsFreeModal
          onTry={handleHandsFreeExample}
          onDismiss={() => setShowHandsFree(false)}
        />
      )}

      <div className="grid gap-6 xl:grid-cols-[1fr_380px] 2xl:grid-cols-[1fr_420px]">
        <div className="space-y-6 min-w-0">
          <FounderCopilotBar accessToken={accessToken} onResult={(a) => onMessage?.(a)} />

          <div className="grid gap-3 grid-cols-2 sm:grid-cols-4">
            <div className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-3">
              <p className="text-[10px] uppercase text-zinc-500">Ideas</p>
              <p className="text-xl font-bold text-white">{stats?.ideas ?? 0}</p>
            </div>
            <div className="rounded-xl border border-sky-500/30 bg-sky-950/20 p-3">
              <p className="text-[10px] uppercase text-zinc-500">Open tasks</p>
              <p className="text-xl font-bold text-white">{stats?.tasks ?? 0}</p>
            </div>
            <div className="rounded-xl border border-amber-500/30 bg-amber-950/20 p-3">
              <p className="text-[10px] uppercase text-zinc-500">Issues</p>
              <p className="text-xl font-bold text-white">{stats?.issues ?? 0}</p>
            </div>
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-3">
              <p className="text-[10px] uppercase text-zinc-500">Commits</p>
              <p className="text-xl font-bold text-white">{stats?.commits ?? 0}</p>
            </div>
          </div>

          <FounderCopilotBriefing
            accessToken={accessToken}
            onMessage={onMessage}
            onRefresh={() => {
              load();
              onRefresh?.();
            }}
          />

      <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/10 p-4">
        <p className="text-sm font-semibold text-cyan-200">Quick Command Bar</p>
        <p className="mt-1 text-xs text-zinc-500">Roadmap · release notes · weekly summary (10 credits)</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {COMMANDS.map((c) => (
            <button
              key={c.intent}
              type="button"
              onClick={() => setCmdIntent(c.intent)}
              className={`rounded-lg px-3 py-1.5 text-xs ${
                cmdIntent === c.intent
                  ? 'bg-cyan-600 text-white'
                  : 'border border-zinc-700 text-zinc-400'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            value={cmdPrompt}
            onChange={(e) => setCmdPrompt(e.target.value)}
            placeholder={COMMANDS.find((c) => c.intent === cmdIntent)?.placeholder}
            className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <button
            type="button"
            disabled={cmdBusy}
            onClick={handleCommand}
            className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Run
          </button>
        </div>
      </div>

      {!room?.cursorConnected && room?.cursorCopy && (
        <div className="rounded-xl border border-indigo-500/40 bg-indigo-950/20 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-indigo-200">Agent prompt (fallback)</p>
              <p className="text-xs text-zinc-500">
                No remote agent connected — paste manually, or connect OpenHands in Settings → Builder.
              </p>
            </div>
            <button
              type="button"
              onClick={copyCursor}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white"
            >
              {copied ? 'Copied!' : 'Copy prompt'}
            </button>
          </div>
          <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-black/40 p-3 text-[11px] text-zinc-400 whitespace-pre-wrap">
            {room.cursorCopy.slice(0, 1200)}
            {room.cursorCopy.length > 1200 ? '…' : ''}
          </pre>
        </div>
      )}

      <nav className="flex flex-wrap gap-1 rounded-xl border border-zinc-800 bg-zinc-900/50 p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
              tab === t.id ? 'bg-indigo-600 text-white' : 'text-zinc-500 hover:text-zinc-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="min-h-[200px]">
        {tab === 'ideas' && (
          <ul className="space-y-2">
            {(room?.grouped.ideas ?? []).length === 0 && (
              <p className="text-sm text-zinc-500">No ideas yet — use Quick Build from your phone.</p>
            )}
            {(room?.grouped.ideas ?? []).map((item) => (
              <ItemRow key={item.id} item={item} onDone={handleDone} onDismiss={handleDismiss} />
            ))}
          </ul>
        )}
        {tab === 'tasks' && (
          <ul className="space-y-2">
            {(room?.grouped.tasks ?? []).map((item) => (
              <ItemRow key={item.id} item={item} onDone={handleDone} onDismiss={handleDismiss} />
            ))}
          </ul>
        )}
        {tab === 'issues' && (
          <div className="space-y-3">
            {room?.githubTokenConnected && (room?.grouped.issues ?? []).some((i) => !i.githubIssueUrl) && (
              <button
                type="button"
                disabled={publishBusy}
                onClick={handlePublishIssues}
                className="rounded-lg bg-sky-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
              >
                {publishBusy ? 'Publishing…' : 'Publish queued issues to GitHub'}
              </button>
            )}
            <ul className="space-y-2">
              {(room?.grouped.issues ?? []).map((item) => (
                <ItemRow key={item.id} item={item} onDone={handleDone} onDismiss={handleDismiss} />
              ))}
            </ul>
            {room?.repoFullName && !room.githubTokenConnected && (
              <p className="text-xs text-amber-400/90">
                Add a GitHub token in{' '}
                <Link href="/settings/builder" className="underline">
                  Builder settings
                </Link>{' '}
                to auto-create issues on {room.repoFullName}.
              </p>
            )}
          </div>
        )}
        {tab === 'commits' && (
          <ul className="space-y-2">
            {(room?.commits ?? []).length === 0 && (
              <p className="text-sm text-zinc-500">Connect GitHub and sync commits below.</p>
            )}
            {(room?.commits ?? []).map((c) => (
              <li key={c.sha} className="rounded-lg border border-zinc-800 px-3 py-2 text-sm">
                <span className="font-mono text-emerald-400">{c.sha}</span>
                <span className="ml-2 text-zinc-300">{c.message}</span>
                <span className="ml-2 text-[10px] text-zinc-600">
                  {new Date(c.date).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        )}
        {tab === 'deployments' && (
          <ul className="space-y-2">
            {(room?.deployments ?? []).length === 0 && (
              <p className="text-sm text-zinc-500">Connect Vercel/Railway webhooks to see deploys here.</p>
            )}
            {(room?.deployments ?? []).map((d) => (
              <li key={d.id} className="rounded-lg border border-zinc-800 px-3 py-2">
                <p className="text-sm text-white">{d.headline}</p>
                <p className="text-[10px] text-zinc-600">
                  {d.source} · {d.status} · {new Date(d.createdAt).toLocaleString()}
                </p>
              </li>
            ))}
          </ul>
        )}
        {tab === 'prs' && (
          <ul className="space-y-2">
            {(room?.pullRequests ?? []).length === 0 && (
              <p className="text-sm text-zinc-500">
                {room?.githubTokenConnected
                  ? 'No pull requests found.'
                  : 'Connect a GitHub token in Builder settings to list PRs.'}
              </p>
            )}
            {(room?.pullRequests ?? []).map((pr) => (
              <li key={pr.url} className="rounded-lg border border-zinc-800 px-3 py-2">
                <a href={pr.url} target="_blank" rel="noopener noreferrer" className="text-sm text-sky-300 hover:underline">
                  {pr.title}
                </a>
                <span className="ml-2 text-[10px] uppercase text-zinc-600">{pr.state}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
        </div>

        <aside className="space-y-4 xl:sticky xl:top-24 xl:self-start">
          <FounderOsPanel
            accessToken={accessToken}
            founderCredits={founderCredits}
            communityRewardPool={communityRewardPool}
            projectId={projectId}
            onRefresh={() => {
              load();
              onRefresh?.();
            }}
            founderActive={founderActive}
          />
        </aside>
      </div>
    </div>
  );
}
