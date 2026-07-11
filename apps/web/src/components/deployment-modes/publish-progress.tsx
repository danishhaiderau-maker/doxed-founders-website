'use client';

import { useEffect, useRef, useState } from 'react';
import { cn, PUBLISH_STEPS } from '@dcf/utils';
import { fetchDeploymentMode, type DeploymentModeState } from '@/lib/api';

/**
 * Live 4-step publish progress (doc §5). Polls the deployment-mode endpoint
 * every few seconds while the latest job is PENDING or RUNNING, then stops
 * once it reaches a terminal state (COMPLETED / FAILED / CANCELLED).
 */
export function PublishProgress({
  slug,
  token,
  /** Job id to track. If omitted, tracks the latest job on the project. */
  jobId,
  onCompleted,
  onFailed,
}: {
  slug: string;
  token?: string;
  jobId?: string;
  onCompleted?: (liveUrl: string | null) => void;
  onFailed?: (errorMessage: string | null) => void;
}) {
  const [state, setState] = useState<DeploymentModeState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const next = await fetchDeploymentMode(slug, token);
        if (cancelled) return;
        setState(next);
        setError(null);

        const job = matchJob(next, jobId);
        if (job && job.status === 'COMPLETED') {
          stopPolling();
          onCompleted?.(job.liveUrl);
        } else if (job && (job.status === 'FAILED' || job.status === 'CANCELLED')) {
          stopPolling();
          onFailed?.(job.errorMessage);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Could not reach publish status');
      }
    }

    function stopPolling() {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }

    void poll();
    timerRef.current = setInterval(poll, 3000);

    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [slug, token, jobId, onCompleted, onFailed]);

  const job = state ? matchJob(state, jobId) : null;

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white">
          Publishing {slug} to the public cloud
        </h3>
        {job && <StatusPill status={job.status} />}
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 text-sm text-red-200">
          {error}
        </p>
      )}

      <ol className="mt-5 space-y-4">
        {PUBLISH_STEPS.map((stepDef) => {
          const stepState = job?.steps.find((s) => s.step === stepDef.step) ?? null;
          const status = stepState?.status ?? 'pending';
          return (
            <li key={stepDef.step} className="flex items-start gap-3">
              <StepIcon status={status} />
              <div className="flex-1">
                <p
                  className={cn(
                    'text-sm font-medium',
                    status === 'complete' && 'text-emerald-300',
                    status === 'running' && 'text-white',
                    status === 'failed' && 'text-red-300',
                    status === 'pending' && 'text-zinc-500',
                  )}
                >
                  Step {stepDef.step} of 4 — {stepDef.label}
                </p>
                {stepState?.detail && (
                  <p className="mt-0.5 text-xs text-zinc-400">{stepState.detail}</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="mt-5 border-t border-zinc-800 pt-4 text-sm text-zinc-400">
        Your code, history, and data all moved with you. Nothing was rewritten.
      </div>

      {job?.status === 'COMPLETED' && job.liveUrl && (
        <a
          href={job.liveUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-block rounded-lg border border-emerald-500/50 px-4 py-2 text-sm text-emerald-200 hover:bg-emerald-900/40"
        >
          Open {job.liveUrl} ↗
        </a>
      )}
      {job?.status === 'FAILED' && (
        <p className="mt-3 rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 text-sm text-red-200">
          {job.errorMessage ?? 'Publish failed. Your local project is untouched — try again anytime.'}
        </p>
      )}
    </div>
  );
}

function matchJob(state: DeploymentModeState, jobId?: string) {
  const job = state.latestPublishJob;
  if (!job) return null;
  if (jobId && job.id !== jobId) return null;
  return job;
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    PENDING: 'border-zinc-600 text-zinc-300',
    RUNNING: 'border-cyan-500/50 text-cyan-200',
    COMPLETED: 'border-emerald-500/50 text-emerald-200',
    FAILED: 'border-red-500/50 text-red-200',
    CANCELLED: 'border-amber-500/50 text-amber-200',
  };
  return (
    <span
      className={cn(
        'rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase',
        map[status] ?? map.PENDING,
      )}
    >
      {status}
    </span>
  );
}

function StepIcon({ status }: { status: string }) {
  if (status === 'complete') {
    return <span className="mt-0.5 text-emerald-400">✓</span>;
  }
  if (status === 'running') {
    return (
      <span className="mt-0.5 inline-block h-3 w-3 animate-pulse rounded-full bg-cyan-400" />
    );
  }
  if (status === 'failed') {
    return <span className="mt-0.5 text-red-400">✕</span>;
  }
  return <span className="mt-0.5 text-zinc-600">○</span>;
}
