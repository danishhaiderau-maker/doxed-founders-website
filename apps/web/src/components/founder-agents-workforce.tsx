'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { WORKFORCE_TEMPLATES, buildCopilotAgentDeepLink } from '@dcf/utils';
import {
  BuildRoomData,
  fetchBuilderWorkerStatus,
  ProjectRoom,
} from '@/lib/api';

const WORKFORCE_AGENTS = [
  { key: 'BUILDER', label: 'Builder Agent', icon: '⚙' },
  { key: 'RESEARCHER', label: 'Research Agent', icon: '🔍' },
  { key: 'MARKETER', label: 'Marketing Agent', icon: '📢' },
  { key: 'LAUNCH', label: 'Launch Agent', icon: '🚀' },
  { key: 'COMMUNITY_MANAGER', label: 'Community Agent', icon: '💬' },
];

type FounderAgentsWorkforceProps = {
  accessToken: string;
  room: ProjectRoom | null;
  buildRoom: BuildRoomData | null;
  onTabChange?: (tab: 'activity') => void;
};

export function FounderAgentsWorkforce({
  accessToken,
  room,
  buildRoom,
  onTabChange,
}: FounderAgentsWorkforceProps) {
  const [workerStatus, setWorkerStatus] = useState<Awaited<
    ReturnType<typeof fetchBuilderWorkerStatus>
  > | null>(null);

  const load = useCallback(async () => {
    try {
      setWorkerStatus(await fetchBuilderWorkerStatus(accessToken));
    } catch {
      setWorkerStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const openTask = buildRoom?.grouped.tasks.find((t) => t.status !== 'DONE');
  const builderWorking = Boolean(workerStatus?.cursorAgentUrl && openTask);
  const latestPr = buildRoom?.pullRequests?.[0];
  const latestCommit = buildRoom?.commits[0];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
          Agent Workforce
        </p>
        <h1 className="mt-1 text-2xl font-bold text-white">Your agents at work</h1>
        <p className="mt-1 text-sm text-zinc-500">Status and outcomes — configuration lives in Settings.</p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <article className="rounded-2xl border border-violet-500/30 bg-violet-950/15 p-5 sm:col-span-2">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-violet-300">Builder Agent</p>
              <p className="mt-1 text-lg font-semibold text-white">
                {builderWorking ? 'Working' : workerStatus?.buildWorker !== 'NONE' ? 'Ready' : 'Needs setup'}
              </p>
              {openTask && (
                <p className="mt-2 text-sm text-zinc-400">
                  Task: <span className="text-zinc-200">{openTask.title.slice(0, 64)}</span>
                </p>
              )}
              {latestPr && (
                <p className="mt-1 text-xs text-zinc-500">
                  PR #{latestPr.number} · {latestPr.state}
                </p>
              )}
              {latestCommit && (
                <p className="mt-1 text-xs text-zinc-500">
                  Last commit: {latestCommit.message.split('\n')[0].slice(0, 56)}
                </p>
              )}
            </div>
            <span
              className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${
                builderWorking
                  ? 'bg-emerald-500/20 text-emerald-300'
                  : workerStatus?.buildWorker !== 'NONE'
                    ? 'bg-violet-500/20 text-violet-200'
                    : 'bg-zinc-800 text-zinc-500'
              }`}
            >
              {builderWorking ? 'Running' : workerStatus?.buildWorker !== 'NONE' ? 'Online' : 'Offline'}
            </span>
          </div>
          {workerStatus?.cursorAgentUrl && (
            <a
              href={workerStatus.cursorAgentUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-4 inline-block text-xs text-violet-400 hover:underline"
            >
              View agent run →
            </a>
          )}
          {workerStatus?.buildWorker === 'NONE' && (
            <Link href="/founder-den?tab=analytics" className="mt-4 inline-block text-xs text-amber-400 hover:underline">
              Connect builder in Settings →
            </Link>
          )}
        </article>

        {WORKFORCE_AGENTS.filter((a) => a.key !== 'BUILDER').map((agent) => {
          const template = WORKFORCE_TEMPLATES.find((t) => t.key === agent.key);
          return (
            <article
              key={agent.key}
              className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4"
            >
              <div className="flex items-center gap-2">
                <span aria-hidden>{agent.icon}</span>
                <p className="text-sm font-semibold text-white">{agent.label}</p>
              </div>
              <p className="mt-2 text-xs text-zinc-500">{template?.description ?? 'Specialized worker'}</p>
              <Link
                href={buildCopilotAgentDeepLink(agent.key, room?.name)}
                className="mt-3 inline-block text-[11px] text-violet-400 hover:underline"
              >
                Run via Copilot →
              </Link>
            </article>
          );
        })}
      </div>

      {onTabChange && (
        <button
          type="button"
          onClick={() => onTabChange('activity')}
          className="w-full rounded-xl border border-zinc-700 py-3 text-sm text-zinc-300 hover:border-violet-500/50 hover:text-white"
        >
          ← Back to Mission Control
        </button>
      )}
    </div>
  );
}
