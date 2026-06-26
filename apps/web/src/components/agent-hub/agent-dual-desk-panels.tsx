'use client';

import Link from 'next/link';
import type { TradingAgentDashboardState } from '@dcf/utils';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import { AgentLiveTradeExportButton } from '@/components/agent-hub/agent-live-trade-export-button';
import {
  AgentTransparencyTables,
  EMPTY_LIVE_BOOK,
} from '@/components/agent-hub/agent-transparency-tables';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import { AgentRelaySimPanel } from '@/components/agent-hub/agent-relay-sim-panel';
import type {
  CopyRelayReconcileSnapshot,
  CopyRelaySimState,
  CopyRelayLimitChainSnapshot,
  TradeLifecycleIntegritySnapshot,
  RelaySimParticipantStats,
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
  showcaseAgent: _showcaseAgent,
  exchangeLiveBook,
  showcaseLiveBook,
  relaySimLiveBook,
  copyRelaySim,
  copyRelayReconcile,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  relaySimParticipantStats,
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
  onResetRelaySim,
  relaySimBusy,
  executionOnly = false,
  relaySimLiveView,
  onRelaySimLiveViewChange,
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
  relaySimParticipantStats?: RelaySimParticipantStats | null;
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
  onResetRelaySim?: () => void;
  relaySimBusy?: boolean;
  executionOnly?: boolean;
  relaySimLiveView?: boolean;
  onRelaySimLiveViewChange?: (enabled: boolean) => void;
}) {
  if (activeDesk === 'relay-sim') {
    return (
      <AgentRelaySimPanel
        signedIn={Boolean(signedIn)}
        exchangeLabel={exchangeLabel}
        copyRelaySim={copyRelaySim}
        copyRelayReconcile={copyRelayReconcile}
        relayFidelity={relayFidelity}
        relaySimLiveBook={relaySimLiveBook}
        simActivity={userActivity}
        copyRelayLimitChain={copyRelayLimitChain}
        tradeLifecycleIntegrity={tradeLifecycleIntegrity}
        relaySimParticipantStats={relaySimParticipantStats}
        botConnected={botConnected}
        onStart={onStartRelaySim}
        onStop={onStopRelaySim}
        onReset={onResetRelaySim}
        slug={slug}
        accessToken={accessToken}
        busy={relaySimBusy}
        instanceStatus={instanceStatus}
        hideSummaryMetrics
        relaySimLiveView={relaySimLiveView}
        onRelaySimLiveViewChange={onRelaySimLiveViewChange}
      />
    );
  }

  if (activeDesk === 'showcase' || mode === 'showcase') {
    const book = showcaseLiveBook ?? EMPTY_LIVE_BOOK;
    return (
      <DeskPanel
        badge="Global showcase"
        badgeClassName="text-violet-300"
        borderClassName="border-violet-500/35"
        title="Conservative BTC Agent · doxxedcrypto.digital"
        subtitle={
          executionOnly
            ? 'Public execution book — positions, orders, and closed trades from the :7002 showcase bot.'
            : 'Admin view — full pipeline tables from the global showcase bot on :7002.'
        }
      >
        <AgentTransparencyTables liveBook={book} maxRows={10} executionOnly={executionOnly} />
        <AgentTradeJourney
          activity={showcaseActivity}
          liveBook={book}
          layout="horizontal"
          windowMinutes={30}
        />
      </DeskPanel>
    );
  }

  const exchange = exchangeLabel ?? 'Bitfinex';
  const isLive = mode === 'live';
  const liveBook = exchangeLiveBook ?? EMPTY_LIVE_BOOK;

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
        subtitle="Your Bitfinex copy book lives here once API keys are connected. Use Global showcase bot tab to watch the admin :7002 bot — data stays separate."
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
            Or use the relay sim tab after connecting — paper book only, no showcase data mixed in.
          </p>
        </div>
        <p className="text-xs text-zinc-500">
          Tables below are your copy session book (empty until connected). Admin bot data is on the
          Research local bot tab only.
        </p>
        <AgentTransparencyTables liveBook={EMPTY_LIVE_BOOK} maxRows={10} />
      </DeskPanel>
    );
  }

  return (
    <DeskPanel
      badge={`${exchange} · live copy`}
      badgeClassName="text-emerald-300"
      borderClassName="border-emerald-500/45"
      title={`Your ${exchange} live session`}
      subtitle="Real Bitfinex copy trades only — open orders, positions, expired limits, and closed trades from your exchange account. Not the admin showcase bot."
    >
      {slug ? (
        <AgentLiveTradeExportButton
          slug={slug}
          token={accessToken}
          signedIn={Boolean(accessToken)}
          exchangeLabel={exchange}
        />
      ) : null}
      {!exchangeLiveBook ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-950/15 px-3 py-2 text-xs text-amber-100/90">
          Waiting for exchange sync — start the relay and ensure Derivatives has USDT. Tables update
          within seconds once your first mirrored signal fires.
        </p>
      ) : null}
      <AgentTransparencyTables liveBook={liveBook} maxRows={10} />
      <AgentTradeJourney
        activity={userActivity}
        liveBook={liveBook}
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
  onResetRelaySim?: () => void;
  relaySimBusy?: boolean;
}) {
  const desk: AgentDeskId =
    props.activeDesk ?? (props.mode === 'showcase' ? 'showcase' : 'live');
  return <AgentDeskView activeDesk={desk} {...props} />;
}
