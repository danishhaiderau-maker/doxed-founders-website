'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAttestationDashboard,
  scanVaultIntegrity,
  verifyPhalaAttestation,
} from '@/lib/api';

type Dashboard = Awaited<ReturnType<typeof fetchAttestationDashboard>>;

type Props = {
  accessToken: string;
};

export function AttestationDashboardPanel({ accessToken }: Props) {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchAttestationDashboard(accessToken);
      setDashboard(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load attestation dashboard');
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function runVaultScan() {
    setBusy('vault');
    setMsg(null);
    setErr(null);
    try {
      await scanVaultIntegrity(accessToken);
      setMsg('Vault integrity scan recorded');
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Vault scan failed');
    } finally {
      setBusy(null);
    }
  }

  async function verifyLatestPhala(logId?: string) {
    setBusy('phala');
    setMsg(null);
    setErr(null);
    try {
      const result = await verifyPhalaAttestation(accessToken, logId);
      setMsg(result.summary ?? (result.verified ? 'TEE attestation verified' : 'Verification incomplete'));
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Phala verification failed');
    } finally {
      setBusy(null);
    }
  }

  if (!dashboard) {
    return <p className="text-sm text-zinc-500">Loading attestation dashboard…</p>;
  }

  const memory = dashboard.memoryIntegrity;
  const phala = dashboard.phalaTee;
  const scoreColor =
    memory.status === 'healthy'
      ? 'text-emerald-300'
      : memory.status === 'partial'
        ? 'text-amber-200'
        : 'text-zinc-400';

  return (
    <section className="rounded-2xl border border-fuchsia-500/35 bg-fuchsia-950/10 p-6">
      <h2 className="text-lg font-semibold text-white">Attestation dashboard (Step 5)</h2>
      <p className="mt-1 text-sm text-zinc-500">
        Verify Phala TEE inference receipts and Founder Vault memory integrity — cryptographic proof, not trust-me badges.
      </p>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium text-white">Memory integrity</p>
            <span className={`text-sm font-semibold ${scoreColor}`}>{memory.score}%</span>
          </div>
          <ul className="mt-3 space-y-2 text-xs text-zinc-400">
            {memory.checks.map((check) => (
              <li key={check.name} className="flex gap-2">
                <span className={check.ok ? 'text-emerald-400' : 'text-amber-400'}>
                  {check.ok ? '✓' : '○'}
                </span>
                <span>{check.detail}</span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={runVaultScan}
            className="mt-4 rounded-lg border border-fuchsia-500/40 bg-fuchsia-950/30 px-3 py-2 text-sm text-fuchsia-100 disabled:opacity-50"
          >
            Scan vault integrity
          </button>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium text-white">Phala TEE receipts</p>
            <span className="text-xs text-zinc-500">
              {phala.verifiedCount}/{phala.recentCount} verified
            </span>
          </div>
          {phala.latest ? (
            <div className="mt-3 space-y-1 text-xs text-zinc-400">
              <p>
                Latest: {phala.latest.model ?? 'Phala model'}
                {phala.latest.verified ? (
                  <span className="ml-2 text-emerald-400">verified</span>
                ) : (
                  <span className="ml-2 text-amber-300">pending</span>
                )}
              </p>
              {phala.latest.requestId && <p className="truncate">Request {phala.latest.requestId}</p>}
              {phala.latest.signingAddress && (
                <p className="truncate">Signer {phala.latest.signingAddress}</p>
              )}
              <p>{new Date(phala.latest.createdAt).toLocaleString()}</p>
            </div>
          ) : (
            <p className="mt-3 text-xs text-zinc-500">
              No Phala Copilot calls yet — set default provider to Private AI (Phala TEE) and ask Copilot.
            </p>
          )}
          <button
            type="button"
            disabled={Boolean(busy) || !phala.latest}
            onClick={() => verifyLatestPhala(phala.latest?.id)}
            className="mt-4 rounded-lg bg-fuchsia-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Verify latest TEE response
          </button>
          <p className="mt-2 text-[11px] text-zinc-600">
            Fetches Redpill attestation report + optional response signature.{' '}
            <a href={phala.docsUrl} className="text-fuchsia-300 underline" target="_blank" rel="noreferrer">
              Docs
            </a>
          </p>
        </div>
      </div>

      {dashboard.recent.length > 0 && (
        <div className="mt-6">
          <p className="text-sm font-medium text-zinc-300">Recent Phala inferences</p>
          <ul className="mt-2 space-y-2">
            {dashboard.recent.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-xs"
              >
                <span className="text-zinc-400">
                  {row.model ?? 'Phala'} · {new Date(row.createdAt).toLocaleString()}
                </span>
                <span className={row.verified ? 'text-emerald-400' : 'text-amber-300'}>
                  {row.verified ? 'verified' : row.status}
                </span>
                {!row.verified && (
                  <button
                    type="button"
                    disabled={Boolean(busy)}
                    onClick={() => verifyLatestPhala(row.id)}
                    className="text-fuchsia-300 underline disabled:opacity-50"
                  >
                    Verify
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {msg && <p className="mt-4 text-sm text-emerald-300">{msg}</p>}
      {err && <p className="mt-4 text-sm text-red-400">{err}</p>}
    </section>
  );
}
