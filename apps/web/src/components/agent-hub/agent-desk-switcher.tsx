'use client';

export type AgentDeskId = 'showcase' | 'live' | 'relay-sim';

/** Compact desk tabs — 3-tab switcher: Showcase Bot | Bitfinex relay sim | Bitfinex live copy.
 *  Showcase Bot surfaces the live :7002 admin bot snapshot (signals, orders, positions, trades).
 *  The other two tabs drive the visitor's own Bitfinex copy session. */
export function AgentDeskSwitcher({
  activeDesk,
  onChange,
  exchangeLabel,
  liveAvailable,
  relaySimAvailable,
  relaySimActive,
  showcaseAvailable,
}: {
  activeDesk: AgentDeskId;
  onChange: (desk: AgentDeskId) => void;
  exchangeLabel?: string | null;
  liveAvailable: boolean;
  liveHired?: boolean;
  relaySimAvailable?: boolean;
  relaySimActive?: boolean;
  showcaseAvailable?: boolean;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const simDeskOn = relaySimAvailable ?? liveAvailable;

  const tabs: { id: AgentDeskId; label: string; short: string; color: string }[] = [
    { id: 'showcase', label: 'Showcase Bot', short: 'Showcase', color: 'violet' },
    { id: 'relay-sim', label: `${exchange} relay sim`, short: 'Sim', color: 'sky' },
    { id: 'live', label: `${exchange} live copy`, short: 'Live', color: 'emerald' },
  ];

  return (
    <div className="flex flex-wrap items-stretch gap-2 rounded-xl border border-zinc-700 bg-gradient-to-b from-zinc-900/90 to-zinc-950/80 p-2 shadow-lg shadow-black/40 ring-1 ring-white/5 backdrop-blur">
      {tabs.map((t) => {
        const disabled =
          t.id === 'live' && !liveAvailable
            ? true
            : t.id === 'relay-sim' && !simDeskOn
              ? true
              : false;
        const active = activeDesk === t.id;
        const colorMap = {
          violet: active
            ? 'bg-violet-500/25 border-violet-400/80 text-violet-100 font-bold shadow-violet-900/40 ring-1 ring-violet-400/40'
            : 'border-zinc-700/60 text-violet-300/70 hover:bg-violet-950/40 hover:border-violet-600/60 hover:text-violet-100',
          emerald: active
            ? 'bg-emerald-500/25 border-emerald-400/80 text-emerald-100 font-bold shadow-emerald-900/40 ring-1 ring-emerald-400/40'
            : 'border-zinc-700/60 text-emerald-300/70 hover:bg-emerald-950/40 hover:border-emerald-600/60 hover:text-emerald-100',
          sky: active
            ? 'bg-sky-500/25 border-sky-400/80 text-sky-100 font-bold shadow-sky-900/40 ring-1 ring-sky-400/40'
            : 'border-zinc-700/60 text-sky-300/70 hover:bg-sky-950/40 hover:border-sky-600/60 hover:text-sky-100',
        }[t.color];

        return (
          <button
            key={t.id}
            type="button"
            disabled={disabled}
            onClick={() => !disabled && onChange(t.id)}
            className={`flex-1 min-w-[120px] rounded-lg border px-5 py-3 text-center text-sm sm:text-base font-semibold tracking-tight transition-all duration-150 shadow-md disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:border-zinc-700/60 ${colorMap}`}
          >
            <span className="hidden sm:inline">{t.label}</span>
            <span className="sm:hidden">{t.short}</span>
            {t.id === 'relay-sim' && relaySimActive ? (
              <span className="ml-2 inline-block h-2 w-2 rounded-full bg-white animate-pulse" />
            ) : null}
            {t.id === 'showcase' && showcaseAvailable ? (
              <span className="ml-2 inline-block h-2 w-2 rounded-full bg-emerald-400 animate-pulse shadow-emerald-400/80 shadow-[0_0_6px_2px_rgba(52,211,153,0.5)]" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
