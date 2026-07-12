'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { apiUrl } from '@/lib/api-base';
import { FounderGdpCard, type FounderGdp } from './founder-gdp-card';
import { DdollarBalance, type DdollarBalanceData } from './ddollar-balance';
import { ClaimTokens, type ClaimableEpoch } from './claim-tokens';
import { KnowledgeGraphViz, type KnowledgeNodeRow } from './knowledge-graph-viz';
import { ProofOfSuccessPanel, type ProofRow } from './proof-of-success';
import { EpochHistory, type EpochRow } from './epoch-history';

export type FounderGdpResponse = FounderGdp;
export type EpochsResponse = EpochRow[];
export type DdollarBalanceResponse = DdollarBalanceData;
export type ClaimableResponse = ClaimableEpoch[];
export type KnowledgeResponse = KnowledgeNodeRow[];
export type ProofsResponse = ProofRow[];

async function fetchJson<T>(path: string, token?: string): Promise<T> {
  const res = await fetch(apiUrl(path), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`${res.status} on ${path}`);
  const text = await res.text();
  if (!text) return [] as unknown as T;
  return JSON.parse(text) as T;
}

export function FounderEconomicsDashboard() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [gdp, setGdp] = useState<FounderGdp | null>(null);
  const [epochs, setEpochs] = useState<EpochRow[]>([]);
  const [balance, setBalance] = useState<DdollarBalanceData | null>(null);
  const [claimable, setClaimable] = useState<ClaimableEpoch[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeNodeRow[]>([]);
  const [proofs, setProofs] = useState<ProofRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [g, e, k] = await Promise.all([
        fetchJson<FounderGdp>('/founder-economics/gdp'),
        fetchJson<EpochRow[]>('/founder-economics/epochs?limit=10'),
        fetchJson<KnowledgeNodeRow[]>('/founder-economics/knowledge?limit=25'),
      ]);
      setGdp(g);
      setEpochs(e);
      setKnowledge(k);
      if (token) {
        const [b, c] = await Promise.all([
          fetchJson<DdollarBalanceData>('/founder-economics/ddollar/balance', token),
          fetchJson<ClaimableEpoch[]>('/founder-economics/claimable', token),
        ]);
        setBalance(b);
        setClaimable(c);
        if (b?.userId) {
          const p = await fetchJson<ProofRow[]>(
            `/founder-economics/proofs/${b.userId}`,
          );
          setProofs(p);
        }
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <section className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-violet-400">
            Phase 8 · MVP (off-chain) · Production counsel-gated
          </p>
          <h2 className="mt-1 text-3xl font-bold text-white">Founder Economics</h2>
          <p className="mt-2 max-w-2xl text-sm text-zinc-400">
            <strong className="text-zinc-300">MVP now:</strong> DDollar ledger, knowledge graph, Merkle
            proofs, GDP dashboard — no live token mint.{' '}
            <strong className="text-zinc-300">Production later:</strong> audited PlatformToken /
            VestingVault / EpochDistributor on-chain (keys + counsel required). See{' '}
            <code className="text-zinc-500">docs/FOUNDER-ECONOMICS-MVP-VS-PRODUCTION.md</code>.
          </p>
        </div>
        <button
          onClick={() => void load()}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 hover:border-violet-500/50"
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </section>

      {error && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
          Could not load Founder Economics data: {error}
        </div>
      )}

      <FounderGdpCard gdp={gdp} loading={loading} />

      <div className="grid gap-6 lg:grid-cols-2">
        <DdollarBalance balance={balance} signedIn={!!token} loading={loading} />
        <ClaimTokens claimable={claimable} signedIn={!!token} loading={loading} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <KnowledgeGraphViz nodes={knowledge} loading={loading} />
        <ProofOfSuccessPanel proofs={proofs} signedIn={!!token} loading={loading} />
      </div>

      <EpochHistory epochs={epochs} loading={loading} />
    </div>
  );
}
