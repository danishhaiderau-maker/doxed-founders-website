import { COPY_RELAY_SIM_RECONCILE_ALERT_BTC } from './copy-relay-sim';

/** Inputs shared by relay sim + live copy fidelity panels. */
export type ShowcaseSyncScoreInput = {
  botConnected?: boolean;
  reconcile?: {
    deltaBtc?: number;
    alert?: boolean;
  } | null;
  fidelity?: {
    summary?: {
      tradeCount?: number;
      unmatchedRelayCount?: number;
      unmatchedShowcaseCount?: number;
      unmatchedShowcaseOfflineCount?: number;
      missingShowcaseEntryCount?: number;
      missingShowcaseExitCount?: number;
      maxEntryDeltaPct?: number | null;
    };
  } | null;
  lifecycle?: {
    integrityPct?: number;
    sampleSize?: number;
  } | null;
};

export type ShowcaseSyncScore = {
  pct: number;
  label: string;
  autoSyncing: boolean;
  healthy: boolean;
  issues: string[];
  notes: string[];
};

const DEFAULT_AUTO_STOP_THRESHOLD_PCT = 98;

export function getDefaultShowcaseSyncStopThreshold(): number {
  return DEFAULT_AUTO_STOP_THRESHOLD_PCT;
}

/** Weighted sync score vs global showcase bot :7002 (0–100). */
export function computeShowcaseSyncScore(input: ShowcaseSyncScoreInput): ShowcaseSyncScore {
  const issues: string[] = [];
  const notes: string[] = [];

  if (input.botConnected === false) {
    issues.push('Showcase bot offline — relay cannot mirror new signals');
    return {
      pct: 0,
      label: 'Offline',
      autoSyncing: false,
      healthy: false,
      issues,
      notes,
    };
  }

  let botWeight = 35;
  let reconcileWeight = 25;
  let fidelityWeight = 25;
  let lifecycleWeight = 15;

  let botScore = 100;
  let reconcileScore = 100;
  let fidelityScore = 100;
  let lifecycleScore = 100;

  const reconcile = input.reconcile;
  if (reconcile) {
    const delta = reconcile.deltaBtc ?? 0;
    const deltaBad =
      reconcile.alert ?? Math.abs(delta) > COPY_RELAY_SIM_RECONCILE_ALERT_BTC;
    if (deltaBad) {
      reconcileScore = Math.max(0, 100 - Math.min(100, Math.abs(delta) * 10_000));
      issues.push(`Ledger desync Δ ${delta >= 0 ? '+' : ''}${delta.toFixed(5)} BTC`);
    }
  }

  const fidelity = input.fidelity?.summary;
  if (fidelity) {
    // Only genuine misses (relay was active but failed to copy) count against
    // the score. Trades missed while the relay was offline are reported as a
    // non-penalizing info issue — they reflect past downtime, not current
    // sync quality, and would otherwise permanently tank the score.
    const orphans =
      (fidelity.unmatchedRelayCount ?? 0) + (fidelity.unmatchedShowcaseCount ?? 0);
    const offlineMisses = fidelity.unmatchedShowcaseOfflineCount ?? 0;
    const gaps =
      (fidelity.missingShowcaseEntryCount ?? 0) + (fidelity.missingShowcaseExitCount ?? 0);
    const matched = fidelity.tradeCount ?? 0;
    const total = matched + orphans;
    if (total > 0) {
      fidelityScore = Math.round((matched / total) * 1000) / 10;
    }
    if (orphans > 0) {
      issues.push(`${orphans} trade ID orphan(s) vs showcase`);
    }
    if (offlineMisses > 0) {
      notes.push(`${offlineMisses} trade(s) missed while relay offline (not scored)`);
    }
    if (gaps > 0) {
      fidelityScore = Math.min(fidelityScore, Math.max(0, 100 - gaps * 8));
      issues.push(`${gaps} showcase price gap(s) in fidelity audit`);
    }
    if (
      fidelity.maxEntryDeltaPct != null &&
      Math.abs(fidelity.maxEntryDeltaPct) > 0.15
    ) {
      fidelityScore = Math.min(fidelityScore, 85);
      issues.push(`Entry slippage up to ${fidelity.maxEntryDeltaPct.toFixed(2)}%`);
    }
  }

  const lifecycle = input.lifecycle;
  if (lifecycle && (lifecycle.sampleSize ?? 0) > 0) {
    lifecycleScore = lifecycle.integrityPct ?? 100;
    if (lifecycleScore < 100) {
      issues.push(`Lifecycle integrity ${lifecycleScore}%`);
    }
  } else {
    lifecycleWeight = 0;
    botWeight += 5;
    fidelityWeight += 10;
  }

  const totalWeight = botWeight + reconcileWeight + fidelityWeight + lifecycleWeight;
  const pct = Math.round(
    ((botScore * botWeight +
      reconcileScore * reconcileWeight +
      fidelityScore * fidelityWeight +
      lifecycleScore * lifecycleWeight) /
      totalWeight) *
      100,
  ) / 100;

  const clamped = Math.max(0, Math.min(100, pct));
  const healthy = clamped >= DEFAULT_AUTO_STOP_THRESHOLD_PCT && issues.length === 0;

  let label: string;
  if (clamped >= 99.9) label = 'Fully synced';
  else if (clamped >= 99) label = 'Excellent';
  else if (clamped >= 98) label = 'Good';
  else if (clamped >= 90) label = 'Drifting';
  else label = 'Out of sync';

  return {
    pct: clamped,
    label,
    autoSyncing: true,
    healthy,
    issues,
    notes,
  };
}

export function formatShowcaseSyncPct(pct: number): string {
  if (pct >= 99.995) return '100%';
  if (pct >= 99) return `${pct.toFixed(2)}%`;
  return `${pct.toFixed(1)}%`;
}
