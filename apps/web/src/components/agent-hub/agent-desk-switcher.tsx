'use client';

export type AgentDeskId = 'showcase' | 'live' | 'relay-sim';

/** Compact desk tabs — primary navigation lives in CopyTradeHub; these mirror the active desk. */
export function AgentDeskSwitcher({
  activeDesk,
  onChange,
  exchangeLabel,
  liveAvailable,
  relaySimAvailable,
  relaySimActive,
}: {
  activeDesk: AgentDeskId;
  onChange: (desk: AgentDeskId) => void;
  exchangeLabel?: string | null;
  liveAvailable: boolean;
  liveHired?: boolean;
  relaySimAvailable?: boolean;
  relaySimActive?: boolean;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const simDeskOn = relaySimAvailable ?? liveAvailable;

  const tabs: { id: AgentDeskId; label: string; short: string; color: string }[] = [
    { id: 'live', label: `${exchange} live copy`, short: 'Live', color: 'emerald' },
    { id: 'relay-sim', label: `${exchange} relay sim`, short: 'Sim', color: 'sky' },
    { id: 'showcase', label: 'Research showcase', short: 'Showcase', color: 'violet' },
  ];

  return (
    <div className="flex flex-wrap gap-2 rounded-xl border border-zinc-800 bg-zinc-950/50 p-1.5">
      {tabs.map((t) => {
        const disabled = t.id === 'live' && !liveAvailable ? true : t.id === 'relay-sim' && !simDeskOn;
        const active = activeDesk === t.id;
        const colorMap = {
          emerald: active
            ? 'bg-emerald-600 text-white shadow-emerald-900/30'
            : 'text-emerald-300/80 hover:bg-emerald-950/40',
          sky: active
            ? 'bg-sky-600 text-white shadow-sky-900/30'
            : 'text-sky-300/80 hover:bg-sky-950/40',
          violet: active
            ? 'bg-violet-600 text-white shadow-violet-900/30'
            : 'text-violet-300/80 hover:bg-violet-950/40',
        }[t.color];

        return (
          <button
            key={t.id}
            type="button"
            disabled={disabled}
            onClick={() => !disabled && onChange(t.id)}
            className={`flex-1 min-w-[100px] rounded-lg px-3 py-2.5 text-center text-sm font-semibold transition shadow-sm disabled:cursor-not-allowed disabled:opacity-40 ${colorMap}`}
          >
            <span className="hidden sm:inline">{t.label}</span>
            <span className="sm:hidden">{t.short}</span>
            {t.id === 'relay-sim' && relaySimActive ? (
              <span className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-white animate-pulse" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
