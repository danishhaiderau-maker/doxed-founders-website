'use client';

import type { BuilderSettings } from '@/lib/api';

type Props = {
  settings: BuilderSettings;
};

export function SealedSecretsPanel({ settings }: Props) {
  const status = settings.secretsStatus;
  if (!status) return null;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
      <p className="text-sm font-medium text-white">Sealed API keys (Sprint 6)</p>
      <p className="mt-1 text-xs text-zinc-500">{status.summary}</p>
      <dl className="mt-3 grid gap-2 text-xs">
        <div className="flex justify-between gap-4">
          <dt className="text-zinc-500">Storage mode</dt>
          <dd className="text-zinc-300">{status.modeLabel}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-zinc-500">Connected credentials</dt>
          <dd className="text-zinc-300">{status.credentialCount}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-zinc-500">Unwrap audits (7d)</dt>
          <dd className="text-zinc-300">{status.recentAccessCount}</dd>
        </div>
        {status.phalaInferenceOnly && (
          <div className="rounded-md border border-emerald-500/25 bg-emerald-950/20 px-2 py-1.5 text-emerald-300">
            Phala key is inference-sealed — only TEE chat and attestation can unwrap it.
          </div>
        )}
      </dl>
      {status.credentials.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-zinc-500">
          {status.credentials.map((c) => (
            <li key={c.provider} className="flex justify-between gap-2">
              <span className="text-zinc-400">{c.provider}</span>
              <span>{c.tierLabel}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[10px] text-zinc-600">
        Raw keys never leave the server encrypted blob. See docs/SECRETS_STORAGE.md.
      </p>
    </div>
  );
}
