'use client';

import { formatPercent, formatUsd } from '@dcf/utils';

type BotRaw = Record<string, unknown>;

function str(v: unknown, fallback = '—') {
  if (v == null || v === '') return fallback;
  return String(v);
}

function num(v: unknown, digits = 1) {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function pct(v: unknown) {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${(n * 100).toFixed(2)}%`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 border-b border-zinc-800/60 py-2 text-sm last:border-0">
      <span className="text-zinc-500">{label}</span>
      <span className="max-w-[70%] text-right font-medium text-zinc-100">{value}</span>
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  if (value == null) return <p className="text-sm text-zinc-500">—</p>;
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return (
    <pre className="max-h-48 overflow-auto rounded-lg bg-black/40 p-3 text-xs text-zinc-300 whitespace-pre-wrap">
      {text.slice(0, 4000)}
    </pre>
  );
}

export function ResearchBotDetailDashboard({
  raw,
  updatedAt,
  onRefresh,
  autoRefresh,
  onAutoRefreshChange,
}: {
  raw: BotRaw;
  updatedAt: string;
  onRefresh: () => void;
  autoRefresh: boolean;
  onAutoRefreshChange: (v: boolean) => void;
}) {
  const sr = (raw.support_resistance ?? {}) as BotRaw;
  const lastAi = (raw.last_ai ?? {}) as BotRaw;
  const debug = (raw.debug_state ?? {}) as BotRaw;
  const funding = (raw.funding ?? {}) as BotRaw;
  const mc = (raw.market_context ?? {}) as BotRaw;
  const dataQuality = typeof raw.data_quality === 'number' ? raw.data_quality * 100 : null;

  const positions = (raw.positions ?? []) as BotRaw[];
  const orders = (raw.orders ?? []) as BotRaw[];
  const trades = (raw.trades ?? []) as BotRaw[];
  const aiHistory = (raw.ai_history ?? []) as BotRaw[];
  const signals = ((raw.signal_info as BotRaw)?.signals ?? []) as BotRaw[];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-zinc-800 bg-zinc-900/40 px-4 py-3">
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold hover:bg-violet-500"
        >
          Refresh now
        </button>
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => onAutoRefreshChange(e.target.checked)}
          />
          Auto-refresh every 60s
        </label>
        <span className="text-xs text-zinc-600">
          Last updated {new Date(updatedAt).toLocaleTimeString()}
        </span>
      </div>

      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm font-semibold text-emerald-200">
        Data source: {str(raw.data_source ?? raw.price_source, 'REAL BYBIT MARKET DATA (WS)')}
      </div>

      <Section title="Account & market">
        <Row label="Price" value={formatUsd(Number(raw.price ?? 0), 1)} />
        <Row label="Account balance" value={formatUsd(Number(raw.account_balance ?? 0), 2)} />
        <Row
          label="Daily PnL"
          value={
            raw.daily_pnl_usd != null
              ? `${formatUsd(Number(raw.daily_pnl_usd), 2)} net (UTC day)`
              : '—'
          }
        />
        <Row label="Equity" value={formatUsd(Number(raw.equity ?? raw.account_balance ?? 0), 2)} />
        <Row label="Regime" value={str(raw.regime)} />
      </Section>

      <Section title="Support / resistance (24h structural)">
        <Row label="Swing high" value={num(sr.swing_high, 1)} />
        <Row label="Swing low" value={num(sr.swing_low, 1)} />
        <Row label="Dist to resistance" value={pct(sr.dist_to_resistance)} />
        <Row label="Dist to support" value={pct(sr.dist_to_support)} />
        <Row label="SR zone" value={sr.sr_zone_pct != null ? `${num(sr.sr_zone_pct, 2)}%` : '—'} />
        <Row label="SR state" value={str(sr.sr_state)} />
        <Row label="SR bias" value={str(sr.sr_bias)} />
      </Section>

      <Section title="AI decision (last signal)">
        <Row label="AI status" value={str(lastAi.decision)} />
        <Row label="AI win prob" value={lastAi.win_prob != null ? `${lastAi.win_prob}%` : '—'} />
        <Row label="AI direction (raw)" value={str(lastAi.direction ?? lastAi.ai_direction_raw)} />
        <Row label="Final direction" value={str(lastAi.final_direction ?? raw.direction)} />
        <Row label="AI threshold" value={raw.ai_threshold != null ? `${raw.ai_threshold}%` : '—'} />
        <Row label="Edge threshold" value={str(raw.edge_threshold ?? debug.last_edge_score)} />
        <div className="mt-3">
          <p className="text-xs uppercase tracking-wider text-zinc-500">AI reason</p>
          <JsonBlock value={lastAi.comment ?? lastAi.reason} />
        </div>
      </Section>

      <Section title="Debug state">
        <Row label="Edge score" value={str(debug.last_edge_score ?? raw.last_edge)} />
        <Row label="Edge progress" value={str(debug.edge_progress)} />
        <Row label="Skip reason" value={str(debug.skip_reason)} />
        <Row label="Block reason" value={str(debug.last_block_reason)} />
        <Row label="Last pipeline" value={str(debug.last_pipeline_stage)} />
        <Row label="Signal cooldown" value={str(debug.signal_cooldown_status ?? debug.signal_cooldown)} />
        <Row label="AI cooldown" value={str(debug.ai_cooldown_status ?? debug.ai_cooldown)} />
        <Row label="Last fetch" value={str(raw.server_ts ?? raw.last_fetch)} />
        <Row label="Data quality" value={dataQuality != null ? `${dataQuality.toFixed(1)}%` : str(raw.data_quality)} />
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div>
            <p className="text-xs uppercase tracking-wider text-zinc-500">AI input</p>
            <JsonBlock value={raw.ai_input ?? raw.feature_snapshot} />
          </div>
          <div>
            <p className="text-xs uppercase tracking-wider text-zinc-500">Market context</p>
            <JsonBlock value={mc} />
          </div>
        </div>
      </Section>

      {funding && Object.keys(funding).length > 0 && (
        <Section title="Funding">
          <Row label="Rate (8h)" value={funding.rate_pct_per_8h != null ? `${funding.rate_pct_per_8h}%` : '—'} />
          <Row label="Interpretation" value={str(funding.interpretation)} />
          <Row label="Source" value={str(funding.source)} />
        </Section>
      )}

      {signals.length > 0 && (
        <Section title="Active signals">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="py-2 pr-2">Dir</th>
                  <th className="py-2 pr-2">Conf</th>
                  <th className="py-2 pr-2">Regime</th>
                  <th className="py-2 pr-2">Trigger</th>
                  <th className="py-2 pr-2">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {signals.slice(0, 8).map((s, i) => (
                  <tr key={i} className="border-t border-zinc-800/60">
                    <td className="py-2 pr-2">{str(s.dir)}</td>
                    <td className="py-2 pr-2">{str(s.conf)}</td>
                    <td className="py-2 pr-2">{str(s.regime)}</td>
                    <td className="py-2 pr-2">{str(s.trigger)}</td>
                    <td className="py-2 pr-2">{str(s.outcome)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Positions">
          {positions.length === 0 ? (
            <p className="text-sm text-zinc-500">No open positions.</p>
          ) : (
            positions.map((p, i) => (
              <Row
                key={i}
                label={`${str(p.side ?? p.dir)} · ${num(p.qty, 4)}`}
                value={`Entry ${num(p.entry)} · PnL ${formatPercent(Number(p.pnl_pct_margin ?? 0))}`}
              />
            ))
          )}
        </Section>

        <Section title="Pending orders">
          {orders.length === 0 ? (
            <p className="text-sm text-zinc-500">No pending orders.</p>
          ) : (
            orders.map((o, i) => (
              <Row
                key={i}
                label={`${str(o.side)} · ${str(o.status)}`}
                value={`Limit ${num(o.limit_price)} · ${num(o.age_min, 0)}m`}
              />
            ))
          )}
        </Section>
      </div>

      <Section title="Recent trades">
        {trades.length === 0 ? (
          <p className="text-sm text-zinc-500">No trades yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="py-2 pr-2">Time</th>
                  <th className="py-2 pr-2">Dir</th>
                  <th className="py-2 pr-2">Entry</th>
                  <th className="py-2 pr-2">Exit</th>
                  <th className="py-2 pr-2">PnL</th>
                </tr>
              </thead>
              <tbody>
                {[...trades].reverse().slice(0, 10).map((t, i) => (
                  <tr key={i} className="border-t border-zinc-800/60">
                    <td className="py-2 pr-2">{str(t.ts).slice(0, 19)}</td>
                    <td className="py-2 pr-2">{str(t.final_direction ?? t.dir)}</td>
                    <td className="py-2 pr-2">{num(t.entry)}</td>
                    <td className="py-2 pr-2">{num(t.exit)}</td>
                    <td className="py-2 pr-2">{t.pnl != null ? formatPercent(Number(t.pnl)) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="AI history (last 5)">
        {aiHistory.length === 0 ? (
          <p className="text-sm text-zinc-500">No AI calls logged yet.</p>
        ) : (
          <ul className="space-y-3">
            {[...aiHistory].reverse().slice(0, 5).map((h, i) => (
              <li key={i} className="rounded-lg border border-zinc-800/80 bg-black/20 p-3 text-xs">
                <p className="font-medium text-zinc-200">
                  {str(h.time).slice(0, 19)} · {str(h.decision)} · {str(h.final_direction ?? h.ai_direction_raw)}{' '}
                  {h.win_prob != null ? `· ${h.win_prob}%` : ''}
                </p>
                {h.comment != null && String(h.comment).trim() !== '' && (
                  <p className="mt-1 line-clamp-3 text-zinc-500">{String(h.comment).slice(0, 280)}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <p className="text-xs text-zinc-600">
        Read-only research view. LIVE ARM, leverage, and AI keys are configured on the bot service (admin infra) — not
        through follower accounts. Followers rent alerts + transparency with DDollar.
      </p>
    </div>
  );
}
