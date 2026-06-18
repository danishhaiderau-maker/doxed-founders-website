'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatDdollar, POINT_ACTIONS } from '@dcf/utils';
import { AccountReferralSummary, claimReferralCode, fetchAccountReferral } from '@/lib/api';
import { readReferralCode, clearReferralCode } from '@/lib/referral-storage';

type Props = {
  accessToken: string;
};

export function ReferralPanel({ accessToken }: Props) {
  const [summary, setSummary] = useState<AccountReferralSummary | null>(null);
  const [claimCode, setClaimCode] = useState('');
  const [claimMsg, setClaimMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchAccountReferral(accessToken);
      setSummary(data);
    } catch {
      setSummary(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
    const stored = readReferralCode();
    if (stored) setClaimCode(stored);
  }, [load]);

  const shareUrl =
    typeof window !== 'undefined' && summary
      ? `${window.location.origin}${summary.sharePath}`
      : '';

  async function handleClaim() {
    if (!claimCode.trim()) return;
    setBusy(true);
    setClaimMsg(null);
    try {
      const data = await claimReferralCode(claimCode.trim(), accessToken);
      setSummary(data);
      clearReferralCode();
      setClaimMsg('Referral code applied — rewards pay out once you sign in with X.');
    } catch (err) {
      setClaimMsg(err instanceof Error ? err.message : 'Could not apply code');
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (!summary) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5 text-sm text-zinc-500">
        Loading referral program…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-emerald-500/25 bg-gradient-to-br from-emerald-950/25 to-zinc-950 p-5">
        <h2 className="text-lg font-semibold text-white">Invite friends — earn DDollar</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Share your link. When someone joins with X, you earn{' '}
          <strong className="text-emerald-300">{formatDdollar(summary.rewardStandard)}</strong>
          {' '}(
          <strong className="text-cyan-300">{formatDdollar(summary.rewardBlueVerified)}</strong> if they have a
          blue-verified X account).
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <code className="rounded-lg border border-zinc-700 bg-black/40 px-3 py-2 text-sm font-semibold text-white">
            {summary.code}
          </code>
          <button
            type="button"
            onClick={() => void copyLink()}
            className="rounded-lg border border-emerald-500/40 bg-emerald-950/40 px-3 py-2 text-xs font-semibold text-emerald-200 hover:bg-emerald-900/40"
          >
            {copied ? 'Copied!' : 'Copy invite link'}
          </button>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Stat label="People invited" value={String(summary.referredCount)} />
          <Stat label="Awaiting X login" value={String(summary.pendingCount)} />
          <Stat label="DDollar earned" value={formatDdollar(summary.earnedDdollar)} />
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
        <h3 className="text-sm font-semibold text-white">Have a referral code?</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Enter a friend&apos;s code within 7 days of signup. Rewards unlock when you authenticate with X.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            value={claimCode}
            onChange={(e) => setClaimCode(e.target.value.toUpperCase())}
            placeholder="ABCDEF12"
            className="min-w-[10rem] flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-white"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleClaim()}
            className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black disabled:opacity-50"
          >
            {busy ? 'Applying…' : 'Apply code'}
          </button>
        </div>
        {claimMsg && <p className="mt-2 text-xs text-zinc-400">{claimMsg}</p>}
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
        <h3 className="text-sm font-semibold text-white">Referral rules &amp; accounting</h3>
        <ul className="mt-3 space-y-3 text-sm text-zinc-300">
          {summary.rules.map((rule) => (
            <li key={rule.title}>
              <p className="font-medium text-white">{rule.title}</p>
              <p className="text-xs text-zinc-500">{rule.detail}</p>
              {rule.amount != null && (
                <p className="mt-0.5 text-xs font-semibold text-emerald-400">
                  +{rule.amount.toLocaleString()} DDollar
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
        <h3 className="text-sm font-semibold text-white">All DDollar earn rates</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Every credit is logged in your wallet ledger. Amounts below are per action unless marked one-time.
        </p>
        <ul className="mt-3 max-h-64 divide-y divide-zinc-800 overflow-y-auto text-sm">
          {POINT_ACTIONS.filter((a) => a.amount > 0).map((action) => (
            <li key={action.key} className="flex items-center justify-between py-2">
              <span className="text-zinc-300">{action.label}</span>
              <span className="font-semibold text-emerald-400">+{action.amount.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-black/20 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-white">{value}</p>
    </div>
  );
}
