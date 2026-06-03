'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  copilotAsk,
  EventActivityFeed,
  executeBuildTask,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  fetchEventActivity,
  ProjectMemory,
} from '@/lib/api';
import { useVoiceInput } from '@/hooks/use-voice-input';
import {
  AI_STACK_HREF,
  CopilotSendMode,
  primaryButtonLabel,
  resolveCopilotStack,
} from '@/lib/copilot-ai-stack';

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
  const [feed, setFeed] = useState<EventActivityFeed | null>(null);
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const [defaultProvider, setDefaultProvider] = useState('RULE_BASED');
  const [providers, setProviders] = useState<
    { key: string; label: string; connected: boolean; connectMode?: string }[]
  >([]);
  const [llmConnected, setLlmConnected] = useState(false);
  const [buildWorker, setBuildWorker] = useState('NONE');
  const [workerConnections, setWorkerConnections] = useState<{
    cursor: boolean;
    openHands: boolean;
  }>({ cursor: false, openHands: false });
  const [sendMode, setSendMode] = useState<CopilotSendMode>('ask');

  const onTranscript = useCallback((text: string) => {
    setPrompt(text);
  }, []);

  const { listening, supported, toggle, stop } = useVoiceInput(onTranscript);

  const load = useCallback(async () => {
    try {
      const [activity, mem, builder, worker] = await Promise.all([
        fetchEventActivity(accessToken),
        fetchCopilotMemory(accessToken),
        fetchBuilderSettings(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
      ]);
      setFeed(activity);
      setMemory(mem);
      setDefaultProvider(builder.defaultProvider);
      setProviders(builder.providers);
      const conn = worker?.connections ?? { cursor: false, openHands: false };
      setWorkerConnections({ cursor: conn.cursor, openHands: conn.openHands });
      setBuildWorker(worker?.buildWorker ?? 'NONE');
      setLlmConnected(
        builder.providers.some(
          (p) => p.connectMode === 'api_key' && p.connected && p.key !== 'RULE_BASED',
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

  async function handleAsk(text?: string) {
    const q = (text ?? prompt).trim();
    if (!q || busy) return;
    stop();
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

  async function handleSubmit() {
    const q = prompt.trim();
    if (!q || busy) return;
    const stack = resolveCopilotStack(providers, defaultProvider, buildWorker, workerConnections);
    if (sendMode === 'build' && stack.canBuild) {
      setBusy(true);
      try {
        const result = await executeBuildTask(
          {
            spec: q,
            cursorPrompt: q,
            repository: memory?.repoFullName ?? undefined,
            worker: stack.buildWorker as 'CURSOR' | 'OPENHANDS',
          },
          accessToken,
        );
        const msg =
          result.status === 'dispatched' && result.worker === 'CURSOR'
            ? `**Cursor** started on ${memory?.repoFullName ?? 'your repo'} — open Mission Control chat for live output.`
            : result.status === 'dispatched'
              ? `**${stack.buildLabel}** started — check Mission Control for session details.`
              : result.error ?? result.message ?? 'Run failed';
        setLastAnswer(msg);
        onResult?.(msg);
        setPrompt('');
        load();
      } catch (err) {
        onResult?.(err instanceof Error ? err.message : 'Builder dispatch failed');
      } finally {
        setBusy(false);
      }
      return;
    }
    await handleAsk(q);
  }

  function handleVoiceToggle() {
    if (!supported) {
      onResult?.('Voice not supported in this browser — type instead');
      return;
    }
    toggle(prompt);
  }

  const stats = feed?.weekStats;
  const stack = resolveCopilotStack(providers, defaultProvider, buildWorker, workerConnections);
  const showModeToggle = stack.canAsk && stack.canBuild;
  const buttonLabel = primaryButtonLabel(sendMode, stack);

  return (
    <section className="overflow-hidden rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-950/30 via-zinc-950/80 to-zinc-950 p-5 shadow-lg shadow-violet-950/20 md:p-6 lg:p-8">
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
          <span
            className="max-w-[240px] truncate rounded-full border border-violet-500/40 bg-violet-950/40 px-2.5 py-1 text-[10px] font-medium text-violet-200"
            title={stack.statusLine}
          >
            {stack.statusLine}
          </span>
          <Link href={AI_STACK_HREF} className="text-[10px] text-zinc-500 hover:text-violet-300">
            AI Stack →
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

      <div className="mt-4 rounded-xl border border-zinc-800 bg-black/40 p-3 md:p-4">
        {showModeToggle && (
          <div className="mb-2 flex rounded-lg border border-zinc-800 p-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setSendMode('ask')}
              className={`flex-1 rounded-md px-2 py-1.5 font-medium ${
                sendMode === 'ask' ? 'bg-violet-600 text-white' : 'text-zinc-500'
              }`}
            >
              Ask {stack.askLabel}
            </button>
            <button
              type="button"
              onClick={() => setSendMode('build')}
              className={`flex-1 rounded-md px-2 py-1.5 font-medium ${
                sendMode === 'build' ? 'bg-emerald-700 text-white' : 'text-zinc-500'
              }`}
            >
              Run in {stack.buildLabel}
            </button>
          </div>
        )}
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          placeholder={
            sendMode === 'build' && stack.canBuild
              ? `Tell ${stack.buildLabel} what to implement in your repo…`
              : `Ask ${stack.askLabel} about your project…`
          }
          className="w-full resize-y min-h-[5rem] bg-transparent text-sm md:text-base text-white placeholder:text-zinc-600 outline-none"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800/80 pt-2">
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={handleVoiceToggle}
              title={listening ? 'Stop recording' : 'Voice — stays open while you talk'}
              className={`rounded-lg px-2.5 py-1.5 text-sm ${
                listening
                  ? 'bg-red-600/80 text-white'
                  : 'border border-zinc-700 text-zinc-400 hover:border-violet-500/50 hover:text-white'
              }`}
            >
              {listening ? '⏹' : '🎤'}
            </button>
            {listening && (
              <span className="self-center text-[10px] text-red-300">Listening…</span>
            )}
          </div>
          <div className="flex gap-2">
            {!stack.canAsk && !stack.canBuild ? (
              <Link
                href={AI_STACK_HREF}
                className="rounded-lg border border-violet-500/40 bg-violet-950/30 px-3 py-1.5 text-xs font-medium text-violet-200 hover:bg-violet-950/50"
              >
                Connect AI Stack
              </Link>
            ) : null}
            <button
              type="button"
              disabled={busy || !prompt.trim()}
              onClick={() => void handleSubmit()}
              className={`rounded-lg px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-50 ${
                sendMode === 'build' && stack.canBuild
                  ? 'bg-emerald-600 hover:bg-emerald-500'
                  : 'bg-violet-600 hover:bg-violet-500'
              }`}
            >
              {busy ? '…' : buttonLabel}
            </button>
          </div>
        </div>
      </div>

      {defaultProvider === 'RULE_BASED' && !llmConnected && (
        <p className="mt-2 text-[11px] text-zinc-600">
          Quick Build uses rule-based specs locally. Connect DeepSeek or OpenAI in{' '}
          <Link href={AI_STACK_HREF} className="text-violet-400 hover:underline">
            AI Stack
          </Link>{' '}
          for AI-written specs — or Cursor for cloud agents on your repo.
        </p>
      )}

      {lastAnswer && (
        <div className="mt-5 rounded-xl border border-violet-500/20 bg-zinc-900/60 p-4 md:p-5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-400">Copilot</p>
          <pre className="mt-2 min-h-[12rem] max-h-[28rem] overflow-auto text-sm leading-relaxed text-zinc-200 whitespace-pre-wrap md:min-h-[16rem] md:text-base">
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
