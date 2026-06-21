'use client';

import Link from 'next/link';
import type { TradingAgentDashboardState } from '@dcf/utils';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import { AgentTransparencyTables } from '@/components/agent-hub/agent-transparency-tables';
import { AgentLiveTradeExportButton } from '@/components/agent-hub/agent-live-trade-export-button';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import { AgentRelaySimPanel } from '@/components/agent-hub/agent-relay-sim-panel';
import type {
  CopyRelayReconcileSnapshot,
  CopyRelaySimState,
  CopyRelayLimitChainSnapshot,
  TradeLifecycleIntegritySnapshot,
} from '@dcf/utils';
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
  userAgent: _userAgent,
  showcaseAgent,
  exchangeLiveBook,
  showcaseLiveBook,
  relaySimLiveBook,
  copyRelaySim,
  copyRelayReconcile,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  relayFidelity,
  botConnected,
  userActivity,
  showcaseActivity,
  slug,
  accessToken,
  signedIn,
  instanceStatus,
  onStartRelaySim,
  onStopRelaySim,
  relaySimBusy,
}: {
  activeDesk: AgentDeskId;
  mode: 'live' | 'copy' | 'showcase';
  exchangeLabel?: string | null;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  exchangeLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseLiveBook?: TradingAgentDashboardState['liveBook'];
  relaySimLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  relayFidelity?: import('@/components/agent-hub/agent-relay-fidelity-panel').RelayFidelitySnapshot | null;
  botConnected?: boolean;
  userActivity: TradingAgentActivityEntry[];
  showcaseActivity: TradingAgentActivityEntry[];
  slug?: string;
  accessToken?: string;
  signedIn?: boolean;
  instanceStatus?: string | null;
  onStartRelaySim?: () => void;
  onStopRelaySim?: () => void;
  relaySimBusy?: boolean;
}) {
  if (activeDesk === 'relay-sim') {
    return (
      <AgentRelaySimPanel
        signedIn={Boolean(signedIn)}
        exchangeLabel={exchangeLabel}
        showcaseAgent={showcaseAgent}
        copyRelaySim={copyRelaySim}
        copyRelayReconcile={copyRelayReconcile}
        relayFidelity={relayFidelity}
        relaySimLiveBook={relaySimLiveBook}
        showcaseLiveBook={showcaseLiveBook}
        showcaseActivity={showcaseActivity}
        simActivity={userActivity}
        copyRelayLimitChain={copyRelayLimitChain}
        tradeLifecycleIntegrity={tradeLifecycleIntegrity}
        botConnected={botConnected}
        onStart={onStartRelaySim}
        onStop={onStopRelaySim}
        busy={relaySimBusy}
        instanceStatus={instanceStatus}
        hideSummaryMetrics
      />
    );
  }

  if (activeDesk === 'showcase' || mode === 'showcase') {
    return (
      <DeskPanel
        badge="Research showcase"
        badgeClassName="text-violet-300"
        borderClassName="border-violet-500/35"
        title="Conservative BTC Agent · showcase bot"
        subtitle="Home research bot — signals, limit orders, open positions, expired orders, and closed trades."
      >
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

  if (!isLive) {
    const hireHref = slug
      ? signedIn
        ? `/agent-hub/${slug}/hire?exchange=bitfinex`
        : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=bitfinex`)}`
      : '/agent-hub';

    return (
      <DeskPanel
        badge={`${exchange} · live copy`}
        badgeClassName="text-emerald-300"
        borderClassName="border-emerald-500/45"
        title={`Connect ${exchange} to copy trades`}
        subtitle="Real API relay on your exchange — not a dummy paper session. Same signals, limits, and exits as the admin showcase."
      >
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-5 text-center">
          <p className="text-sm text-emerald-100/90">
            Connect read+trade API keys on Bitfinex Derivatives. Platform enforces virtual $20/lot caps on
            your real account.
          </p>
          <Link
            href={hireHref}
            className="mt-4 inline-block rounded-xl bg-emerald-600 px-8 py-3 text-sm font-bold text-white hover:bg-emerald-500"
          >
            Connect {exchange} API
          </Link>
          <p className="mt-3 text-[11px] text-zinc-500">
            Or run relay simulation after connecting — test Option B without real orders.
          </p>
        </div>
        <AgentTransparencyTables liveBook={showcaseLiveBook} maxRows={6} />
        <p className="text-xs text-zinc-500">
          Below: showcase reference trades (admin bot). Your copy desk fills in once API is connected.
        </p>
      </DeskPanel>
    );
  }

  return (
    <DeskPanel
      badge={`${exchange} · live copy`}
      badgeClassName="text-emerald-300"
      borderClassName="border-emerald-500/45"
      title={`Your ${exchange} account`}
      subtitle="Real money on your exchange — every open order, position, expired limit, and closed copy trade."
    >
      {slug ? (
        <AgentLiveTradeExportButton
          slug={slug}
          token={accessToken}
          signedIn={Boolean(accessToken)}
          exchangeLabel={exchange}
        />
      ) : null}
      {exchangeLiveBook ? (
        <AgentTransparencyTables liveBook={exchangeLiveBook} maxRows={10} />
      ) : isLive ? (
        <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
          Connect {exchange} and start the relay to load live orders and positions from your exchange.
        </p>
      ) : (
        <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
          Waiting for exchange sync — relay tick loads orders and positions within seconds.
        </p>
      )}
      <AgentTradeJourney
        activity={userActivity}
        liveBook={exchangeLiveBook}
        layout="horizontal"
        showBalance
        windowMinutes={30}
        liveExchangeOnly={isLive}
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
  relaySimLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  userActivity: TradingAgentActivityEntry[];
  showcaseActivity: TradingAgentActivityEntry[];
  activeDesk?: AgentDeskId;
  slug?: string;
  accessToken?: string;
  signedIn?: boolean;
  instanceStatus?: string | null;
  onStartRelaySim?: () => void;
  onStopRelaySim?: () => void;
  relaySimBusy?: boolean;
}) {
  const desk: AgentDeskId =
    props.activeDesk ?? (props.mode === 'showcase' ? 'showcase' : 'live');
  return <AgentDeskView activeDesk={desk} {...props} />;
}
