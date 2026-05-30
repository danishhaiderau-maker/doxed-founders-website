'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  copilotAsk,
  dispatchCursorCloudBuild,
  EventActivityFeed,
  fetchBuilderSettings,
  fetchCopilotMemory,
  fetchEventActivity,
  ProjectMemory,
} from '@/lib/api';

type SpeechRecognitionCtor = new () => {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((ev: { results: { [index: number]: { [index: number]: { transcript: string } } } }) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const QUICK_ACTIONS = [
  { label: 'Continue where I left off', prompt: 'Resume work — what should I finish next?' },
  { label: 'Finish MVP', prompt: 'What is left to finish the MVP?' },
  { label: 'Weekly update', prompt: "Generate this week's update." },
  { label: 'Launch readiness', prompt: 'Create launch readiness report.' },
];

type FounderCopilotBarProps = {
  accessToken: string;
  onResult?: (answer: string) => void;
};

export function FounderCopilotBar({ accessToken, onResult }: FounderCopilotBarProps) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [feed, setFeed] = useState<EventActivityFeed | null>(null);
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const [defaultProvider, setDefaultProvider] = useState('RULE_BASED');
  const [cursorConnected, setCursorConnected] = useState(false);
  const [llmConnected, setLlmConnected] = useState(false);
  const recognitionRef = useRef<InstanceType<NonNullable<ReturnType<typeof getSpeechRecognition>>> | null>(null);

  const load = useCallback(async () => {
    try {
      const [activity, mem, builder] = await Promise.all([
        fetchEventActivity(accessToken),
        fetchCopilotMemory(accessToken),
        fetchBuilderSettings(accessToken),
      ]);
      setFeed(activity);
      setMemory(mem);
      setDefaultProvider(builder.defaultProvider);
      setCursorConnected(builder.providers.some((p) => p.key === 'CURSOR' && p.connected));
      setLlmConnected(
        builder.providers.some(
          (p) => p.needsApiKey && p.connected && p.key !== 'RULE_BASED',
        ),
      );
    } catch {
      setFeed(null);
      setMemory(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => () => recognitionRef.current?.stop(), []);

  async function handleAsk(text?: string) {
    const q = (text ?? prompt).trim();
    if (!q || busy) return;
    setBusy(true);
    try {
      const result = await copilotAsk(q, accessToken);
      setLastAnswer(result.answer);
      onResult?.(result.answer);
      if (!text) setPrompt('');
      load();
    } catch (err) {
      onResult?.(err instanceof Error ? err.message : 'Copilot failed');
    } finally {
      setBusy(false);
    }
  }

  async function handleQuickAction(actionPrompt: string) {
    setPrompt(actionPrompt);
    await handleAsk(actionPrompt);
  }

  async function handleDispatchCursor() {
    if (!prompt.trim() || busy) return;
    setBusy(true);
    try {
      const result = await dispatchCursorCloudBuild(
        { spec: prompt.trim(), cursorPrompt: prompt.trim() },
        accessToken,
      );
      const msg = `Cursor agent ${result.mode === 'follow_up' ? 'resumed' : 'started'} — ${result.agentUrl}`;
      setLastAnswer(msg);
      onResult?.(msg);
      setPrompt('');
    } catch (err) {
      onResult?.(
        err instanceof Error ? err.message : 'Cursor dispatch failed — connect API key in Builder settings',
      );
    } finally {
      setBusy(false);
    }
  }

  function toggleVoice() {
    const Ctor = getSpeechRecognition();
    if (!Ctor) {
      onResult?.('Voice not supported in this browser — type instead');
      return;
    }
    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }
    const rec = new Ctor();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = 'en-US';
    rec.onresult = (ev) => {
      const t = ev.results[0]?.[0]?.transcript ?? '';
      setPrompt((p) => (p ? `${p} ${t}` : t));
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognitionRef.current = rec;
    rec.start();
    setListening(true);
  }

  const stats = feed?.weekStats;
  const providerLabel =
    defaultProvider === 'CURSOR'
      ? 'Cursor Cloud Agents'
      : defaultProvider === 'RULE_BASED'
        ? 'Rule-based (local)'
        : defaultProvider.replace('_', ' ');

  return (
    <section className="overflow-hidden rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-950/30 via-zinc-950/80 to-zinc-950 p-5 shadow-lg shadow-violet-950/20">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-violet-300">
            Founder Copilot
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">
            {memory?.welcomeMessage?.split('\n')[0] ?? 'What shall we build today?'}
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-2.5 py-1 text-[10px] font-medium text-violet-200">
            {providerLabel}
          </span>
          <Link href="/settings/builder" className="text-[10px] text-zinc-500 hover:text-violet-300">
            Builder settings →
          </Link>
        </div>
      </div>

      {memory && (
        <p className="mt-2 text-xs text-zinc-500">
          {memory.project?.name ?? 'Project'} · {memory.progressPercent}% · next:{' '}
          <span className="text-violet-200">{memory.suggestedNextStep}</span>
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {QUICK_ACTIONS.map((a) => (
          <button
            key={a.label}
            type="button"
            disabled={busy}
            onClick={() => handleQuickAction(a.prompt)}
            className="rounded-full border border-zinc-700/80 bg-zinc-900/60 px-3 py-1.5 text-[11px] font-medium text-zinc-300 transition hover:border-violet-500/50 hover:text-white disabled:opacity-50"
          >
            {a.label}
          </button>
        ))}
      </div>

      <div className="mt-4 rounded-xl border border-zinc-800 bg-black/40 p-3">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder="Ask Founder Copilot anything… e.g. What am I working on? Finish the landing page."
          className="w-full resize-none bg-transparent text-sm text-white placeholder:text-zinc-600 outline-none"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800/80 pt-2">
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={toggleVoice}
              title="Voice input"
              className={`rounded-lg px-2.5 py-1.5 text-sm ${
                listening
                  ? 'bg-red-600/80 text-white animate-pulse'
                  : 'border border-zinc-700 text-zinc-400 hover:border-violet-500/50 hover:text-white'
              }`}
            >
              🎤
            </button>
            {cursorConnected && (
              <span className="rounded-lg border border-emerald-500/30 px-2 py-1 text-[10px] text-emerald-300">
                Cursor agent ready
              </span>
            )}
            {llmConnected && defaultProvider !== 'CURSOR' && (
              <span className="rounded-lg border border-sky-500/30 px-2 py-1 text-[10px] text-sky-300">
                LLM specs enabled
              </span>
            )}
            {!cursorConnected && !llmConnected && (
              <span className="rounded-lg border border-zinc-700 px-2 py-1 text-[10px] text-zinc-500">
                Connect LLM or Cursor in Builder settings for smarter replies
              </span>
            )}
          </div>
          <div className="flex gap-2">
            {cursorConnected && defaultProvider === 'CURSOR' && (
              <button
                type="button"
                disabled={busy || !prompt.trim()}
                onClick={handleDispatchCursor}
                className="rounded-lg border border-emerald-500/40 bg-emerald-950/30 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-900/40 disabled:opacity-50"
              >
                Run on Cursor
              </button>
            )}
            <button
              type="button"
              disabled={busy || !prompt.trim()}
              onClick={() => handleAsk()}
              className="rounded-lg bg-violet-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
            >
              {busy ? '…' : 'Send'}
            </button>
          </div>
        </div>
      </div>

      {defaultProvider === 'RULE_BASED' && !llmConnected && (
        <p className="mt-2 text-[11px] text-zinc-600">
          Quick Build uses rule-based specs locally. Connect DeepSeek or OpenAI in{' '}
          <Link href="/settings/builder" className="text-violet-400 hover:underline">
            Builder settings
          </Link>{' '}
          for AI-written specs — or Cursor for cloud agents on your repo.
        </p>
      )}

      {lastAnswer && (
        <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <p className="text-[10px] uppercase text-zinc-600">Copilot</p>
          <pre className="mt-1 max-h-40 overflow-auto text-xs text-zinc-300 whitespace-pre-wrap">
            {lastAnswer}
          </pre>
        </div>
      )}

      {stats && (
        <div className="mt-4 grid grid-cols-2 gap-2 border-t border-zinc-800/80 pt-4 sm:grid-cols-3 lg:grid-cols-6">
          {[
            { label: 'Commits', value: stats.commits, color: 'text-violet-300' },
            { label: 'Open tasks', value: memory?.openTasks?.filter((t) => !t.done).length ?? 0, color: 'text-sky-300' },
            { label: 'Deploys', value: stats.deploys, color: 'text-amber-300' },
            { label: 'Followers', value: stats.followers, color: 'text-white' },
            {
              label: 'Raise Room',
              value: memory?.raiseStatus
                ? `$${Math.round(memory.raiseStatus.allocatedUsd).toLocaleString()} Ddollar`
                : '—',
              color: 'text-emerald-300',
            },
            { label: 'Launch', value: `${feed?.launchReadiness ?? 0}%`, color: 'text-emerald-300' },
          ].map((s) => (
            <div key={s.label} className="rounded-lg bg-black/30 px-2 py-2 text-center">
              <p className="text-[9px] uppercase tracking-wider text-zinc-600">{s.label}</p>
              <p className={`text-sm font-bold ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      {(feed?.recentEvents?.length ?? 0) > 0 && (
        <ul className="mt-3 space-y-1">
          <p className="text-[10px] uppercase text-zinc-600">Recent activity</p>
          {feed!.recentEvents.slice(0, 4).map((ev) => (
            <li key={ev.id} className="text-[11px] text-zinc-500">
              <span className="text-violet-400">{ev.source}</span> · {ev.title}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
