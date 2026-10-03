'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';

/**
 * /founder-ide/local — destination for the "Local" option in the chat composer
 * AI dropdown. Model setup lives in the desktop app, not this web page.
 */
export default function FounderIdeLocalPage() {
  return (
    <main className='min-h-screen bg-[#050508] text-zinc-100'>
      <header className='border-b border-zinc-800'>
        <div className='mx-auto flex max-w-5xl flex-col gap-4 px-6 py-5'>
          <div>
            <SiteBrand className='text-sm' />
            <h1 className='mt-1 text-2xl font-semibold tracking-tight text-white'>Local models</h1>
            <p className='text-sm text-zinc-400'>
              Run Qwen, Llama, or GGUF models on your laptop. No cloud key required.
            </p>
          </div>
          <div className='w-full min-w-0'><SiteNav /></div>
        </div>
      </header>

      <div className='mx-auto max-w-3xl px-6 py-12'>
        <div className='rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8'>
          <h2 className='text-lg font-semibold text-white'>Choose a model in the desktop app</h2>
          <p className='mt-2 text-sm leading-relaxed text-zinc-400'>
            Install or select a compatible model in Founder IDE on your laptop. Downloaded files,
            model verification and role selection are separate from pairing the laptop with this website.
            A browser selection alone does not install or configure a local model.
          </p>

          <ol className='mt-6 list-decimal space-y-2 pl-5 text-sm text-zinc-300'>
            <li>
              Open <strong className='text-white'>Local AI</strong> in the Founder IDE desktop app.
            </li>
            <li>
              Reuse an installed model, choose a compatible download, or use <strong className='text-white'>Import a local GGUF</strong>.
              Check the hardware and compatibility guidance before downloading.
            </li>
            <li>
              Select your coding, reviewer and optional vision roles, then complete the desktop verification and configuration steps.
              Vision needs a compatible verified model and projector pair, not just a text model.
            </li>
            <li>
              Choose Laptop AI or the configured local connection in the desktop composer. Installed, verified and configured models wake on demand;
              a remote build request still needs local approval.
            </li>
          </ol>

          <div className='mt-6 rounded-xl border border-zinc-800 bg-zinc-950/40 p-4'>
            <p className='text-xs text-zinc-400'>
              <strong className='text-zinc-200'>Local is not a blanket capability guarantee.</strong> Supported architectures,
              available RAM, context size and the task affect performance. Text-only models cannot interpret images.
              Do not assume a remote request changed your selected desktop route; check its actual execution receipt.
            </p>
          </div>

          <div className='mt-6 flex flex-wrap gap-3'>
            <Link
              href='/founder-ide'
              className='inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-violet-500'
            >
              ← Back to Founder IDE
            </Link>
            <Link
              href='/founder-ide/byok'
              className='inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-200 transition hover:border-zinc-500 hover:bg-white/5'
            >
              Prefer cloud keys? Set up BYOK →
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
