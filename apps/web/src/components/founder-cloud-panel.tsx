'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { fetchFounderCloudStatus, type FounderCloudStatusResponse } from '@/lib/api';
import { FounderImportWizard } from '@/components/founder-import-wizard';

type Props = {
  accessToken: string;
  showImport?: boolean;
};

export function FounderCloudPanel({ accessToken, showImport }: Props) {
  const [status, setStatus] = useState<FounderCloudStatusResponse | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchFounderCloudStatus(accessToken));
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(id);
  }, [load]);

  const local = status?.localStack;
  const running = Boolean(local?.running);

  return (
    <section className="rounded-xl border border-violet-500/20 bg-violet-950/10 p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-violet-300">Founder Cloud</p>
      <p className="mt-1 text-sm text-zinc-400">
        Personal stack on your PC via Founder Node tray — localhost Mission Control, optional GitHub later.
      </p>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
          <p className="text-[10px] uppercase text-zinc-600">Compute plane</p>
          <p className="text-sm font-medium text-white">{status?.computePlaneMode ?? 'CLOUD'}</p>
        </div>
        <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
          <p className="text-[10px] uppercase text-zinc-600">Local stack</p>
          <p className={`text-sm font-medium ${running ? 'text-emerald-300' : 'text-zinc-400'}`}>
            {running ? 'Running' : local?.enabled ? 'Enabled · stopped' : 'Off'}
          </p>
        </div>
      </div>

      {status?.missionControlUrl ? (
        <a
          href={status.missionControlUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex text-xs font-medium text-violet-300 hover:underline"
        >
          Open local Mission Control →
        </a>
      ) : (
        <p className="mt-3 text-xs text-zinc-500">
          Tray → Enable Founder Cloud mode → Start local stack (requires repo checkout +{' '}
          <code className="text-zinc-400">FOUNDER_CLOUD_REPO</code>).
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          href="/founder-node"
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-[11px] text-zinc-300 hover:text-white"
        >
          Download Founder Node
        </Link>
        <code className="rounded bg-zinc-900 px-2 py-1 text-[10px] text-zinc-500">
          node scripts/founder-local.mjs start
        </code>
      </div>

      {showImport ? (
        <div className="mt-4">
          <FounderImportWizard accessToken={accessToken} onComplete={() => void load()} compact />
        </div>
      ) : null}
    </section>
  );
}
