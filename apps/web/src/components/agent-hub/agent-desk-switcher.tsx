'use client';

export type AgentDeskId = 'showcase' | 'live';

export function AgentDeskSwitcher({
  activeDesk,
  onChange,
  exchangeLabel,
  liveAvailable,
  liveHired,
}: {
  activeDesk: AgentDeskId;
  onChange: (desk: AgentDeskId) => void;
  exchangeLabel?: string | null;
  liveAvailable: boolean;
  liveHired: boolean;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <button
        type="button"
        onClick={() => onChange('showcase')}
        className={`rounded-2xl border-2 p-4 text-left transition ${
          activeDesk === 'showcase'
            ? 'border-violet-500/70 bg-violet-950/35 ring-2 ring-violet-500/30'
            : 'border-zinc-800 bg-zinc-950/40 hover:border-violet-500/40'
        }`}
      >
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
          Admin showcase
        </p>
        <p className="mt-1 text-base font-bold text-white">Conservative BTC Agent</p>
        <p className="mt-1 text-xs text-zinc-500">
          Public research bot on Railway — signals, limits, positions, and paper trades.
        </p>
      </button>

      <button
        type="button"
        onClick={() => liveAvailable && onChange('live')}
        disabled={!liveAvailable}
        className={`rounded-2xl border-2 p-4 text-left transition ${
          !liveAvailable
            ? 'cursor-not-allowed border-zinc-800/80 bg-zinc-950/20 opacity-60'
            : activeDesk === 'live'
              ? 'border-emerald-500/70 bg-emerald-950/30 ring-2 ring-emerald-500/30'
              : 'border-zinc-800 bg-zinc-950/40 hover:border-emerald-500/40'
        }`}
      >
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-300">
          {exchange} · live copy
        </p>
        <p className="mt-1 text-base font-bold text-white">Your live bot on {exchange}</p>
        <p className="mt-1 text-xs text-zinc-500">
          {liveHired
            ? 'Real orders, open positions, expired limits, and closed copy trades on your exchange.'
            : `Hire the agent and connect ${exchange} to unlock your live desk.`}
        </p>
      </button>
    </div>
  );
}
