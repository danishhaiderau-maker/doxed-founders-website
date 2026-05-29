'use client';

import { useCallback, useEffect, useState } from 'react';
import { copilotAsk, EventActivityFeed, fetchEventActivity } from '@/lib/api';

type FounderCopilotBarProps = {
  accessToken: string;
  onResult?: (answer: string) => void;
};

export function FounderCopilotBar({ accessToken, onResult }: FounderCopilotBarProps) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [feed, setFeed] = useState<EventActivityFeed | null>(null);
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setFeed(await fetchEventActivity(accessToken));
    } catch {
      setFeed(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAsk() {
    if (!prompt.trim() || busy) return;
    setBusy(true);
    try {
      const result = await copilotAsk(prompt.trim(), accessToken);
      setLastAnswer(result.answer);
      onResult?.(result.answer);
      setPrompt('');
      load();
    } catch (err) {
      onResult?.(err instanceof Error ? err.message : 'Copilot failed');
    } finally {
      setBusy(false);
    }
  }

  const stats = feed?.weekStats;

  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-950/10 p-4">
      <p className="text-sm font-semibold text-amber-200">Founder Copilot</p>
      <p className="mt-1 text-xs text-zinc-500">
        Ask what happened this week — event bus aggregates commits, deploys, community, launch readiness.
      </p>

      {stats && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4">
          <div className="rounded-lg bg-black/30 px-2 py-1.5">
            <span className="text-zinc-500">Commits</span>
            <p className="font-bold text-white">{stats.commits}</p>
          </div>
          <div className="rounded-lg bg-black/30 px-2 py-1.5">
            <span className="text-zinc-500">Deploys</span>
            <p className="font-bold text-white">{stats.deploys}</p>
          </div>
          <div className="rounded-lg bg-black/30 px-2 py-1.5">
            <span className="text-zinc-500">Followers</span>
            <p className="font-bold text-white">{stats.followers}</p>
          </div>
          <div className="rounded-lg bg-black/30 px-2 py-1.5">
            <span className="text-zinc-500">Launch</span>
            <p className="font-bold text-emerald-300">{feed?.launchReadiness ?? 0}%</p>
          </div>
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
          placeholder="What happened this week?"
          className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
        />
        <button
          type="button"
          disabled={busy}
          onClick={handleAsk}
          className="shrink-0 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Ask
        </button>
      </div>

      {lastAnswer && (
        <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-black/40 p-3 text-xs text-zinc-300 whitespace-pre-wrap">
          {lastAnswer}
        </pre>
      )}

      {(feed?.recentEvents?.length ?? 0) > 0 && (
        <ul className="mt-3 space-y-1 border-t border-zinc-800 pt-3">
          <p className="text-[10px] uppercase text-zinc-600">Event bus (live)</p>
          {feed!.recentEvents.slice(0, 5).map((ev) => (
            <li key={ev.id} className="text-[11px] text-zinc-500">
              <span className="text-violet-400">{ev.source}</span> · {ev.title}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
