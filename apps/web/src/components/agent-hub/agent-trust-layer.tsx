'use client';

export function AgentTrustLayer({
  builderHandle = '@bitbro4crypto',
  githubVerified = true,
  xVerified = true,
  performancePublic = true,
  exchangeConnected = true,
}: {
  builderHandle?: string;
  githubVerified?: boolean;
  xVerified?: boolean;
  performancePublic?: boolean;
  exchangeConnected?: boolean;
}) {
  const badges = [
    { label: 'Doxxed builder', ok: true, detail: builderHandle },
    { label: 'GitHub verified', ok: githubVerified, detail: 'Strategy repo linked' },
    { label: 'X verified', ok: xVerified, detail: 'Public builder account' },
    { label: 'Track record public', ok: performancePublic, detail: 'Live trades visible' },
    { label: 'Exchange connected', ok: exchangeConnected, detail: 'Admin showcase live' },
  ];

  return (
    <section className="rounded-2xl border border-emerald-500/25 bg-emerald-950/10 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">Verified strategy</p>
          <h2 className="mt-1 text-lg font-bold text-white">Trust layer</h2>
          <p className="mt-1 text-xs text-zinc-500">Trade builders. Not excuses.</p>
        </div>
        <span className="rounded-full border border-emerald-500/50 bg-emerald-500/15 px-3 py-1 text-[10px] font-bold uppercase text-emerald-300">
          Verified
        </span>
      </div>
      <ul className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {badges.map((b) => (
          <li
            key={b.label}
            className={`rounded-xl border px-3 py-3 ${
              b.ok ? 'border-emerald-800/60 bg-black/20' : 'border-zinc-800 opacity-50'
            }`}
          >
            <p className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
              <span className={b.ok ? 'text-emerald-400' : 'text-zinc-600'}>{b.ok ? '✓' : '○'}</span>
              {b.label}
            </p>
            <p className="mt-1 pl-5 text-xs text-zinc-500">{b.detail}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
