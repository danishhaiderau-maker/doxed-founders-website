import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Privacy Policy — Doxxed Crypto',
  description: 'How Doxxed Crypto handles account data for Google and X login.',
};

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <div className="mx-auto max-w-2xl px-6 py-12">
        <Link href="/" className="text-sm text-zinc-500 hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-4 text-3xl font-bold">Privacy Policy</h1>
        <p className="mt-2 text-sm text-zinc-500">Last updated: June 1, 2026</p>

        <div className="mt-8 space-y-6 text-sm leading-relaxed text-zinc-300">
          <section>
            <h2 className="text-lg font-semibold text-white">Summary</h2>
            <p className="mt-2">
              Doxxed Crypto is a paper-trading and founder validation platform. We do not sell user data. We do not
              operate a real-money exchange. DDollar is simulated ecosystem currency with no cash value.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">What we collect</h2>
            <ul className="mt-2 list-disc space-y-2 pl-5">
              <li>
                <strong>Account sign-in (Google or X/Twitter):</strong> We receive your email, display name, and profile
                identifier from the OAuth provider so you can log in. We do not receive your Google or X password.
              </li>
              <li>
                <strong>Platform activity:</strong> Paper trades, scout votes, founder submissions, and community
                participation stored to operate rankings, Trust Center, and DDollar rewards.
              </li>
              <li>
                <strong>Optional connections:</strong> If you connect GitHub in Founder OS, we store an encrypted token
                to sync build activity you authorize.
              </li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">What we do not do</h2>
            <ul className="mt-2 list-disc space-y-2 pl-5">
              <li>We do not sell personal data to third parties.</li>
              <li>We do not collect government ID or KYC documents for basic platform use.</li>
              <li>We do not use your data for off-platform advertising profiles.</li>
              <li>DDollar cannot be withdrawn and is not redeemable for cash.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">Third-party services</h2>
            <p className="mt-2">
              Sign-in is handled by Google and X (Twitter) OAuth. Their privacy policies apply to authentication.
              Market data may come from public APIs (e.g. DexScreener). Hosting is provided by cloud infrastructure
              providers (Vercel, Railway, Neon).
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">Your choices</h2>
            <p className="mt-2">
              You may disconnect OAuth accounts in Account → Connected Accounts. To request account deletion, contact
              the team via the email on your Town Hall announcements or support channel.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">Contact</h2>
            <p className="mt-2">
              Questions about this policy: use the official channel listed on{' '}
              <Link href="/town-hall" className="text-violet-300 hover:underline">
                Town Hall
              </Link>
              .
            </p>
          </section>
        </div>
      </div>
    </main>
  );
}
