'use client';

export type EpochRow = {
  id: string;
  epochNumber: number;
  startTime: string;
  endTime: string;
  tokensReleased: number;
  merkleRoot: string | null;
  proofDataUri: string | null;
  status: string;
  distributionModelVersion: string | null;
  publishTxHash: string | null;
  publishedAt: string | null;
  _count?: { claims: number };
};

const STATUS_COLORS: Record<string, string> = {
  OPEN: 'text-zinc-400',
  SETTLING: 'text-amber-300',
  PUBLISHED: 'text-emerald-300',
  CLOSED: 'text-zinc-500',
};

export function EpochHistory({ epochs, loading }: { epochs: EpochRow[]; loading: boolean }) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Epoch history</h3>
      <p className="mt-1 text-[11px] text-zinc-600">
        Each epoch publishes a Merkle root on-chain; founders claim against the root for 365 days.
      </p>

      {loading && epochs.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : epochs.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">No epochs yet. The settlement job will open epoch 0 on its first run.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-[11px] uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="py-2 pr-3">#</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Model</th>
                <th className="py-2 pr-3">Released</th>
                <th className="py-2 pr-3">Claims</th>
                <th className="py-2 pr-3">Merkle root</th>
                <th className="py-2 pr-3">Ends</th>
              </tr>
            </thead>
            <tbody>
              {epochs.map((e) => (
                <tr key={e.id} className="border-t border-zinc-800">
                  <td className="py-2 pr-3 text-zinc-200">{e.epochNumber}</td>
                  <td className={`py-2 pr-3 ${STATUS_COLORS[e.status] ?? 'text-zinc-400'}`}>{e.status}</td>
                  <td className="py-2 pr-3 text-zinc-400">{e.distributionModelVersion ?? '—'}</td>
                  <td className="py-2 pr-3 text-zinc-300">{e.tokensReleased.toLocaleString()}</td>
                  <td className="py-2 pr-3 text-zinc-300">{e._count?.claims ?? 0}</td>
                  <td className="py-2 pr-3 font-mono text-[10px] text-zinc-500">
                    {e.merkleRoot ? `${e.merkleRoot.slice(0, 10)}…` : '—'}
                  </td>
                  <td className="py-2 pr-3 text-zinc-500">
                    {new Date(e.endTime).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
