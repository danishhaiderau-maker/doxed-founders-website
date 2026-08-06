import { SiteBrand, SiteNav } from '@/components/site-nav';
import { DownloadsHub } from '@/components/downloads-hub';

export const metadata = {
  title: 'Downloads — Founder Stack (IDE + Node)',
  description:
    'Install Founder Stack — one desktop kit (Founder IDE + Founder Node) for sovereign AI coding and encrypted vault sync.',
};

export default function DownloadsPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800/80 bg-[#050508]/95">
        <div className="mx-auto flex w-full max-w-3xl flex-wrap items-center justify-between gap-4 px-4 py-5 sm:px-6">
          <SiteBrand />
          <SiteNav />
        </div>
      </header>
      <DownloadsHub />
    </main>
  );
}
