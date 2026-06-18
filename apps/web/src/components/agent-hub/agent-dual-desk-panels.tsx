'use client';

import type { TradingAgentDashboardState } from '@dcf/utils';
import { AgentLiveExchangeEquity } from '@/components/agent-hub/agent-live-exchange-equity';
import { AgentShowcaseEquity } from '@/components/agent-hub/agent-showcase-equity';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import { AgentTransparencyTables } from '@/components/agent-hub/agent-transparency-tables';
import type { TradingAgentActivityEntry, TradingAgentSummary } from '@/lib/api';

type DeskPanelProps = {
  title: string;
  subtitle: string;
  badge: string;
  badgeClassName: string;
  borderClassName: string;
  children: React.ReactNode;
};

function DeskPanel({
  title,
  subtitle,
  badge,
  badgeClassName,
  borderClassName,
  children,
}: DeskPanelProps) {
  return (
    <section className={`rounded-2xl border-2 ${borderClassName} bg-zinc-950/40 p-4 sm:p-5`}>
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-zinc-800/80 pb-3">
        <div>
          <p className={`text-[10px] font-bold uppercase tracking-[0.2em] ${badgeClassName}`}>
            {badge}
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">{title}</h2>
          <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
        </div>
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

export function AgentDualDeskPanels({
  mode,
  exchangeLabel,
  userAgent,
  showcaseAgent,
  exchangeLiveBook,
  showcaseLiveBook,
  userActivity,
  showcaseActivity,
}: {
  mode: 'live' | 'copy' | 'showcase';
  exchangeLabel?: string | null;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  exchangeLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseLiveBook?: TradingAgentDashboardState['liveBook'];
  userActivity: TradingAgentActivityEntry[];
  showcaseActivity: TradingAgentActivityEntry[];
}) {
  if (mode === 'showcase') {
    return (
      <DeskPanel
        badge="Admin showcase"
        badgeClassName="text-violet-300"
        borderClassName="border-violet-500/35"
        title="Conservative BTC Agent · research desk"
        subtitle="Public paper run on Railway — signals, limits, positions, and trades from the showcase bot."
      >
        <AgentShowcaseEquity agent={showcaseAgent} title="Showcase equity" />
        <AgentTransparencyTables liveBook={showcaseLiveBook} maxRows={10} />
        <AgentTradeJourney activity={showcaseActivity} layout="horizontal" />
      </DeskPanel>
    );
  }

  const primary =
    mode === 'live'
      ? {
          badge: `${exchangeLabel ?? 'Bitfinex'} · live copy`,
          badgeClassName: 'text-emerald-300',
          borderClassName: 'border-emerald-500/45',
          title: `Your ${exchangeLabel ?? 'Bitfinex'} account`,
          subtitle:
            'Real money on your exchange — open position, pending limits, expired relay orders, closed copy trades, and session P&L.',
        }
      : {
          badge: 'Paper track',
          badgeClassName: 'text-violet-300',
          borderClassName: 'border-violet-500/40',
          title: 'Your paper-track session',
          subtitle:
            'DDollar simulation from when you started — isolated from the admin showcase and from other users.',
        };

  const secondary = {
    badge: 'Admin showcase bot',
    badgeClassName: 'text-amber-200/90',
    borderClassName: 'border-amber-500/30',
    title: 'Conservative BTC Agent · signal source',
    subtitle:
      'What the research bot is doing on Railway — this is what your live copy relays when a signal is approved.',
  };

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <DeskPanel {...primary}>
        {mode === 'live' ? (
          <AgentLiveExchangeEquity agent={userAgent} exchangeLabel={exchangeLabel ?? 'Bitfinex'} />
        ) : (
          <AgentShowcaseEquity agent={userAgent} title="Your session equity" compact />
        )}
        {exchangeLiveBook ? (
          <AgentTransparencyTables liveBook={exchangeLiveBook} maxRows={10} />
        ) : mode === 'live' ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
            Connect Bitfinex and start the relay to load live orders and positions from your exchange.
          </p>
        ) : (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
            Start a paper-track session to see your signals, pending limits, and closed trades here.
          </p>
        )}
        <AgentTradeJourney activity={userActivity} layout="horizontal" showBalance />
      </DeskPanel>

      <DeskPanel {...secondary}>
        <AgentShowcaseEquity agent={showcaseAgent} title="Showcase desk" compact />
        <AgentTransparencyTables liveBook={showcaseLiveBook} maxRows={10} />
        <AgentTradeJourney activity={showcaseActivity} layout="horizontal" />
      </DeskPanel>
    </div>
  );
}
