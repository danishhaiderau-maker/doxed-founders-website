'use client';

import { useCallback, useEffect, useState } from 'react';
import type { WorkspaceActivity } from '@dcf/utils';
import {
  fetchWorkspaceActivity,
  fetchBuilderWorkerStatus,
  fetchDeployIntelligence,
  fetchDesktopBridge,
  fetchActiveAgentRun,
  type DeployIntelligenceResponse,
  type DesktopBridgeResponse,
  type FounderAgentRunRecord,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

type WorkerStatus = {
  buildWorker: string;
  connections: { cursor: boolean; openHands: boolean; founderNode: boolean };
  llmConnected: boolean;
  githubConnected: boolean;
  cursorAgentUrl: string | null;
  latestRunId: string | null;
  cursorAgentId?: string | null;
};

export function DevWorkspace({ accessToken }: Props) {
  const [activity, setActivity] = useState<WorkspaceActivity | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [deploys, setDeploys] = useState<DeployIntelligenceResponse | null>(null);
  const [bridge, setBridge] = useState<DesktopBridgeResponse | null>(null);
  const [activeRun, setActiveRun] = useState<{ run: FounderAgentRunRecord | null; active: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [a, w, d, b, r] = await Promise.allSettled([
        fetchWorkspaceActivity(accessToken),
        fetchBuilderWorkerStatus(accessToken),
        fetchDeployIntelligence(accessToken),
        fetchDesktopBridge(accessToken),
        fetchActiveAgentRun(accessToken),
      ]);
      if (a.status === 'fulfilled') setActivity(a.value);
      if (w.status === 'fulfilled') setWorker(w.value);
      if (d.status === 'fulfilled') setDeploys(d.value);
      if (b.status === 'fulfilled') setBridge(b.value);
      if (r.status === 'fulfilled') setActiveRun(r.value);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspace');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-zinc-500">
        Loading workspace…
      </div>
    );
  }

  const repo = activity?.repoFullName ?? null;
  const branch = bridge?.latest?.branch ?? activity?.defaultBranch ?? 'main';
  const recentCommits = activity?.commitsLast2h ?? [];
  const lastDeploy = deploys?.cards?.[0] ?? null;
  const desktopNode = bridge?.latest ?? null;
  const runActive = activeRun?.active && activeRun.run;
  const connectedCount = [
    worker?.githubConnected,
    worker?.llmConnected,
    worker?.connections?.cursor,
    worker?.connections?.founderNode,
  ].filter(Boolean).length;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-zinc-950 text-zinc-100">
      {/* ── Header bar ── */}
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2.5">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-sm font-semibold text-white">Workspace</span>
          {repo ? (
            <span className="truncate text-xs text-zinc-400 font-mono">{repo}</span>
          ) : (
            <span className="text-xs text-amber-400">No repo connected</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <StatusPill label="Git" ok={worker?.githubConnected ?? false} />
          <StatusPill label="AI" ok={worker?.llmConnected ?? false} />
          <StatusPill label="Cursor" ok={worker?.connections?.cursor ?? false} />
          <StatusPill label="Node" ok={worker?.connections?.founderNode ?? false} />
        </div>
      </div>

      {/* ── Body: two-column layout ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left column: Commits + Agent timeline */}
        <div className="flex-1 overflow-y-auto border-r border-zinc-800">
          {/* Branch + file bar */}
          <div className="flex items-center gap-3 border-b border-zinc-900 px-4 py-2 text-xs text-zinc-400">
            <span className="inline-flex items-center gap-1.5 rounded-md bg-zinc-800/50 px-2 py-1 font-mono text-violet-300">
              <BranchIcon /> {branch}
            </span>
            {desktopNode?.openFilePaths?.length ? (
              <span className="truncate text-zinc-500">
                {desktopNode.openFilePaths.length} file{desktopNode.openFilePaths.length > 1 ? 's' : ''} open
                {desktopNode.openFilePaths[0] ? ` · ${desktopNode.openFilePaths[0].split('/').pop()}` : ''}
              </span>
            ) : null}
            {desktopNode?.taskLabel ? (
              <span className="truncate text-emerald-400/80">{desktopNode.taskLabel}</span>
            ) : null}
          </div>

          {/* Running agent */}
          {runActive ? (
            <div className="border-b border-zinc-900 bg-violet-950/20 px-4 py-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-violet-400" />
                  <span className="text-xs font-semibold text-violet-200">
                    {runActive.worker ?? 'agent'} · {runActive.status}
                  </span>
                  {runActive.adapterLabel && (
                    <span className="text-[10px] text-violet-400/60">{runActive.adapterLabel}</span>
                  )}
                </div>
                <span className="text-[10px] text-zinc-500">
                  {new Date(runActive.startedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
              <p className="mt-1 truncate text-xs text-zinc-400">{runActive.task}</p>
              {runActive.branch && (
                <p className="mt-0.5 text-[10px] font-mono text-violet-400/60">{runActive.branch}</p>
              )}
              {runActive.prUrl && (
                <a
                  href={runActive.prUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-block text-[10px] text-violet-300 underline hover:text-violet-200"
                >
                  View PR →
                </a>
              )}
            </div>
          ) : (
            <div className="border-b border-zinc-900 px-4 py-2 text-xs text-zinc-600">
              No agent running
            </div>
          )}

          {/* Commit timeline */}
          <div className="px-4 py-3">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              Recent commits
            </p>
            {recentCommits.length === 0 ? (
              <p className="text-xs text-zinc-600">No commits in the last 2 hours.</p>
            ) : (
              <div className="space-y-1">
                {recentCommits.slice(0, 20).map((c) => (
                  <CommitRow key={c.sha} sha={c.sha} message={c.message} date={c.date} branch={c.branch} />
                ))}
              </div>
            )}
            {activity && activity.commitsLast24h.length > recentCommits.length && (
              <details className="mt-2">
                <summary className="cursor-pointer text-[10px] text-zinc-600 hover:text-zinc-400">
                  +{activity.commitsLast24h.length - recentCommits.length} more in last 24h
                </summary>
                <div className="mt-1 space-y-1">
                  {activity.commitsLast24h
                    .filter((c) => !recentCommits.some((r) => r.sha === c.sha))
                    .slice(0, 20)
                    .map((c) => (
                      <CommitRow key={c.sha} sha={c.sha} message={c.message} date={c.date} branch={c.branch} />
                    ))}
                </div>
              </details>
            )}
          </div>

          {/* Deploy timeline */}
          {deploys && deploys.cards.length > 0 && (
            <div className="border-t border-zinc-900 px-4 py-3">
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                Deployments
              </p>
              <div className="space-y-2">
                {deploys.cards.slice(0, 5).map((card) => (
                  <div key={card.id} className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-zinc-200">{card.title}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${
                          card.risk === 'low'
                            ? 'bg-emerald-500/20 text-emerald-300'
                            : card.risk === 'medium'
                              ? 'bg-amber-500/20 text-amber-300'
                              : 'bg-red-500/20 text-red-300'
                        }`}
                      >
                        {card.risk}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[10px] text-zinc-500">
                      {card.provider ?? 'unknown'} · {new Date(card.at).toLocaleString()}
                    </p>
                    {card.affectedRoutes.length > 0 && (
                      <p className="mt-1 text-[10px] text-zinc-600">
                        Routes: {card.affectedRoutes.join(', ')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right column: Project Health + Desktop Bridge */}
        <div className="w-72 shrink-0 overflow-y-auto bg-zinc-950/50">
          {/* Project Health */}
          <div className="border-b border-zinc-800 px-3 py-3">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              Project Health
            </p>
            <div className="space-y-1.5">
              <HealthRow label="Repository" ok={worker?.githubConnected ?? false} />
              <HealthRow label="AI Brain" ok={worker?.llmConnected ?? false} />
              <HealthRow label="Cursor" ok={worker?.connections?.cursor ?? false} />
              <HealthRow label="Founder Node" ok={worker?.connections?.founderNode ?? false} />
              <HealthRow label="Last Deploy" ok={Boolean(lastDeploy)} value={lastDeploy ? new Date(lastDeploy.at).toLocaleDateString() : 'never'} />
              <HealthRow label="Agent Active" ok={Boolean(runActive)} value={runActive ? 'running' : 'idle'} />
            </div>
            {connectedCount < 2 && (
              <p className="mt-2 rounded bg-amber-950/30 px-2 py-1.5 text-[10px] text-amber-200/80">
                Connect GitHub + an AI key in Settings → Builder to unlock the full workspace.
              </p>
            )}
          </div>

          {/* Desktop Bridge */}
          {desktopNode && (
            <div className="border-b border-zinc-800 px-3 py-3">
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                Desktop Bridge
              </p>
              <div className="space-y-1.5 text-xs">
                <div className="text-zinc-400">
                  <span className="text-zinc-600">Node:</span> {desktopNode.label}
                </div>
                {desktopNode.branch && (
                  <div className="text-zinc-400">
                    <span className="text-zinc-600">Branch:</span>{' '}
                    <span className="font-mono text-violet-300">{desktopNode.branch}</span>
                  </div>
                )}
                {desktopNode.taskLabel && (
                  <div className="text-zinc-400">
                    <span className="text-zinc-600">Task:</span> {desktopNode.taskLabel}
                  </div>
                )}
                {desktopNode.editSummary && (
                  <div className="text-zinc-400">
                    <span className="text-zinc-600">Edits:</span> {desktopNode.editSummary}
                  </div>
                )}
                {desktopNode.openFilePaths && desktopNode.openFilePaths.length > 0 && (
                  <div>
                    <span className="text-zinc-600">Open files:</span>
                    <ul className="mt-1 space-y-0.5">
                      {desktopNode.openFilePaths.slice(0, 8).map((f) => (
                        <li key={f} className="truncate font-mono text-[10px] text-zinc-500">
                          {f}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="text-[10px] text-zinc-600">
                  Updated {new Date(desktopNode.updatedAt).toLocaleTimeString()}
                </div>
              </div>
            </div>
          )}

          {/* Quick stats */}
          <div className="px-3 py-3">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              Stats
            </p>
            <div className="grid grid-cols-2 gap-2">
              <StatCard label="Commits 24h" value={activity?.commitsLast24h.length ?? 0} />
              <StatCard label="Deploys" value={deploys?.count ?? 0} />
              <StatCard label="Connections" value={connectedCount} />
              <StatCard label="Worker" value={worker?.buildWorker ?? 'none'} />
            </div>
          </div>
        </div>
      </div>

      {error && (
        <div className="border-t border-red-900/30 bg-red-950/20 px-4 py-1.5 text-xs text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}

function StatusPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${
        ok ? 'bg-emerald-500/15 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
      }`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
      {label}
    </span>
  );
}

function HealthRow({ label, ok, value }: { label: string; ok: boolean; value?: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-zinc-400">{label}</span>
      <span className={`font-medium ${ok ? 'text-emerald-300' : 'text-zinc-600'}`}>
        {value ?? (ok ? 'Connected' : 'Offline')}
      </span>
    </div>
  );
}

function CommitRow({
  sha,
  message,
  date,
  branch,
}: {
  sha: string;
  message: string;
  date: string;
  branch?: string;
}) {
  const shortSha = sha.slice(0, 7);
  const firstLine = message.split('\n')[0];
  const time = new Date(date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return (
    <div className="group flex items-start gap-2 rounded px-1.5 py-1 hover:bg-zinc-900/50">
      <span className="mt-0.5 font-mono text-[10px] text-zinc-600 group-hover:text-violet-400">
        {shortSha}
      </span>
      <span className="flex-1 truncate text-xs text-zinc-300">{firstLine}</span>
      {branch && (
        <span className="shrink-0 rounded bg-zinc-800/50 px-1 py-0.5 font-mono text-[9px] text-zinc-500">
          {branch}
        </span>
      )}
      <span className="shrink-0 text-[10px] text-zinc-600">{time}</span>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-2">
      <p className="text-[9px] uppercase tracking-wider text-zinc-600">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-white">{value}</p>
    </div>
  );
}

function BranchIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" className="text-violet-400">
      <path
        d="M5 4.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm9 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0ZM3.5 6v4a2.5 2.5 0 0 0 2.5 2.5h4"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
