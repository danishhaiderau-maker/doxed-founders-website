'use client';

import type { TradingAgentDashboardState } from '@dcf/utils';
import { AgentLiveExchangeEquity } from '@/components/agent-hub/agent-live-exchange-equity';
import { AgentShowcaseEquity } from '@/components/agent-hub/agent-showcase-equity';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import { AgentTransparencyTables } from '@/components/agent-hub/agent-transparency-tables';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
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

export function AgentDeskView({
  activeDesk,
  mode,
  exchangeLabel,
  userAgent,
  showcaseAgent,
  exchangeLiveBook,
  showcaseLiveBook,
  userActivity,
  showcaseActivity,
}: {
  activeDesk: AgentDeskId;
  mode: 'live' | 'copy' | 'showcase';
  exchangeLabel?: string | null;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  exchangeLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseLiveBook?: TradingAgentDashboardState['liveBook'];
  userActivity: TradingAgentActivityEntry[];
  showcaseActivity: TradingAgentActivityEntry[];
}) {
  if (activeDesk === 'showcase' || mode === 'showcase') {
    return (
      <DeskPanel
        badge="Admin showcase"
        badgeClassName="text-violet-300"
        borderClassName="border-violet-500/35"
        title="Conservative BTC Agent · showcase bot"
        subtitle="Public research run on Railway — signals, limit orders, open positions, expired orders, and closed trades."
      >
        <AgentShowcaseEquity agent={showcaseAgent} title="Showcase equity" />
        <AgentTransparencyTables liveBook={showcaseLiveBook} maxRows={10} />
        <AgentTradeJourney
          activity={showcaseActivity}
          liveBook={showcaseLiveBook}
          layout="horizontal"
          windowMinutes={30}
        />
      </DeskPanel>
    );
  }

  const exchange = exchangeLabel ?? 'Bitfinex';
  const isLive = mode === 'live';

  return (
    <DeskPanel
      badge={`${exchange} · live copy`}
      badgeClassName="text-emerald-300"
      borderClassName="border-emerald-500/45"
      title={isLive ? `Your ${exchange} account` : 'Your paper-track session'}
      subtitle={
        isLive
          ? 'Real money on your exchange — every open order, position, expired limit, and closed copy trade.'
          : 'Your isolated DDollar session — signals and trades from when you started paper tracking.'
      }
    >
      {isLive ? (
        <AgentLiveExchangeEquity agent={userAgent} exchangeLabel={exchange} />
      ) : (
        <AgentShowcaseEquity agent={userAgent} title="Your session equity" compact />
      )}
      {exchangeLiveBook ? (
        <AgentTransparencyTables liveBook={exchangeLiveBook} maxRows={10} />
      ) : isLive ? (
        <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
          Connect {exchange} and start the relay to load live orders and positions from your exchange.
        </p>
      ) : (
        <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
          Start a paper-track session to see your signals, pending limits, and closed trades here.
        </p>
      )}
      <AgentTradeJourney
        activity={userActivity}
        liveBook={exchangeLiveBook}
        layout="horizontal"
        showBalance
        windowMinutes={30}
      />
    </DeskPanel>
  );
}

/** @deprecated Use AgentDeskView with activeDesk instead */
export function AgentDualDeskPanels(props: {
  mode: 'live' | 'copy' | 'showcase';
  exchangeLabel?: string | null;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  exchangeLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseLiveBook?: TradingAgentDashboardState['liveBook'];
  userActivity: TradingAgentActivityEntry[];
  showcaseActivity: TradingAgentActivityEntry[];
  activeDesk?: AgentDeskId;
}) {
  const desk: AgentDeskId =
    props.activeDesk ?? (props.mode === 'showcase' ? 'showcase' : 'live');
  return <AgentDeskView activeDesk={desk} {...props} />;
}
