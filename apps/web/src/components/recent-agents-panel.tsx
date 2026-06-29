'use client';

import { useState } from 'react';
import type { RecentAgent, RecentAgentsResponse } from '@/lib/api';

const STATUS_TONE: Record<string, string> = {
  running: 'text-violet-300 bg-violet-500/10',
  idle: 'text-zinc-400 bg-zinc-700/30',
  waiting: 'text-amber-300 bg-amber-500/10',
  completed: 'text-zinc-500 bg-zinc-800/40',
  offline: 'text-zinc-600 bg-zinc-800/40',
  dispatched: 'text-violet-300 bg-violet-500/10',
};

function toneFor(status: string): string {
  const key = status.toLowerCase();
  for (const k of Object.keys(STATUS_TONE)) {
    if (key.includes(k)) return STATUS_TONE[k];
  }
  return 'text-zinc-400 bg-zinc-700/30';
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}

const SOURCE_LABEL: Record<RecentAgent['source'], string> = {
  live_desktop: 'Live desktop',
  dispatched_run: 'Dispatched from Founder OS',
  cursor_history: 'Recent Cursor session',
};

export function RecentAgentsPanel({
  data,
  onContinueAgent,
  compact = false,
}: {
  data: RecentAgentsResponse | null;
  onContinueAgent: (agent: RecentAgent, prompt: string) => void;
  compact?: boolean;
}) {
  const [composerAgentId, setComposerAgentId] = useState<string | null>(null);
  const [composerText, setComposerText] = useState('');

  const agents = data?.agents ?? [];
  const liveLabel = data?.liveCursorAgentsAvailable
    ? 'Live desktop agents'
    : 'Recently dispatched from Founder OS';

  if (agents.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">
          Resume Desktop
        </p>
        <p className="mt-2 text-xs text-zinc-500">
          No recent agents yet. Dispatch a Cursor agent from Founder OS or connect Founder Node to see
          your live desktop sessions here.
        </p>
      </div>
    );
  }

  // Flatten the most recent messages across agents (max 5), newest first.
  const recentMessages = agents
    .flatMap((a) => (a.recentMessages ?? []).map((m) => ({ ...m, agent: a })))
    .sort((a, b) => {
      const ta = a.at ? new Date(a.at).getTime() : 0;
      const tb = b.at ? new Date(b.at).getTime() : 0;
      return tb - ta;
    })
    .slice(0, 5);

  function submitComposer(agent: RecentAgent) {
    const prompt = composerText.trim();
    if (!prompt) return;
    onContinueAgent(agent, prompt);
    setComposerText('');
    setComposerAgentId(null);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-violet-400/80">
            Resume Desktop
          </p>
          <p className="mt-0.5 text-[10px] text-zinc-600">{liveLabel}</p>
        </div>
        <div className="flex items-center gap-1.5 text-[9px]">
          <span className={`h-1.5 w-1.5 rounded-full ${data?.desktopOnline ? 'bg-emerald-400' : 'bg-zinc-700'}`} />
          <span className="text-zinc-500">{data?.desktopOnline ? 'Desktop online' : 'Desktop offline'}</span>
        </div>
      </div>

      <div className={compact ? 'space-y-1.5' : 'space-y-2'}>
        {agents.map((agent) => (
          <div
            key={agent.id}
            className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-2.5 transition hover:border-violet-500/30"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-semibold text-zinc-200">{agent.label}</p>
                <p className="mt-0.5 text-[9px] text-zinc-600">
                  {SOURCE_LABEL[agent.source]}
                  {agent.repository ? ` · ${agent.repository}` : ''}
                  {agent.branch ? ` · ${agent.branch}` : ''}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <span className={`rounded px-1.5 py-0.5 text-[8px] font-semibold uppercase tracking-wider ${toneFor(agent.status)}`}>
                  {agent.status}
                </span>
                <span className="text-[9px] text-zinc-600">{relativeTime(agent.lastActivityAt)}</span>
              </div>
            </div>

            {(agent.lastUserPrompt || agent.lastAssistantSnippet) && (
              <div className="mt-2 space-y-1 border-l border-zinc-800 pl-2">
                {agent.lastUserPrompt && (
                  <p className="text-[10px] text-zinc-400">
                    <span className="text-zinc-600">You: </span>
                    {agent.lastUserPrompt}
                  </p>
                )}
                {agent.lastAssistantSnippet && (
                  <p className="text-[10px] text-zinc-500">
                    <span className="text-zinc-600">Agent: </span>
                    {agent.lastAssistantSnippet}
                  </p>
                )}
              </div>
            )}

            {composerAgentId === agent.id ? (
              <div className="mt-2 flex items-center gap-1.5">
                <input
                  autoFocus
                  value={composerText}
                  onChange={(e) => setComposerText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') submitComposer(agent);
                    if (e.key === 'Escape') {
                      setComposerAgentId(null);
                      setComposerText('');
                    }
                  }}
                  placeholder="Continue this agent — type a follow-up…"
                  className="flex-1 rounded-md border border-violet-500/40 bg-zinc-950/60 px-2 py-1.5 text-[11px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-violet-400"
                />
                <button
                  type="button"
                  onClick={() => submitComposer(agent)}
                  className="rounded-md bg-violet-600 px-2.5 py-1.5 text-[10px] font-semibold text-white transition hover:bg-violet-500"
                >
                  Send
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setComposerAgentId(null);
                    setComposerText('');
                  }}
                  className="rounded-md border border-zinc-700 px-2 py-1.5 text-[10px] text-zinc-400 transition hover:text-zinc-200"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setComposerAgentId(agent.id);
                  setComposerText('');
                }}
                className="mt-2 text-[10px] font-medium text-violet-400/80 transition hover:text-violet-300"
              >
                Continue this agent →
              </button>
            )}
          </div>
        ))}
      </div>

      {recentMessages.length > 0 && !compact && (
        <div className="rounded-lg border border-zinc-800/70 bg-zinc-950/40 p-2.5">
          <p className="mb-1.5 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
            Recent messages across agents
          </p>
          <div className="space-y-1.5">
            {recentMessages.map((m, i) => (
              <div key={i} className="flex items-start gap-2">
                <span
                  className={`mt-0.5 shrink-0 rounded px-1 py-0.5 text-[8px] font-semibold uppercase ${
                    m.role === 'user' ? 'bg-violet-500/15 text-violet-300' : 'bg-zinc-700/40 text-zinc-400'
                  }`}
                >
                  {m.role}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[10px] text-zinc-400">{m.text}</p>
                  <p className="text-[8px] text-zinc-600">
                    {m.agent.label} · {relativeTime(m.at)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
