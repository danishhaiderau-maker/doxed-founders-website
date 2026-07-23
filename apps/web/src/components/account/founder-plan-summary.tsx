'use client';

import { useState } from 'react';
import { Check, CreditCard, Users } from 'lucide-react';
import {
  createFounderBillingPortal,
  createFounderBuilderCheckout,
  type FounderPlanCatalog,
  type FounderPlanEntitlement,
} from '@/lib/api';

type Props = {
  token: string;
  catalog: FounderPlanCatalog | null;
  entitlement: FounderPlanEntitlement | null;
};

function planName(plan: FounderPlanEntitlement['plan'] | undefined): string {
  if (plan === 'builder') return 'Founder Builder';
  if (plan === 'team') return 'Founder Team';
  return 'Founder Free';
}

export function FounderPlanSummary({ token, catalog, entitlement }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const builder = catalog?.plans.find((plan) => plan.id === 'builder');
  const plan = entitlement?.plan ?? 'free';

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

  return (
    <section className="border-b border-zinc-800 pb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Current plan</p>
          <h3 className="mt-1 text-xl font-semibold text-white">{planName(entitlement?.plan)}</h3>
          <p className="mt-1 text-sm text-zinc-400">
            {(entitlement?.weeklyWeightedUnitCap ?? 200_000).toLocaleString()} managed weighted units each week
            {entitlement?.teamName ? ` shared by ${entitlement.teamName}` : ''}.
          </p>
        </div>
        <span className="rounded-full border border-emerald-500/35 bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-200">
          Active
        </span>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        {[
          ['Project memory', true],
          ['Agent coordination', entitlement?.coordination ?? false],
          ['Remote control', entitlement?.remoteControl ?? false],
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
            {entitlement?.teamRole ? `${entitlement.teamRole} access` : 'Team access'}
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
