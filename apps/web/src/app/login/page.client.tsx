'use client';

import Link from 'next/link';
import { signIn } from 'next-auth/react';
import { FormEvent, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { OAuthButtons } from '@/components/oauth-buttons';

interface LoginPageClientProps {
  oauthEnabled: { google: boolean; twitter?: boolean };
  nextAuthUrl: string;
}

export default function LoginPageClient({ oauthEnabled, nextAuthUrl }: LoginPageClientProps) {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get('callbackUrl') ?? '/';
  const oauthError = searchParams.get('error');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const result = await signIn('credentials', {
      email,
      password,
      redirect: false,
      callbackUrl,
    });

    setLoading(false);

    if (result?.error) {
      setError('Invalid email or password');
      return;
    }

    window.location.href = callbackUrl;
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-16">
      <div className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-8">
        <Link href="/" className="text-sm text-[var(--color-muted)] hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-6 text-2xl font-bold">Sign in</h1>
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Best for traders: sign in with X so your paper record links to your public handle.
        </p>

        <div className="mt-8">
          {oauthError && (
            <p className="mb-4 rounded-lg border border-red-500/40 bg-red-950/30 p-3 text-sm text-red-200">
              {oauthErrorMessage(oauthError)}
            </p>
          )}
          <OAuthButtons
            callbackUrl={callbackUrl}
            enabled={oauthEnabled}
            nextAuthUrl={nextAuthUrl}
            preferTwitter
          />
        </div>

        <div className="my-6 flex items-center gap-3">
          <div className="h-px flex-1 bg-[var(--color-border)]" />
          <span className="text-xs text-[var(--color-muted)]">or email</span>
          <div className="h-px flex-1 bg-[var(--color-border)]" />
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
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
            <span className="text-[var(--color-muted)]">Password</span>
            <input
              type="password"
              required
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
            {loading ? 'Signing in…' : 'Sign in with email'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-[var(--color-muted)]">
          No account?{' '}
          <Link href="/register" className="text-[var(--color-accent)] hover:underline">
            Create one
          </Link>
        </p>
        {process.env.NODE_ENV === 'development' && (
          <p className="mt-4 rounded-lg bg-[var(--color-background)] p-3 text-xs text-[var(--color-muted)]">
            Local dev admin uses <code className="text-white">SEED_ADMIN_PASSWORD</code> from your{' '}
            <code className="text-white">.env</code> (not shown here).
          </p>
        )}
      </div>
    </main>
  );
}

function oauthErrorMessage(code: string): string {
  switch (code) {
    case 'OAuthCallback':
      return 'X sign-in failed after authorization. We use your X API Key + Secret for login (OAuth 1.0a). In the X Developer Portal, enable 3-legged OAuth and set callback https://doxxedcrypto.digital/api/auth/callback/twitter';
    case 'OAuthSignin':
    case 'twitter':
      return 'Could not start X sign-in. Check TWITTER_API_KEY and TWITTER_API_SECRET on Vercel (from X Developer Portal → Keys and tokens).';
    case 'AccessDenied':
      return 'X sign-in was denied or our server could not finish linking your account. Try again, or use Google / email login.';
    case 'Configuration':
      return 'Login is misconfigured on the server (NEXTAUTH_URL / NEXTAUTH_SECRET). Contact support.';
    default:
      return `Sign-in error (${code}). Try again or use Google / email login.`;
  }
}
