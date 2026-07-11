'use client';

import Link from 'next/link';

export type ProofRow = {
  id: string;
  userId: string;
  proofType: string;
  externalId: string;
  verifiedMetric: number;
  metricLabel: string;
  multiplier: number;
  verifiedAt: string;
  reverified: boolean;
};

export function ProofOfSuccessPanel({
  proofs,
  signedIn,
  loading,
}: {
  proofs: ProofRow[];
  signedIn: boolean;
  loading: boolean;
}) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Proof of Success</h3>
      <p className="mt-1 text-[11px] text-zinc-600">
        Verified real-world milestones (Stripe ARR, GitHub stars, Vercel deploys, …).
      </p>

      {!signedIn ? (
        <p className="mt-3 text-sm text-zinc-400">
          <Link href="/login?callbackUrl=/founder-economics" className="text-emerald-400 hover:underline">
            Sign in
          </Link>{' '}
          to submit a milestone for verification.
        </p>
      ) : loading && proofs.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : proofs.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">No verified milestones yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {proofs.map((p) => (
            <li
              key={p.id}
              className="rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-emerald-300">{p.proofType}</span>
                <span className="text-[11px] text-zinc-500">
                  {p.reverified ? 're-verified' : 'verified'} · ×{p.multiplier.toFixed(2)}
                </span>
              </div>
              <p className="mt-1 text-xs text-zinc-300">
                {p.metricLabel}: {p.verifiedMetric.toLocaleString()}
              </p>
              <p className="mt-1 text-[10px] text-zinc-600">
                external id: {p.externalId} · {new Date(p.verifiedAt).toLocaleDateString()}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
