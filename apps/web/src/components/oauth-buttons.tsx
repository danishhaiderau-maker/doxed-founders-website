'use client';

import { signIn } from 'next-auth/react';

interface OAuthButtonsProps {
  callbackUrl?: string;
  enabled?: { google: boolean; twitter?: boolean };
  nextAuthUrl?: string;
  /** Prefer X login for paper trading / public track record */
  preferTwitter?: boolean;
}

export function OAuthButtons({
  callbackUrl = '/',
  enabled = { google: false, twitter: false },
  nextAuthUrl = 'http://localhost:3000',
  preferTwitter = false,
}: OAuthButtonsProps) {
  const googleRedirect = `${nextAuthUrl.replace(/\/$/, '')}/api/auth/callback/google`;
  const hasTwitter = Boolean(enabled.twitter);
  const hasGoogle = Boolean(enabled.google);

  if (!hasTwitter && !hasGoogle) {
    return (
      <div className="space-y-3">
        <button
          type="button"
          disabled
          className="flex w-full cursor-not-allowed items-center justify-center gap-2 rounded-lg border border-[var(--color-border)] bg-zinc-800/50 py-3 text-sm font-medium text-zinc-400"
        >
          <XIcon />
          Continue with X
        </button>
        <p className="rounded-lg bg-[var(--color-background)] p-3 text-xs text-[var(--color-muted)]">
          Add <code className="text-white">TWITTER_CLIENT_ID</code> and{' '}
          <code className="text-white">TWITTER_CLIENT_SECRET</code> on Vercel (same X app as API), or
          use email sign-up below.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {preferTwitter && hasTwitter && (
        <p className="text-center text-xs text-amber-200/90">
          Sign in with X — your paper trades link to your public handle. Talent over deep pockets.
        </p>
      )}
      {hasTwitter && (
        <button
          type="button"
          onClick={() => signIn('twitter', { callbackUrl })}
          className={`flex w-full items-center justify-center gap-2 rounded-lg py-3 text-sm font-semibold ${
            preferTwitter
              ? 'bg-white text-black hover:bg-zinc-100'
              : 'border border-[var(--color-border)] bg-[#0a0a0a] text-white hover:border-emerald-400'
          }`}
        >
          <XIcon />
          Continue with X
        </button>
      )}
      {hasGoogle && (
        <button
          type="button"
          onClick={() => signIn('google', { callbackUrl })}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--color-border)] bg-white py-3 text-sm font-medium text-zinc-900 hover:bg-zinc-100"
        >
          <GoogleIcon />
          Continue with Google
        </button>
      )}
      {hasGoogle && !hasTwitter && (
        <p className="text-[10px] text-[var(--color-muted)]">
          Google redirect: <code className="break-all text-white">{googleRedirect}</code>
        </p>
      )}
    </div>
  );
}

function XIcon() {
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  );
}
