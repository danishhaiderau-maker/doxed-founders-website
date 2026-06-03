'use client';

const PUMP_FUN_YAHOO =
  'https://finance.yahoo.com/news/99-6-pump-fun-traders-074204251.html';
const PUMP_FUN_CMC =
  'https://coinmarketcap.com/academy/article/996percent-of-pumpfun-traders-have-not-realized-over-dollar10000-in-profits-data-shows';

function HoodedFigureArt() {
  return (
    <div
      className="relative mx-auto flex h-[min(280px,42vw)] w-full max-w-[220px] items-end justify-center lg:mx-0 lg:h-[300px] lg:max-w-[260px]"
      aria-hidden
    >
      <div className="absolute inset-0 overflow-hidden rounded-lg opacity-40">
        <div
          className="h-full w-full text-[8px] leading-none text-emerald-500/20"
          style={{
            fontFamily: 'ui-monospace, monospace',
            backgroundImage:
              'repeating-linear-gradient(0deg, transparent, transparent 12px, rgba(0,255,65,0.03) 12px, rgba(0,255,65,0.03) 13px)',
          }}
        >
          {Array.from({ length: 18 }).map((_, row) => (
            <div key={row} className="truncate whitespace-nowrap">
              {'01'.repeat(40)}
            </div>
          ))}
        </div>
      </div>
      <svg viewBox="0 0 200 280" className="relative h-full w-auto drop-shadow-[0_0_24px_rgba(57,255,20,0.35)]">
        <path
          d="M40 120 Q100 20 160 120 L175 280 L25 280 Z"
          fill="#0a0a0a"
          stroke="#1a3a1a"
          strokeWidth="1"
        />
        <ellipse cx="100" cy="95" rx="52" ry="58" fill="#050505" />
        <text
          x="100"
          y="115"
          textAnchor="middle"
          fill="#39ff14"
          fontSize="72"
          fontWeight="bold"
          fontFamily="system-ui, sans-serif"
        >
          ?
        </text>
        <rect x="72" y="200" width="56" height="72" fill="#39ff14" opacity="0.85" />
        <rect x="78" y="206" width="44" height="60" fill="#b8ffb8" opacity="0.95" />
        <ellipse cx="100" cy="248" rx="10" ry="18" fill="#1a1a1a" />
        <path d="M88 248 L100 220 L112 248 Z" fill="#2a2a2a" />
      </svg>
    </div>
  );
}

export function LandingConvictionTile() {
  return (
    <section
      className="overflow-hidden rounded-2xl border-2 border-[#39ff14]/70 bg-black shadow-[0_0_40px_rgba(57,255,20,0.12)]"
      aria-labelledby="conviction-tile-heading"
    >
      <div className="grid items-center gap-6 p-5 sm:p-6 lg:grid-cols-[1fr_auto] lg:gap-8 lg:p-8">
        <div className="min-w-0">
          <h2
            id="conviction-tile-heading"
            className="text-2xl font-bold uppercase leading-[1.05] tracking-tight sm:text-3xl lg:text-[2rem]"
          >
            <span className="text-white">If you want the money,</span>
            <br />
            <span className="text-[#39ff14]">show the face.</span>
          </h2>

          <p className="mt-4 max-w-xl text-sm leading-relaxed text-zinc-200 sm:text-base">
            Sending money to anonymous founders or buying meme coins is no different than sending it to
            a stranger on the internet.
          </p>

          <div className="mt-5 max-w-xl border-l-2 border-[#39ff14]/50 pl-4">
            <p className="text-sm font-medium leading-relaxed text-[#d4ffd4] sm:text-[15px]">
              We came to crypto for the tech. Let&apos;s build the HODL culture and bring conviction to
              the space.
            </p>
            <p className="mt-2 text-xs leading-relaxed text-zinc-500">
              Trade doxxed founders. Reward builders who ship in public — not faceless launches and
              anonymous wallets.
            </p>
          </div>

          <div className="mt-6 max-w-xl rounded-xl border border-[#39ff14]/35 bg-[#031203]/90 p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#39ff14]">Fun fact</p>
            <p className="mt-2 text-sm font-semibold leading-snug text-white sm:text-base">
              99.6% of Pump.fun traders have not realized over $10,000 in profits — data shows.
            </p>
            <p className="mt-2 text-xs leading-relaxed text-zinc-400">
              Out of 13.55M wallet addresses on Pump.fun, only ~55k crossed $10k in realized gains (Dune
              Analytics, reported Jan 2025). The platform still generated hundreds of millions in
              revenue — while almost everyone else didn&apos;t.
            </p>
            <p className="mt-3 text-[11px] text-zinc-500">Sources · read the data yourself</p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
              <a
                href={PUMP_FUN_YAHOO}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs font-medium text-[#39ff14] underline decoration-[#39ff14]/40 underline-offset-2 hover:text-[#7fff7f]"
              >
                Yahoo Finance →
              </a>
              <a
                href={PUMP_FUN_CMC}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs font-medium text-[#39ff14] underline decoration-[#39ff14]/40 underline-offset-2 hover:text-[#7fff7f]"
              >
                CoinMarketCap Academy →
              </a>
            </div>
          </div>
        </div>

        <HoodedFigureArt />
      </div>
    </section>
  );
}
