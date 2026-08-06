'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';

/**
 * /pricing — public pricing page. This is where plan info lives. The
 * /founder-ide page links here from the Upgrade CTA; chat surfaces link
 * here instead of inlining plan details.
 */
export default function PricingPage() {
  return (
    <main className='min-h-screen bg-[#050508] text-zinc-100'>
      <header className='border-b border-zinc-800'>
        <div className='mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5'>
          <div>
            <SiteBrand className='text-sm' />
            <h1 className='mt-1 text-2xl font-bold text-white'>Pricing</h1>
            <p className='text-sm text-zinc-400'>
              Founder IDE plans — pick the model access that fits your pace.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className='mx-auto max-w-6xl px-6 py-12'>
        {/* HERO */}
        <section className='mb-12 text-center'>
          <h2 className='text-4xl font-bold tracking-tight text-white sm:text-5xl'>
            Code with your own AI. Locally.
          </h2>
          <p className='mt-4 text-lg text-zinc-400'>
            Bring your own keys or run local models. Pay only for what you actually use.
          </p>
        </section>

        {/* PRICING GRID */}
        <section className='mb-12'>
          <div className='grid gap-6 md:grid-cols-2 lg:grid-cols-4'>
            {/* FREE */}
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6'>
              <h3 className='font-semibold text-white'>Free</h3>
              <p className='mt-2 text-3xl font-bold text-white'>$0</p>
              <p className='mt-1 text-xs text-zinc-500'>forever</p>
              <ul className='mt-5 space-y-2 text-sm text-zinc-300'>
                <li>Founder IDE app</li>
                <li><strong className='text-white'>5 BYOK cloud models</strong> (OpenAI, Anthropic, DeepSeek, +2)</li>
                <li><strong className='text-white'>Unlimited local models</strong> (Qwen, Llama, GGUF)</li>
                <li>Tab autocomplete (always works)</li>
                <li>Limited daily cloud quota</li>
                <li>Pair with doxxedcrypto.digital</li>
              </ul>
              <p className='mt-4 text-xs text-zinc-500'>
                At limit: cloud pauses, <strong className='text-zinc-300'>local mode keeps working</strong>.
              </p>
            </div>

            {/* PRO */}
            <div className='rounded-2xl border border-violet-700 bg-zinc-950/40 p-6 ring-1 ring-violet-700/50'>
              <h3 className='font-semibold text-white'>Pro</h3>
              <p className='mt-2 text-3xl font-bold text-white'>$20<span className='text-base font-normal text-zinc-400'>/mo</span></p>
              <p className='mt-1 text-xs text-violet-300'>most popular</p>
              <ul className='mt-5 space-y-2 text-sm text-zinc-300'>
                <li>Everything in Free</li>
                <li>5h burst window (~50–200 msgs)</li>
                <li>Weekly ceiling (~1,500 msgs)</li>
                <li><strong className='text-white'>Auto Router</strong> (cost-optimized model routing)</li>
                <li><strong className='text-white'>Borrow next week</strong> (50%, max 2 wks, auto)</li>
                <li>Signed update channel</li>
              </ul>
              <p className='mt-4 text-xs text-zinc-500'>
                At limit: auto-borrow → degraded mode → upgrade nudge.
              </p>
              <Link
                href='/founder-ide'
                className='mt-5 inline-flex w-full justify-center rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-violet-500'
              >
                Get started
              </Link>
            </div>

            {/* MAX */}
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6'>
              <h3 className='font-semibold text-white'>Max</h3>
              <p className='mt-2 text-3xl font-bold text-white'>$60<span className='text-base font-normal text-zinc-400'>/mo</span></p>
              <p className='mt-1 text-xs text-zinc-500'>power users</p>
              <ul className='mt-5 space-y-2 text-sm text-zinc-300'>
                <li>Everything in Pro</li>
                <li><strong className='text-white'>3× Pro quota</strong></li>
                <li>Priority routing</li>
                <li>Signed update channel</li>
                <li>Early access to new models</li>
              </ul>
              <p className='mt-4 text-xs text-zinc-500'>
                Same borrow rules, 3× headroom.
              </p>
            </div>

            {/* TEAM */}
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6'>
              <h3 className='font-semibold text-white'>Team</h3>
              <p className='mt-2 text-3xl font-bold text-white'>$40<span className='text-base font-normal text-zinc-400'>/seat/mo</span></p>
              <p className='mt-1 text-xs text-zinc-500'>for orgs</p>
              <ul className='mt-5 space-y-2 text-sm text-zinc-300'>
                <li><strong className='text-white'>10 BYOK models</strong> (per seat)</li>
                <li><strong className='text-white'>Unlimited local models + Tab</strong> (per seat)</li>
                <li><strong className='text-white'>Pair with doxxedcrypto.digital</strong> (per seat)</li>
                <li>Per-seat Pro quota</li>
                <li>Org-wide credit pool</li>
                <li>Admin controls</li>
                <li>Spend caps &amp; alerts</li>
                <li>SSO + audit log</li>
              </ul>
            </div>
          </div>

          {/* On-demand overage line */}
          <div className='mt-8 rounded-xl border border-zinc-800 bg-zinc-950/40 p-4 text-center'>
            <p className='text-sm text-zinc-400'>
              <strong className='text-zinc-200'>All plans:</strong> on-demand overage at API rates with{' '}
              <strong className='text-zinc-200'>0% markup</strong> + user-set spend cap +{' '}
              <span className='text-zinc-200'>75 / 90 / 100% alerts</span>.
            </p>
          </div>

          {/* SECOND BRAIN ADD-ON (GLM) */}
          <div className='mt-4 rounded-xl border border-violet-900/40 bg-violet-950/20 p-4 text-center'>
            <p className='text-sm text-zinc-300'>
              <strong className='text-violet-200'>Second Brain add-on:</strong>{' '}
              GLM-powered critical review of agent output - available as a premium add-on,
              used sparingly to keep costs down. GLM is <em>not</em> part of general chat or any per-seat tier.
            </p>
          </div>
        </section>
        {/* COMPARISON */}
        <section className='mb-12'>
          <div className='overflow-hidden rounded-2xl border border-zinc-800'>
            <div className='border-b border-zinc-800 bg-zinc-950/60 px-5 py-3'>
              <p className='text-xs font-semibold uppercase tracking-wide text-zinc-400'>
                Founder IDE vs Cursor — honest comparison
              </p>
            </div>
            <div className='overflow-x-auto'>
              <table className='w-full text-left text-sm'>
                <thead>
                  <tr className='border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500'>
                    <th className='px-5 py-2.5 font-medium'>Capability</th>
                    <th className='px-5 py-2.5 font-medium text-violet-300'>Founder</th>
                    <th className='px-5 py-2.5 font-medium text-zinc-400'>Cursor</th>
                  </tr>
                </thead>
                <tbody className='divide-y divide-zinc-900 text-zinc-300'>
                  <tr>
                    <td className='px-5 py-2.5'>BYOK models on Free</td>
                    <td className='px-5 py-2.5 font-semibold text-white'>5</td>
                    <td className='px-5 py-2.5 text-zinc-400'>1</td>
                  </tr>
                  <tr>
                    <td className='px-5 py-2.5'>Local models</td>
                    <td className='px-5 py-2.5 font-semibold text-white'>Unlimited</td>
                    <td className='px-5 py-2.5 text-zinc-400'>None</td>
                  </tr>
                  <tr>
                    <td className='px-5 py-2.5'>Tab at zero quota</td>
                    <td className='px-5 py-2.5 font-semibold text-white'>Yes</td>
                    <td className='px-5 py-2.5 text-zinc-400'>No</td>
                  </tr>
                  <tr>
                    <td className='px-5 py-2.5'>Borrow forward</td>
                    <td className='px-5 py-2.5 font-semibold text-white'>Yes</td>
                    <td className='px-5 py-2.5 text-zinc-400'>No</td>
                  </tr>
                  <tr>
                    <td className='px-5 py-2.5'>Auto Router (cost-optimized)</td>
                    <td className='px-5 py-2.5 font-semibold text-white'>Yes</td>
                    <td className='px-5 py-2.5 text-zinc-400'>Yes</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section className='rounded-xl border border-zinc-800 bg-zinc-950/40 p-6 text-center text-sm text-zinc-400'>
          <p>
            Founder Pro at $20/mo is viable because of Auto Router + local fallback. Free is viable because BYOK
            means users pay their own providers — we don&apos;t subsidize your tokens.
          </p>
          <div className='mt-5'>
            <Link
              href='/founder-ide'
              className='inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-violet-500'
            >
              ← Back to Founder IDE
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}
