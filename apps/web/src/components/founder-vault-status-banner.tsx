'use client';

import Link from 'next/link';
import type { VaultRelaySummary } from '@dcf/utils';

type Props = {
  relay: VaultRelaySummary | null | undefined;
  memoryStorageMode?: string;
  compact?: boolean;
};

export function FounderVaultStatusBanner({ relay, memoryStorageMode, compact }: Props) {
  if (!relay && memoryStorageMode !== 'FOUNDER_NODE' && memoryStorageMode !== 'LOCAL_SYNC') {
    return null;
  }

  if (memoryStorageMode === 'LOCAL_SYNC' && !relay) {
    return (
      <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
        Local + encrypted relay — memory stays on this device first.
      </div>
    );
  }

  if (!relay) {
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-950/25 px-3 py-2 text-xs text-amber-100">
        Founder Vault selected —{' '}
        <Link href="/settings/builder" className="underline">
          pair Founder Node
        </Link>{' '}
        to sync encrypted memory from your machine.
      </div>
    );
  }

  const isNode = relay.mode === 'FOUNDER_NODE';
  const online = !isNode || relay.nodeOnline;
  const border = online ? 'border-cyan-500/35 bg-cyan-950/20 text-cyan-100' : 'border-amber-500/40 bg-amber-950/25 text-amber-100';

  return (
    <div className={`rounded-lg border px-3 py-2 text-xs ${border}`}>
      <p className="font-semibold">
        {isNode ? 'Founder Vault' : 'Local vault relay'}
        {isNode && relay.nodeLabel ? ` · ${relay.nodeLabel}` : ''}
        {isNode && (
          <span className="ml-2 font-normal opacity-80">
            {relay.nodeOnline ? '● online' : '○ offline'}
          </span>
        )}
      </p>
      {!compact && (
        <ul className="mt-1.5 list-inside list-disc space-y-0.5 opacity-90">
          {relay.hasEncryptedBlob && (
            <li>Encrypted snapshot relayed — server cannot read private notes or full task bodies</li>
          )}
          {relay.tasksRemaining > 0 && (
            <li>{relay.tasksRemaining} open task(s) in vault (details on your machine)</li>
          )}
          {relay.lastSyncedAt && (
            <li>Last sync {new Date(relay.lastSyncedAt).toLocaleString()}</li>
          )}
          {!relay.nodeOnline && isNode && (
            <li>Open Founder Node on your desktop to refresh Copilot context</li>
          )}
        </ul>
      )}
    </div>
  );
}
