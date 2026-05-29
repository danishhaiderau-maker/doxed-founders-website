'use client';

import Link from 'next/link';
import { signIn } from 'next-auth/react';
import { startAuthentication } from '@simplewebauthn/browser';
import { FormEvent, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { OAuthButtons } from '@/components/oauth-buttons';
import {
  loginAccount,
  passkeyLoginOptions,
  passkeyLoginVerify,
  verify2FaLogin,
} from '@/lib/api';

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
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [methods, setMethods] = useState<string[]>([]);
  const [totpCode, setTotpCode] = useState('');
  const [recoveryCode, setRecoveryCode] = useState('');

  async function finishLogin(accessToken: string) {
    const result = await signIn('credentials', {
      accessToken,
      redirect: false,
      callbackUrl,
    });
    if (result?.error) {
      setError('Session could not be created');
      return;
    }
    window.location.href = callbackUrl;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const data = await loginAccount({ email, password });
      if (data.requires2fa && data.pendingToken) {
        setPendingToken(data.pendingToken);
        setMethods(data.methods ?? ['totp']);
        setLoading(false);
        return;
      }
      if (!data.accessToken) {
        setError('Invalid email or password');
        setLoading(false);
        return;
      }
      await finishLogin(data.accessToken);
    } catch {
      setError('Invalid email or password');
      setLoading(false);
    }
  }

  async function handleVerify2Fa(e: FormEvent) {
    e.preventDefault();
    if (!pendingToken) return;
    setLoading(true);
    setError(null);
    try {
      const data = await verify2FaLogin(
        pendingToken,
        totpCode || undefined,
        recoveryCode || undefined,
      );
      if (!data.accessToken) {
        setError('Verification failed');
        setLoading(false);
        return;
      }
      await finishLogin(data.accessToken);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed');
      setLoading(false);
    }
  }

  async function handlePasskeyLogin() {
    if (!pendingToken) return;
    setLoading(true);
    setError(null);
    try {
      const { options, passkeyToken } = await passkeyLoginOptions(pendingToken);
      const assertion = await startAuthentication({ optionsJSON: options as never });
      const data = await passkeyLoginVerify(passkeyToken, assertion as never);
      if (!data.accessToken) {
        setError('Passkey verification failed');
        setLoading(false);
        return;
      }
      await finishLogin(data.accessToken);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Passkey login failed');
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-16">
      <div className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-8">
        <Link href="/" className="text-sm text-[var(--color-muted)] hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-6 text-2xl font-bold">Sign in</h1>

        {!pendingToken ? (
          <>
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
          </>
        ) : (
          <form onSubmit={handleVerify2Fa} className="mt-8 space-y-4">
            <p className="text-sm text-[var(--color-muted)]">
              Two-factor authentication required for this account.
            </p>
            {methods.includes('totp') && (
              <label className="block text-sm">
                <span className="text-[var(--color-muted)]">Authenticator code</span>
                <input
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="6 digits"
                  className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm"
                />
              </label>
            )}
            {methods.includes('recovery') && (
              <label className="block text-sm">
                <span className="text-[var(--color-muted)]">Or recovery code</span>
                <input
                  value={recoveryCode}
                  onChange={(e) => setRecoveryCode(e.target.value)}
                  placeholder="XXXX-XXXX-XXXX-XXXX"
                  className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm"
                />
              </label>
            )}
            {methods.includes('passkey') && (
              <button
                type="button"
                onClick={handlePasskeyLogin}
                disabled={loading}
                className="w-full rounded-lg border border-sky-500/40 py-2.5 text-sm text-sky-200"
              >
                Use passkey instead
              </button>
            )}
            {error && <p className="text-sm text-red-300">{error}</p>}
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-[var(--color-accent)] py-3 text-sm font-medium text-white disabled:opacity-50"
            >
              {loading ? 'Verifying…' : 'Continue'}
            </button>
            <button
              type="button"
              onClick={() => {
                setPendingToken(null);
                setTotpCode('');
                setRecoveryCode('');
              }}
              className="w-full text-xs text-[var(--color-muted)] hover:text-white"
            >
              ← Back
            </button>
          </form>
        )}

        <p className="mt-6 text-center text-sm text-[var(--color-muted)]">
          No account?{' '}
          <Link href="/register" className="text-[var(--color-accent)] hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </main>
  );
}

function oauthErrorMessage(code: string): string {
  switch (code) {
    case 'OAuthCallback':
      return 'OAuth sign-in failed after authorization.';
    case 'OAuthSignin':
    case 'twitter':
      return 'Could not start X sign-in.';
    case 'AccessDenied':
      return 'Sign-in was denied.';
    case 'Configuration':
      return 'Login is misconfigured on the server.';
    default:
      return `Sign-in error (${code}). Try again.`;
  }
}
