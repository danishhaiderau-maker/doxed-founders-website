'use client';

import { useCallback, useEffect, useState } from 'react';
import type { FounderImportJob } from '@dcf/utils';
import { fetchImportStatus, startFounderImport } from '@/lib/api';

type Props = {
  accessToken: string;
  onComplete?: () => void;
  compact?: boolean;
};

const STATUS_ICON: Record<string, string> = {
  pending: '○',
  running: '→',
  done: '✓',
  error: '✗',
  skipped: '–',
};

export function FounderImportWizard({ accessToken, onComplete, compact }: Props) {
  const [job, setJob] = useState<FounderImportJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchImportStatus(accessToken);
      setJob(res.job);
      if (res.complete) onComplete?.();
    } catch {
      setJob(null);
    }
  }, [accessToken, onComplete]);

  useEffect(() => {
    void load();
  }, [load]);

  const runImport = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await startFounderImport(accessToken);
      setJob(res);
      if (res.status === 'complete') onComplete?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`rounded-xl border border-cyan-500/25 bg-gradient-to-br from-cyan-950/20 to-zinc-950/80 ${
        compact ? 'p-4' : 'p-5'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-cyan-400">Import wizard</p>
          <p className="mt-1 text-sm text-zinc-300">
            Pull cloud repo memory and host manifest into Founder Vault — read-only first.
          </p>
        </div>
        {job?.status === 'complete' ? (
          <span className="rounded-full border border-emerald-500/30 bg-emerald-950/40 px-2 py-0.5 text-[10px] text-emerald-300">
            Complete
          </span>
        ) : null}
      </div>

      {job && job.steps.length > 0 ? (
        <ol className="mt-4 space-y-2">
          {job.steps.map((step) => (
            <li key={step.id} className="flex gap-2 text-[11px]">
              <span className="w-4 shrink-0 font-mono text-zinc-500">{STATUS_ICON[step.status] ?? '·'}</span>
              <div>
                <p className="font-medium text-zinc-200">{step.label}</p>
                {step.detail ? <p className="text-zinc-500">{step.detail}</p> : null}
              </div>
            </li>
          ))}
        </ol>
      ) : null}

      {job?.summary ? <p className="mt-3 text-xs text-zinc-400">{job.summary}</p> : null}
      {error ? <p className="mt-2 text-xs text-red-400">{error}</p> : null}

      <button
        type="button"
        disabled={busy || job?.status === 'running'}
        onClick={() => void runImport()}
        className="mt-4 rounded-lg bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
      >
        {busy || job?.status === 'running'
          ? 'Importing…'
          : job?.status === 'complete'
            ? 'Re-run import'
            : 'Start import'}
      </button>
    </div>
  );
}
