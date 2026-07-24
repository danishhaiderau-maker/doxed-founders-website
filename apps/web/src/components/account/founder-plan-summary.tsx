'use client';

import { useState } from 'react';
import { Check, CreditCard, RefreshCw, Users } from 'lucide-react';
import {
  createFounderBillingPortal,
  createFounderBuilderCheckout,
  type FounderPlanCatalog,
  type FounderPlanEntitlement,
} from '@/lib/api';
import {
  hasResolvedFounderPlan,
  type FounderPlanLoadState,
} from '@/components/account/founder-plan-account-state';

type Props = {
  token: string;
  catalog: FounderPlanCatalog | null;
  entitlement: FounderPlanEntitlement | null;
  loadState: FounderPlanLoadState;
  loadError: string | null;
  onRetry: () => void;
};

function planName(plan: FounderPlanEntitlement['plan'] | undefined): string {
  if (plan === 'builder') return 'Founder Builder';
  if (plan === 'team') return 'Founder Team';
  return 'Founder Free';
}

export function FounderPlanSummary({
  token,
  catalog,
  entitlement,
  loadState,
  loadError,
  onRetry,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function openBilling(action: 'checkout' | 'portal') {
    setBusy(true);
    setError(null);
    try {
      const result = action === 'checkout'
        ? await createFounderBuilderCheckout(token)
        : await createFounderBillingPortal(token);
      window.location.assign(result.url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Billing could not be opened.');
      setBusy(false);
    }
  }

  if (loadState === 'loading') {
    return (
      <section className="border-b border-zinc-800 pb-6" aria-live="polite">
        <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Current plan</p>
        <div className="mt-2 h-7 w-44 animate-pulse rounded bg-zinc-800" />
        <p className="mt-2 text-sm text-zinc-500">Checking your live entitlement and billing status...</p>
      </section>
    );
  }

  if (!catalog || !entitlement || !hasResolvedFounderPlan(loadState, catalog, entitlement)) {
    return (
      <section className="border-b border-zinc-800 pb-6" role="alert">
        <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Current plan</p>
        <h3 className="mt-1 text-xl font-semibold text-white">Plan unavailable</h3>
        <p className="mt-2 max-w-xl text-sm text-amber-200/90">
          {loadError ?? 'Founder OS could not confirm your plan. No plan or allowance has been assumed.'}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-4 py-2 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
        >
          <RefreshCw className="h-4 w-4" aria-hidden />
          Retry
        </button>
      </section>
    );
  }

  const builder = catalog.plans.find((candidate) => candidate.id === 'builder');
  const plan = entitlement.plan;

  return (
    <section className="border-b border-zinc-800 pb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Current plan</p>
          <h3 className="mt-1 text-xl font-semibold text-white">{planName(entitlement.plan)}</h3>
          <p className="mt-1 text-sm text-zinc-400">
            {entitlement.weeklyWeightedUnitCap.toLocaleString()} managed weighted units each week
            {entitlement.teamName ? ` shared by ${entitlement.teamName}` : ''}.
          </p>
        </div>
        <span className="rounded-full border border-emerald-500/35 bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-200">
          Active
        </span>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        {[
          ['Project memory', true],
          ['Agent coordination', entitlement.coordination],
          ['Remote control', entitlement.remoteControl],
        ].map(([label, enabled]) => (
          <div key={String(label)} className="flex items-center gap-2 border-t border-zinc-800 py-2 text-sm text-zinc-300">
            <Check className={`h-4 w-4 ${enabled ? 'text-emerald-300' : 'text-zinc-700'}`} aria-hidden />
            <span className={enabled ? '' : 'text-zinc-600'}>{label}</span>
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {plan === 'free' ? (
          <button
            type="button"
            disabled={busy || !builder?.checkoutAvailable}
            onClick={() => void openBilling('checkout')}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
          >
            <CreditCard className="h-4 w-4" aria-hidden />
            {builder?.checkoutAvailable ? 'Upgrade to Builder for $35/month' : 'Builder checkout opening soon'}
          </button>
        ) : plan === 'builder' ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void openBilling('portal')}
            className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-4 py-2 text-sm font-semibold text-zinc-200 hover:border-zinc-500 disabled:opacity-50"
          >
            <CreditCard className="h-4 w-4" aria-hidden />
            Manage billing
          </button>
        ) : (
          <span className="inline-flex items-center gap-2 text-sm text-zinc-400">
            <Users className="h-4 w-4" aria-hidden />
            {entitlement.teamRole ? `${entitlement.teamRole} access` : 'Team access'}
          </span>
        )}
        {builder?.weeklyWeightedUnits ? (
          <span className="text-xs text-zinc-500">
            Builder includes {builder.weeklyWeightedUnits.toLocaleString()} units each week.
          </span>
        ) : null}
      </div>
      {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
    </section>
  );
}
