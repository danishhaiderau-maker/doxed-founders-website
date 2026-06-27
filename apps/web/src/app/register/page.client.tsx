'use client';

import Link from 'next/link';
import { signIn } from 'next-auth/react';
import { FormEvent, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { OAuthButtons } from '@/components/oauth-buttons';
import { FounderPromoSignupBanner } from '@/components/founder-promo-signup-banner';
import { registerAccount } from '@/lib/api';
import { persistReferralCode, readReferralCode } from '@/lib/referral-storage';

interface RegisterPageClientProps {
  oauthEnabled: { google: boolean; twitter?: boolean };
  nextAuthUrl: string;
}

export default function RegisterPageClient({ oauthEnabled, nextAuthUrl }: RegisterPageClientProps) {
  const searchParams = useSearchParams();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [referralCode, setReferralCode] = useState<string | null>(null);

  useEffect(() => {
    const fromUrl = searchParams.get('ref');
    const code = persistReferralCode(fromUrl) ?? readReferralCode();
    setReferralCode(code);
  }, [searchParams]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await registerAccount({
        email,
        password,
        name: name || undefined,
        referralCode: referralCode ?? undefined,
      });
      const result = await signIn('credentials', {
        email,
        password,
        redirect: false,
        callbackUrl: '/paper-trading',
      });
      if (result?.error) {
        setError('Account created but sign-in failed. Try logging in.');
        return;
      }
      window.location.href = '/paper-trading';
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-16">
      <div className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-8">
        <Link href="/" className="text-sm text-[var(--color-muted)] hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-6 text-2xl font-bold">Create account</h1>
        <FounderPromoSignupBanner className="mt-4" />
        <p className="mt-3 rounded-lg border border-cyan-500/25 bg-cyan-950/20 px-3 py-2 text-xs text-cyan-100">
          <strong className="text-white">Sign in with X</strong> to unlock your public @handle, referrals, and 1-click
          Proof of Conviction. Email signups get a legacy platform ID (animal · country) until X is connected.
        </p>
        {referralCode && (
          <p className="mt-2 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
            Referral code <strong className="text-white">{referralCode}</strong> saved — rewards unlock when you sign in with X.
          </p>
        )}
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Most traders start with <strong className="text-white">Sign up with X</strong> — your paper
          portfolio links to your handle, and you can share Proof of Conviction instantly (auto-written
          posts, 1-click to X, better engagement with followers).
        </p>

        <div className="mt-8">
          <OAuthButtons
            callbackUrl="/paper-trading"
            enabled={oauthEnabled}
            nextAuthUrl={nextAuthUrl}
            preferTwitter
            referralCode={referralCode}
          />
        </div>

        <div className="my-6 flex items-center gap-3">
          <div className="h-px flex-1 bg-[var(--color-border)]" />
          <span className="text-xs text-[var(--color-muted)]">or email</span>
          <div className="h-px flex-1 bg-[var(--color-border)]" />
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="block text-sm">
            <span className="text-[var(--color-muted)]">Display name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
            />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--color-muted)]">Email</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
            />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--color-muted)]">Password (8+ characters)</span>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
            />
          </label>
          {error && <p className="text-sm text-red-300">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-[var(--color-accent)] py-3 text-sm font-medium text-white disabled:opacity-50"
          >
            {loading ? 'Creating account…' : 'Create account with email'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-[var(--color-muted)]">
          Already have an account?{' '}
          <Link href="/login" className="text-[var(--color-accent)] hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
