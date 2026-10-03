'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';

// Pairing carries approved task requests, not provider credentials. Keep setup
// in the desktop Connections surface until an explicit key-transfer flow exists.
export default function FounderIdeByokPage() {
  return (
    <main className='min-h-screen bg-[#050508] text-zinc-100'>
      <header className='border-b border-zinc-800'>
        <div className='mx-auto flex max-w-5xl flex-col gap-4 px-6 py-5'>
          <div>
            <SiteBrand className='text-sm' />
            <h1 className='mt-1 text-2xl font-semibold tracking-tight text-white'>Bring your own keys</h1>
            <p className='text-sm text-zinc-400'>
              Configure and verify your provider connection in the desktop app.
            </p>
          </div>
          <div className='w-full min-w-0'><SiteNav /></div>
        </div>
      </header>

      <div className='mx-auto max-w-3xl px-6 py-12'>
        <div className='rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8'>
          <h2 className='text-lg font-semibold text-white'>Set up BYOK in Founder IDE</h2>
          <p className='mt-2 text-sm leading-relaxed text-zinc-400'>
            This website does not save or transfer API keys to your laptop. Pairing a device does not
            configure a provider. Do not paste API keys into remote chat or task prompts.
          </p>

          <ol className='mt-6 list-decimal space-y-3 pl-5 text-sm leading-relaxed text-zinc-300'>
            <li>Open the Founder IDE desktop app on your laptop, then choose <strong>Connections → My AI → Add My AI</strong>.</li>
            <li>Enter the provider, model, endpoint and API key there, then choose <strong>Connect My AI</strong>. Check the connection verification result.</li>
            <li>Select the verified connection for the intended Brain or Builder role. Provider support and model capabilities depend on that role.</li>
            <li>Return here to pair the laptop and select its project. A remote request still requires local approval before Builder starts.</li>
          </ol>

          <p className='mt-6 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-200'>
            Remote key setup is not available on this website. A successful desktop connection test—not a browser save message—is the evidence that your provider is ready.
          </p>

          <div className='mt-6 flex flex-wrap gap-3'>
            <Link
              href='/founder-ide'
              className='inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-violet-500'
            >
              ← Back to Founder IDE
            </Link>
            <Link
              href='/founder-ide/local'
              className='inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-200 transition hover:border-zinc-500 hover:bg-white/5'
            >
              Prefer local? See desktop model setup →
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
