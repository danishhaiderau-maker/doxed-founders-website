'use client';

import Link from 'next/link';
import { Suspense } from 'react';
import { useSession } from 'next-auth/react';

/**
 * Lightweight shell. Builder settings used to host four tabs (downloads / ai /
 * infra / security). Pairing, downloads, AI providers, and infrastructure
 * connections now all live in the Founder IDE app — this page only keeps the
 * Security summary (which links to the Account page) and points everything
 * else at /founder-ide.
 *
 * Old tab query params (downloads / ai / infra) collapse to the default view
 * so existing bookmarks do not 404.
 */
export function BuilderSettingsPanel() {
  return (
    <div className='space-y-8'>
      <section className='rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6'>
        <div className='flex flex-wrap items-start justify-between gap-4'>
          <div>
            <h2 className='text-lg font-semibold text-white'>Moved to Founder IDE</h2>
            <p className='mt-1 max-w-2xl text-sm text-zinc-400'>
              Pairing your device, connecting AI providers, and infrastructure credentials are now managed inside
              the Founder IDE app. The website is for marketing and remote control only.
            </p>
          </div>
          <Link
            href='/founder-ide'
            className='inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-violet-500'
          >
            Open Founder IDE →
          </Link>
        </div>

        <div className='mt-5 grid gap-3 sm:grid-cols-3'>
          <Link
            href='/founder-ide'
            className='rounded-xl border border-zinc-800 bg-zinc-950/40 px-4 py-3 text-sm text-zinc-200 transition hover:border-violet-500/40 hover:bg-violet-950/10 hover:text-white'
          >
            <span className='block font-medium'>Pair your device</span>
            <span className='mt-0.5 block text-xs text-zinc-500'>Generate a pairing code</span>
          </Link>
          <Link
            href='/founder-ide/byok'
            className='rounded-xl border border-zinc-800 bg-zinc-950/40 px-4 py-3 text-sm text-zinc-200 transition hover:border-violet-500/40 hover:bg-violet-950/10 hover:text-white'
          >
            <span className='block font-medium'>AI providers (BYOK)</span>
            <span className='mt-0.5 block text-xs text-zinc-500'>OpenAI · Anthropic · Google · +7</span>
          </Link>
          <Link
            href='/founder-ide'
            className='rounded-xl border border-zinc-800 bg-zinc-950/40 px-4 py-3 text-sm text-zinc-200 transition hover:border-violet-500/40 hover:bg-violet-950/10 hover:text-white'
          >
            <span className='block font-medium'>Infrastructure</span>
            <span className='mt-0.5 block text-xs text-zinc-500'>Vercel · Railway · Neon · GitHub</span>
          </Link>
        </div>
      </section>

      <section className='rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6'>
        <div className='flex flex-wrap items-start justify-between gap-4'>
          <div>
            <h2 className='text-lg font-semibold text-white'>Security</h2>
            <p className='mt-1 max-w-2xl text-sm text-zinc-400'>
              Account security lives on the Account page. Use the full Security panel for password, 2FA, passkeys,
              and wallet management.
            </p>
          </div>
          <span className='rounded-full bg-amber-500/15 px-3 py-1 text-xs font-semibold text-amber-200'>
            On Account page
          </span>
        </div>

        <div className='mt-5 grid gap-3 sm:grid-cols-2'>
          {[
            { label: 'Password', href: '/account?tab=security#password' },
            { label: 'Two-factor (2FA)', href: '/account?tab=security#2fa' },
            { label: 'Passkeys', href: '/account?tab=security#passkeys' },
            { label: 'Wallet', href: '/account?tab=security#wallet' },
          ].map((item) => (
            <a
              key={item.label}
              href={item.href}
              className='flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950/40 px-4 py-3 text-sm text-zinc-200 transition hover:border-violet-500/40 hover:bg-violet-950/10 hover:text-white'
            >
              <span>{item.label}</span>
              <span className='text-zinc-500'>→</span>
            </a>
          ))}
        </div>

        <div className='mt-4 flex flex-wrap gap-3'>
          <a
            href='/account?tab=security'
            className='rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-500'
          >
            Open full Security panel
          </a>
          <a
            href='/privacy'
            className='rounded-xl border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition hover:border-zinc-500 hover:text-white'
          >
            Privacy page
          </a>
        </div>
      </section>
    </div>
  );
}

/**
 * Default export wrapper for pages that still render BuilderSettingsPanel —
 * keeps the Suspense boundary and the auth gate.
 */
export function BuilderSettingsPanelWithAuth() {
  const { data: session } = useSession();

  if (!session?.accessToken) {
    return (
      <div className='rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100'>
        <Link href='/login?callbackUrl=/settings/builder' className='font-semibold underline'>
          Sign in
        </Link>{' '}
        to manage your integrations.
      </div>
    );
  }

  return (
    <Suspense fallback={<p className='text-sm text-zinc-500'>Loading…</p>}>
      <BuilderSettingsPanel />
    </Suspense>
  );
}
