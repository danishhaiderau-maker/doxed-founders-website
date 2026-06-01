'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAttestationDashboard,
  scanVaultIntegrity,
  verifyPhalaAttestation,
} from '@/lib/api';

type Dashboard = Awaited<ReturnType<typeof fetchAttestationDashboard>>;

const CHECK_NEXT_STEPS: Record<string, { title: string; detail: string }> = {
  memory_mode: {
    title: 'Enable Founder Vault',
    detail: 'In Step 1 above, select Founder Vault (Founder Node) as your memory mode.',
  },
  encrypted_relay: {
    title: 'Wait for encrypted backup sync',
    detail:
      'Keep Founder Node open for a few minutes — it pushes an encrypted vault blob the server cannot read. This happens automatically after pairing; no button needed.',
  },
  founder_node_online: {
    title: 'Open Founder Node on your desktop',
    detail: 'Launch the tray app from Step 1 and leave it running while you complete the steps below.',
  },
  vector_index: {
    title: 'Build your local search index',
    detail: 'In Step 4, click Rebuild vector index while Founder Node is online.',
  },
};

type Props = {
  accessToken: string;
  embedded?: boolean;
};

export function AttestationDashboardPanel({ accessToken, embedded }: Props) {
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
  const passedChecks = memory.checks.filter((c) => c.ok).length;
  const totalChecks = memory.checks.length;
  const pendingChecks = memory.checks.filter((c) => !c.ok);
  const scoreColor =
    memory.status === 'healthy'
      ? 'text-emerald-300'
      : memory.status === 'partial'
        ? 'text-amber-200'
        : 'text-zinc-400';

  const body = (
    <>
      {!embedded && (
        <>
          <h2 className="text-lg font-semibold text-white">Attestation dashboard (Step 5)</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Verify Phala TEE inference receipts and Founder Vault memory integrity — cryptographic proof, not trust-me badges.
          </p>
        </>
      )}

      {memory.score < 100 && (
        <div className={`rounded-xl border border-amber-500/30 bg-amber-950/20 p-4 ${embedded ? '' : 'mt-4'}`}>
          <p className="text-sm font-medium text-amber-100">
            {memory.score}% — {passedChecks} of {totalChecks} privacy checks complete
          </p>
          <p className="mt-1 text-xs text-zinc-400">
            Pairing is only the start. Finish the numbered steps above to reach 100%, then verify Phala TEE receipts below.
          </p>
          {pendingChecks.length > 0 && (
            <ol className="mt-3 space-y-2">
              {pendingChecks.map((check, i) => {
                const step = CHECK_NEXT_STEPS[check.name];
                return (
                  <li key={check.name} className="flex gap-3 text-sm">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-500/25 text-[10px] font-bold text-amber-200">
                      {i + 1}
                    </span>
                    <div>
                      <p className="font-medium text-zinc-200">{step?.title ?? check.detail}</p>
                      <p className="text-xs text-zinc-500">{step?.detail ?? check.detail}</p>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}

      {memory.score >= 100 && (
        <p className="mt-4 rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">
          All memory integrity checks passed. Use Phala Copilot and click Verify below to record TEE attestation receipts.
        </p>
      )}

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
    </>
  );

  if (embedded) return <div>{body}</div>;

  return (
    <section className="rounded-2xl border border-fuchsia-500/35 bg-fuchsia-950/10 p-6">{body}</section>
  );
}
