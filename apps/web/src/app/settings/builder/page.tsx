'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { BuilderSettingsPanelWithAuth } from '@/components/settings/builder-settings-panel';

export default function BuilderSettingsPage() {
  return (
    <main className='min-h-screen bg-[#050508]'>
      <header className='border-b border-zinc-800'>
        <div className='mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5'>
          <div>
            <SiteBrand className='text-sm' />
            <h1 className='mt-1 text-2xl font-bold text-white'>Integrations</h1>
            <p className='text-sm text-zinc-400'>Founder IDE connections · Account security</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className='mx-auto max-w-4xl px-6 py-10'>
        <div className='mb-6 flex flex-wrap gap-4 text-sm'>
          <Link href='/founder-ide' className='text-violet-300 hover:underline'>
            ← Founder IDE
          </Link>
          <Link href='/downloads' className='text-zinc-400 hover:text-white'>
            Downloads →
          </Link>
        </div>

        <BuilderSettingsPanelWithAuth />
      </div>
    </main>
  );
}
