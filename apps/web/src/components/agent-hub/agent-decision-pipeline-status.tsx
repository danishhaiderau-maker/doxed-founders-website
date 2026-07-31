import type { TradingAgentDashboardState } from '@dcf/utils';
import {
  chaseSelectionLabel,
  directionGap,
  directionGapLabel,
} from '@/components/agent-hub/agent-direction-gap';

export function AgentDecisionPipelineStatus({
  dashboard,
}: {
  dashboard: TradingAgentDashboardState;
}) {
  const pending = dashboard.pendingApproval;
  const verdictGap = dashboard.latestAiVerdict?.rawScoreGap;
  const rawGap = pending?.rawScoreGap ?? verdictGap;
  const gap = directionGap(rawGap);

  if (!pending && !gap) {
    return (
      <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 px-4 py-3 text-xs text-zinc-400">
        <p className="font-semibold text-zinc-200">Direction score scale</p>
        <p className="mt-1">
          Probability confidence is not requested. When available, this panel reports the raw LONG/SHORT
          score difference on 0–100 and its execution bucket (raw gap ÷ 10, rounded down).
        </p>
      </section>
    );
  }

  const status = String(pending?.status ?? 'EVALUATED').toUpperCase();
  const waiting =
    status.includes('PENDING') ||
    status.includes('WAIT') ||
    status.includes('VIRTUAL') ||
    status.includes('CHASE');
  const exactLimit = Number(pending?.exactLimitPrice ?? 0);
  const chase = pending?.chaseCount;
  const reportedBucket = pending?.gapBucket ?? dashboard.latestAiVerdict?.gapBucket;
  const gapText = gap
    ? directionGapLabel(rawGap)
    : reportedBucket != null
      ? `Raw AI gap not recorded · execution bucket ${reportedBucket >= 5 ? '5+' : reportedBucket}`
      : 'Gap not recorded';

  return (
    <section
      className={`rounded-2xl border px-4 py-3 text-xs ${
        waiting
          ? 'border-sky-500/35 bg-sky-950/20 text-sky-100'
          : 'border-zinc-700 bg-zinc-950/50 text-zinc-300'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold">
          {waiting ? 'Approved candidate · waiting in virtual chase' : 'Latest direction evaluation'}
        </p>
        <span className="rounded-full border border-current/30 px-2 py-0.5 font-mono text-[10px]">
          {status}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        {pending?.tradeId ? <span>Trade ID: {pending.tradeId}</span> : null}
        {pending?.direction ? <span>Direction: {pending.direction}</span> : null}
        <span>{gapText}</span>
        {chase != null ? <span>Virtual chase now: {chase}</span> : null}
        <span>Entry buckets selected: {chaseSelectionLabel(pending?.selectedChaseBuckets)}</span>
        {exactLimit > 0 ? <span>Exact structural limit: ${exactLimit.toLocaleString()}</span> : null}
      </div>
      {pending?.reason ? <p className="mt-2 text-[11px] opacity-80">{pending.reason}</p> : null}
      {waiting ? (
        <p className="mt-2 text-[11px] text-sky-200/80">
          This is not yet a resting Bitfinex order. The Pending orders table stays empty until an allowed
          chase bucket creates an executable exact structural limit.
        </p>
      ) : null}
    </section>
  );
}
