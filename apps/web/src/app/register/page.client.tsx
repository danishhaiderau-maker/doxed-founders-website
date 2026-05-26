'use client';

import Link from 'next/link';
import { signIn } from 'next-auth/react';
import { FormEvent, useState } from 'react';
import { OAuthButtons } from '@/components/oauth-buttons';
import { registerAccount } from '@/lib/api';

interface RegisterPageClientProps {
  oauthEnabled: { google: boolean };
}

export default function RegisterPageClient({ oauthEnabled }: RegisterPageClientProps) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await registerAccount({ email, password, name: name || undefined });
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
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Sign up with Google or email — includes $10,000 paper trading balance.
        </p>

        <div className="mt-8">
          <OAuthButtons callbackUrl="/paper-trading" enabled={oauthEnabled} />
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
