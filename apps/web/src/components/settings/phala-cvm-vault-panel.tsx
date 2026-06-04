'use client';

import { useCallback, useEffect, useState } from 'react';
import type { PhalaCvmVaultStatusPayload } from '@dcf/utils';
import {
  fetchVaultCvmStatus,
  requestVaultCvmBackup,
  verifyVaultCvmBackup,
} from '@/lib/api';

type Props = {
  accessToken: string;
  embedded?: boolean;
};

const BACKUP_STATE_LABELS: Record<string, string> = {
  idle: 'Ready for backup',
  pending: 'Backup in progress',
  recorded: 'Backup recorded',
  verified: 'TEE verified',
  failed: 'Backup failed',
  unavailable: 'CVM not configured',
};

export function PhalaCvmVaultPanel({ accessToken, embedded }: Props) {
  const [status, setStatus] = useState<PhalaCvmVaultStatusPayload | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchVaultCvmStatus(accessToken);
      setStatus(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load CVM vault status');
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runBackup() {
    setBusy('backup');
    setMsg(null);
    setErr(null);
    try {
      const result = await requestVaultCvmBackup(accessToken);
      setMsg(
        result.summary ??
          (result.platformCvmPushed
            ? 'Encrypted relay backed up to Phala CVM'
            : 'Local relay snapshot saved — configure PHALA_CVM_BACKUP_URL for full CVM'),
      );
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'CVM backup request failed');
    } finally {
      setBusy(null);
    }
  }

  async function runVerify() {
    setBusy('verify');
    setMsg(null);
    setErr(null);
    try {
      const result = await verifyVaultCvmBackup(accessToken, status?.lastBackup?.id);
      setMsg(result.summary ?? (result.verified ? 'CVM backup TEE verified' : 'Verification incomplete'));
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'CVM verify failed');
    } finally {
      setBusy(null);
    }
  }

  if (!status) {
    return <p className="text-sm text-zinc-500">Loading Phala CVM vault backup…</p>;
  }

  const modeLabel =
    status.mode === 'cvm_enabled'
      ? 'Phala CVM enabled'
      : status.mode === 'local_relay_only'
        ? 'Local relay (CVM optional)'
        : 'Configure vault relay';

  const stateLabel = BACKUP_STATE_LABELS[status.backupState] ?? status.backupState;

  const body = (
    <>
      {!embedded && (
        <>
          <h3 className="text-base font-semibold text-white">Phala CVM vault backup (P1)</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Sealed backup of your encrypted vault relay into a Phala Confidential VM — metadata and blob hash only;
            plaintext vault files never leave Founder Node.
          </p>
        </>
      )}

      <div className={`rounded-xl border border-violet-500/25 bg-violet-950/15 p-4 ${embedded ? '' : 'mt-4'}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-medium text-violet-100">{modeLabel}</p>
          <span className="text-xs text-zinc-500">{stateLabel}</span>
        </div>

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

        {status.relay.blobHashPrefix && (
          <p className="mt-2 text-[11px] text-zinc-600">
            Relay hash prefix {status.relay.blobHashPrefix}… · last sync{' '}
            {status.relay.lastSyncedAt
              ? new Date(status.relay.lastSyncedAt).toLocaleString()
              : 'never'}
          </p>
        )}

        {status.lastBackup && (
          <p className="mt-2 text-[11px] text-zinc-500">
            Last backup: {status.lastBackup.summary ?? status.lastBackup.status} ·{' '}
            {new Date(status.lastBackup.createdAt).toLocaleString()}
            {status.lastBackup.verified && (
              <span className="ml-1 text-emerald-400">verified</span>
            )}
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={Boolean(busy) || !status.canRequestBackup}
            onClick={runBackup}
            className="rounded-lg bg-violet-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Request CVM backup
          </button>
          <button
            type="button"
            disabled={Boolean(busy) || !status.lastBackup}
            onClick={runVerify}
            className="rounded-lg border border-violet-500/40 px-3 py-2 text-sm text-violet-100 disabled:opacity-50"
          >
            Verify CVM receipt
          </button>
        </div>

        <p className="mt-2 text-[11px] text-zinc-600">
          Platform CVM: {status.platformCvmAvailable ? 'configured' : 'not set on API'} ·{' '}
          <a href={status.docsUrl} className="text-violet-300 underline" target="_blank" rel="noreferrer">
            Phala Cloud docs
          </a>
        </p>
      </div>

      {msg && <p className="mt-3 text-sm text-emerald-300">{msg}</p>}
      {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
    </>
  );

  if (embedded) return <div>{body}</div>;

  return (
    <section className="rounded-2xl border border-violet-500/30 bg-violet-950/10 p-6">{body}</section>
  );
}
