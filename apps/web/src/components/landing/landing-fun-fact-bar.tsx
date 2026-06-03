'use client';

const PUMP_FUN_YAHOO =
  'https://finance.yahoo.com/news/99-6-pump-fun-traders-074204251.html';
const PUMP_FUN_CMC =
  'https://coinmarketcap.com/academy/article/996percent-of-pumpfun-traders-have-not-realized-over-dollar10000-in-profits-data-shows';

const LOST_PCT = 99.6;
const PROFIT_PCT = 0.4;
const R = 42;
const C = 2 * Math.PI * R;
const GREEN_LEN = (PROFIT_PCT / 100) * C;
const RED_LEN = (LOST_PCT / 100) * C;

function PumpFunDonut() {
  return (
    <div className="relative shrink-0" aria-hidden>
      <svg width="112" height="112" viewBox="0 0 112 112" className="drop-shadow-[0_0_20px_rgba(239,68,68,0.25)]">
        <circle cx="56" cy="56" r={R} fill="#0a0a0a" stroke="#1a1a1a" strokeWidth="2" />
        {/* 0.4% realized $10k+ — green sliver */}
        <circle
          cx="56"
          cy="56"
          r={R}
          fill="none"
          stroke="#22c55e"
          strokeWidth="14"
          strokeLinecap="butt"
          strokeDasharray={`${GREEN_LEN} ${C}`}
          transform="rotate(-90 56 56)"
        />
        {/* 99.6% did not — red dominant arc */}
        <circle
          cx="56"
          cy="56"
          r={R}
          fill="none"
          stroke="#ef4444"
          strokeWidth="14"
          strokeLinecap="butt"
          strokeDasharray={`${RED_LEN} ${C}`}
          strokeDashoffset={-GREEN_LEN}
          transform="rotate(-90 56 56)"
        />
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="text-xl font-bold leading-none text-red-400">{LOST_PCT}%</span>
        <span className="mt-0.5 max-w-[4.5rem] text-[7px] font-semibold uppercase leading-tight text-zinc-500">
          no $10k+ profit
        </span>
      </div>
    </div>
  );
}

export function LandingFunFactBar() {
  return (
    <section
      aria-label="Pump.fun trader statistics"
      className="rounded-2xl border border-emerald-500/25 bg-[#050508] p-4 shadow-2xl shadow-emerald-950/20 sm:p-5"
    >
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:gap-8">
        <div className="flex items-center gap-4 sm:gap-6">
          <PumpFunDonut />
          <div className="flex flex-col gap-2 text-[10px] font-semibold uppercase tracking-wide">
            <span className="flex items-center gap-2 text-red-400">
              <span className="h-2.5 w-2.5 rounded-full bg-red-500" aria-hidden />
              {LOST_PCT}% — no $10k+ realized profit
            </span>
            <span className="flex items-center gap-2 text-emerald-400">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" aria-hidden />
              {PROFIT_PCT}% — crossed $10k realized gains
            </span>
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">Fun fact</p>
          <p className="mt-1.5 text-sm font-bold leading-snug text-white sm:text-base">
            <span className="text-red-400">{LOST_PCT}%</span> of Pump.fun traders have not realized over{' '}
            <span className="text-white">$10,000</span> in profits — data shows.
          </p>
          <p className="mt-2 text-xs leading-relaxed text-zinc-400">
            Out of 13.55M wallet addresses on Pump.fun, only ~55k crossed $10k in realized gains (Dune Analytics,
            reported Jan 2025). We came to crypto for the tech — let&apos;s build HODL culture and bring conviction to
            the space.
          </p>
        </div>

        <div className="shrink-0 lg:w-52">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-zinc-500">Source</p>
          <ul className="mt-2 space-y-2">
            <li>
              <a
                href={PUMP_FUN_YAHOO}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center gap-2 rounded-lg border border-zinc-800 bg-black/40 px-3 py-2 text-xs font-semibold text-emerald-300 transition hover:border-emerald-500/50 hover:bg-emerald-950/30 hover:text-emerald-200"
              >
                <span className="text-[10px] font-bold text-zinc-500">YF</span>
                Yahoo Finance
                <span className="ml-auto text-emerald-500/80 group-hover:translate-x-0.5" aria-hidden>
                  →
                </span>
              </a>
            </li>
            <li>
              <a
                href={PUMP_FUN_CMC}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center gap-2 rounded-lg border border-zinc-800 bg-black/40 px-3 py-2 text-xs font-semibold text-emerald-300 transition hover:border-emerald-500/50 hover:bg-emerald-950/30 hover:text-emerald-200"
              >
                <span className="text-[10px] font-bold text-zinc-500">CMC</span>
                CoinMarketCap Academy
                <span className="ml-auto text-emerald-500/80 group-hover:translate-x-0.5" aria-hidden>
                  →
                </span>
              </a>
            </li>
          </ul>
        </div>
      </div>
    </section>
  );
}
