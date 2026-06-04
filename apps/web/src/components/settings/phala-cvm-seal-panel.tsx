'use client';

import { useCallback, useEffect, useState } from 'react';
import type { PhalaCvmSealStatusPayload } from '@dcf/utils';
import { fetchVaultCvmSealStatus } from '@/lib/api';

type Props = {
  accessToken: string;
  embedded?: boolean;
  cvmUnwrapReadyFromSettings?: boolean;
  activeUnwrapPathLabel?: string;
};

export function PhalaCvmSealPanel({
  accessToken,
  embedded,
  cvmUnwrapReadyFromSettings,
  activeUnwrapPathLabel,
}: Props) {
  const [status, setStatus] = useState<PhalaCvmSealStatusPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchVaultCvmSealStatus(accessToken);
      setStatus(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load CVM seal status');
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const ready = status?.cvmUnwrapReady ?? cvmUnwrapReadyFromSettings ?? false;
  const pathLabel =
    status?.activeUnwrapPath === 'cvm_sealed'
      ? 'Phala CVM (TEE unwrap)'
      : activeUnwrapPathLabel ?? 'Platform AES-256 (Neon relay)';

  const body = (
    <div className={`rounded-xl border border-emerald-500/25 bg-emerald-950/15 p-4 ${embedded ? '' : 'mt-4'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium text-emerald-100">
          {ready ? 'CVM credential unwrap ready' : 'CVM unwrap (optional P2)'}
        </p>
        <span className="text-xs text-zinc-500">{pathLabel}</span>
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        API keys stay encrypted in Neon; unwrap runs in Phala CVM when configured, with audited fallback to
        platform AES. Raw keys never reach the browser.
      </p>
      {status?.checks && (
        <ul className="mt-3 space-y-2 text-xs text-zinc-400">
          {status.checks.map((check) => (
            <li key={check.name} className="flex gap-2">
              <span className={check.ok ? 'text-emerald-400' : 'text-amber-400'}>
                {check.ok ? '✓' : '○'}
              </span>
              <span>{check.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {status?.docsUrl && (
        <p className="mt-2 text-[11px] text-zinc-600">
          <a href={status.docsUrl} className="text-emerald-300 underline" target="_blank" rel="noreferrer">
            Phala Cloud docs
          </a>
        </p>
      )}
      {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
    </div>
  );

  if (embedded) return <div>{body}</div>;

  return (
    <section className="rounded-2xl border border-emerald-500/30 bg-emerald-950/10 p-6">
      <h3 className="text-base font-semibold text-white">Phala CVM credential unwrap (P2)</h3>
      <p className="mt-1 text-sm text-zinc-500">
        Platform integration keys can unwrap inside a Confidential VM instead of only on the API host.
      </p>
      {body}
    </section>
  );
}
