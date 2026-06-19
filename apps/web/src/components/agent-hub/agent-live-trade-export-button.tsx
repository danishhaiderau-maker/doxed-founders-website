'use client';

import Link from 'next/link';
import { useState } from 'react';
import { downloadLiveTradeExport } from '@/lib/api';

export function AgentLiveTradeExportButton({
  slug,
  token,
  signedIn = Boolean(token),
  exchangeLabel = 'Bitfinex',
  compact = false,
}: {
  slug: string;
  token?: string;
  signedIn?: boolean;
  exchangeLabel?: string;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(format: 'csv' | 'json') {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await downloadLiveTradeExport(slug, token, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setBusy(false);
    }
  }

  if (!signedIn) {
    return (
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-4 py-3">
        <p className="text-xs font-bold uppercase tracking-widest text-emerald-300">Export trade history</p>
        <p className="mt-1 text-xs text-zinc-400">
          Sign in to download all your real {exchangeLabel} copy trades as CSV or JSON.
        </p>
        <Link
          href={`/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}`)}`}
          className="mt-3 inline-block rounded-lg border border-emerald-500/50 bg-emerald-600/20 px-3 py-1.5 text-xs font-semibold text-emerald-100 transition hover:bg-emerald-600/35"
        >
          Sign in to export
        </Link>
      </div>
    );
  }

  if (!token) return null;

  return (
    <div
      className={`rounded-xl border border-emerald-500/30 bg-emerald-950/20 ${compact ? 'px-3 py-2.5' : 'px-4 py-3'}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-widest text-emerald-300">
            Export trade history
          </p>
          {!compact && (
            <p className="mt-1 text-xs text-zinc-400">
              All real {exchangeLabel} copy trades — timestamps, fills, exits, P&amp;L, order IDs, and event log.
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => handleDownload('csv')}
            className="rounded-lg border border-emerald-500/50 bg-emerald-600/20 px-3 py-1.5 text-xs font-semibold text-emerald-100 transition hover:bg-emerald-600/35 disabled:opacity-50"
          >
            {busy ? 'Exporting…' : 'Download CSV'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => handleDownload('json')}
            className="rounded-lg border border-zinc-600 bg-zinc-900/80 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-zinc-500 hover:text-white disabled:opacity-50"
          >
            JSON
          </button>
        </div>
      </div>
      {error && <p className="mt-2 text-xs text-amber-300">{error}</p>}
    </div>
  );
}
