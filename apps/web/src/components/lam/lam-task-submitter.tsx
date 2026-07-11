'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import { LamAdaptersStatus } from './lam-adapters-status';
import { LamTaskHistory } from './lam-task-history';
import { STATUS_META, type LamTask } from './types';

type Props = { accessToken: string };

const EXAMPLE_GOALS = [
  'Research the top 5 competitors for an AI-powered personal finance app and summarize their pricing.',
  'Find the 3 most popular open-source CRMs on GitHub and describe what each is best for.',
  'Go to news.ycombinator.com and extract the titles of the top 10 stories.',
];

/**
 * LamTaskSubmitter — the chat-like input where a founder describes a
 * natural-language task and watches it get planned + executed step by step.
 *
 * Flow:
 *   1. Founder types a goal (or picks an example).
 *   2. POST /api/lam/task → returns a task in PLANNING.
 *   3. Poll GET /api/lam/task/:id every 2s until COMPLETED/FAILED.
 *   4. Render: the plan, each step's live status, and the final result.
 *
 * The step log is the centerpiece — it's the "action trace" that makes
 * this a Large Action Model rather than a chatbot. Each step shows its
 * adapter (browser/desktop), status, summary, and artifacts.
 */
export function LamTaskSubmitter({ accessToken }: Props) {
  const [goal, setGoal] = useState('');
  const [activeTask, setActiveTask] = useState<LamTask | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const pollTask = useCallback(
    (taskId: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(apiUrl(`/api/lam/task/${taskId}`), {
            headers: { Authorization: `Bearer ${accessToken}` },
          });
          if (!res.ok) return;
          const t = (await res.json()) as LamTask;
          setActiveTask(t);
          if (t.status === 'COMPLETED' || t.status === 'FAILED') {
            stopPolling();
          }
        } catch {
          // keep polling through transient errors
        }
      }, 2000);
    },
    [accessToken, stopPolling],
  );

  const submit = useCallback(async () => {
    const trimmed = goal.trim();
    if (trimmed.length < 8) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(apiUrl('/api/lam/task'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ goal: trimmed }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { message?: string };
        throw new Error(body.message ?? `${res.status} ${res.statusText}`);
      }
      const task = (await res.json()) as LamTask;
      setActiveTask(task);
      pollTask(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [accessToken, goal, pollTask]);

  const isLoading =
    !!activeTask &&
    (activeTask.status === 'PLANNING' || activeTask.status === 'RUNNING');

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-white">Founder OS Actions</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Describe a task in plain English. The LAM plans it into browser/desktop steps, executes them, and reports back.
            </p>
          </div>
          <LamAdaptersStatus accessToken={accessToken} />
        </div>

        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. Research the top 5 competitors for my idea and summarize their pricing…"
          className="h-24 w-full resize-none rounded-lg border border-zinc-800 bg-black/40 p-3 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-violet-500"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void submit();
            }
          }}
        />

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            onClick={submit}
            disabled={goal.trim().length < 8 || submitting || isLoading}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? 'Starting…' : 'Run task'}
          </button>
          <span className="text-[11px] text-zinc-600">
            {goal.trim().length < 8 ? 'At least 8 characters' : `${goal.trim().length} chars · ⌘/Ctrl+Enter`}
          </span>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {EXAMPLE_GOALS.map((ex, i) => (
            <button
              key={i}
              onClick={() => setGoal(ex)}
              className="rounded-full border border-zinc-800 bg-black/30 px-2.5 py-1 text-[10px] text-zinc-500 transition hover:border-zinc-600 hover:text-zinc-300"
            >
              {ex.slice(0, 52)}
              {ex.length > 52 ? '…' : ''}
            </button>
          ))}
        </div>

        {error && (
          <p className="mt-3 rounded-lg border border-rose-700/40 bg-rose-900/20 p-3 text-xs text-rose-200">
            {error}
          </p>
        )}
      </div>

      {activeTask && <TaskProgressView task={activeTask} />}

      {!isLoading && (
        <LamTaskHistory
          accessToken={accessToken}
          activeId={activeTask?.id}
          onSelect={(t) => setActiveTask(t)}
        />
      )}
    </div>
  );
}

/**
 * The live step-by-step execution view. Renders the plan as it lands,
 * then each step's result as it completes, then the final synthesized
 * answer. This is the "action trace" surface.
 */
function TaskProgressView({ task }: { task: LamTask }) {
  const meta = STATUS_META[task.status] ?? STATUS_META.RUNNING;
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">
              Task
            </div>
            <p className="mt-0.5 truncate text-sm text-zinc-200">{task.goal}</p>
          </div>
          <div className={`shrink-0 text-xs font-semibold uppercase ${meta.color}`}>
            {task.status === 'PLANNING' || task.status === 'RUNNING' ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
                {meta.label}
              </span>
            ) : (
              meta.label
            )}
          </div>
        </div>

        {task.steps.length > 0 && (
          <div className="mt-4">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">
              Plan · {task.steps.length} steps
            </div>
            <ol className="space-y-1.5">
              {task.steps.map((step) => {
                const result = task.results.find((r) => r.index === step.index);
                const status = result?.status;
                const icon =
                  status === 'success'
                    ? '✓'
                    : status === 'failed'
                      ? '✗'
                      : status === 'skipped'
                        ? '–'
                        : '⏳';
                const iconColor =
                  status === 'success'
                    ? 'text-emerald-400'
                    : status === 'failed'
                      ? 'text-rose-400'
                      : status === 'skipped'
                        ? 'text-zinc-600'
                        : 'text-zinc-500';
                return (
                  <li
                    key={step.index}
                    className="flex items-start gap-2.5 rounded-lg border border-zinc-800/60 bg-black/20 p-2.5"
                  >
                    <span className={`mt-0.5 shrink-0 text-xs ${iconColor}`}>{icon}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] uppercase text-zinc-400">
                          {step.adapter === 'computer-use' ? '🖥️ desktop' : '🌐 browser'}
                        </span>
                        <span className="text-[11px] text-zinc-500">Step {step.index}</span>
                      </div>
                      <p className="mt-1 text-xs text-zinc-300">{step.description}</p>
                      {result?.summary && (
                        <p className="mt-1 text-[11px] text-zinc-500">{result.summary}</p>
                      )}
                      {result?.error && (
                        <p className="mt-1 text-[11px] text-rose-400">{result.error}</p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        )}

        {task.status === 'PLANNING' && task.steps.length === 0 && (
          <p className="mt-4 text-xs text-zinc-500">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400 align-middle" />{' '}
            Asking the AI Gateway to plan the steps…
          </p>
        )}

        {task.result && (task.status === 'COMPLETED' || task.status === 'FAILED') && (
          <div className="mt-4 rounded-lg border border-violet-500/20 bg-violet-950/15 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-violet-300">
              Result
            </div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-zinc-200">
              {task.result}
            </p>
          </div>
        )}

        {task.status === 'FAILED' && task.error && !task.result && (
          <p className="mt-3 rounded-lg border border-rose-700/40 bg-rose-900/20 p-3 text-xs text-rose-200">
            {task.error}
          </p>
        )}

        {(task.status === 'COMPLETED' || task.status === 'FAILED') &&
          typeof task.elapsedMs === 'number' && (
            <div className="mt-3 flex items-center gap-3 text-[10px] text-zinc-600">
              <span>⏱ {(task.elapsedMs / 1000).toFixed(1)}s</span>
              {typeof task.costDdollar === 'number' && (
                <span className="rounded bg-amber-950/40 px-1.5 py-0.5 font-semibold uppercase text-amber-300">
                  {task.costDdollar} DD
                </span>
              )}
            </div>
          )}
      </div>
    </div>
  );
}
