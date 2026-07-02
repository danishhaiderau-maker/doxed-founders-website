import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Privacy Policy — Founder OS',
  description:
    'How Founder OS collects, uses, and protects your data — including GitHub tokens, IDE bridge data, chat history, and usage analytics.',
};

export default function LegalPrivacyPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <div className="mx-auto max-w-3xl px-6 py-12">
        <Link href="/" className="text-sm text-zinc-500 hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-4 text-3xl font-bold tracking-tight">
          Privacy <span className="text-violet-400">Policy</span>
        </h1>
        <p className="mt-2 text-sm text-zinc-500">Last updated: July 2, 2026</p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-zinc-300">
          <section>
            <p className="text-zinc-400">
              This Privacy Policy explains how Doxxed Crypto (“we”, “us”) collects, uses, retains,
              and protects personal data when you use Founder OS, the AI development workspace, IDE
              bridge, and founder validation platform (the “Service”). It applies alongside our{' '}
              <Link href="/legal/terms" className="text-violet-300 hover:underline">
                Terms of Service
              </Link>
              .
            </p>
          </section>

          <Section title="1. What data we collect">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong>Account data:</strong> email, display name, OAuth provider ID (Google or
                X/Twitter), platform handle, and reputation tier. We do not collect government IDs
                or KYC documents for basic platform use.
              </li>
              <li>
                <strong>GitHub tokens (encrypted):</strong> if you connect a GitHub repository, we
                store an encrypted OAuth or personal access token to sync build activity and create
                issues on your behalf. Tokens are encrypted at rest and never exposed to other
                users.
              </li>
              <li>
                <strong>IDE bridge data:</strong> when you enable the IDE bridge, we receive prompt
                text, file paths, branch and repo names, and session metadata relayed by your editor
                (Cursor, Claude Code, Codex, Windsurf). The bridge reads only what your editor
                exposes to it — not arbitrary file contents on your machine.
              </li>
              <li>
                <strong>Chat history:</strong> prompts and AI responses you send through the Founder
                OS chat are stored so you can resume conversations and so we can bill AI usage
                accurately. You can delete individual sessions or your full chat history from the
                Founder Den.
              </li>
              <li>
                <strong>Cursor / Claude Code session metadata:</strong> build session titles, prompt
                summaries, credits spent, and outcome status are recorded for your build history and
                the founder event graph. Full prompt bodies are only stored if you explicitly route
                them through the platform bridge.
              </li>
              <li>
                <strong>Usage analytics:</strong> page views, searches, watchlist adds, paper
                trades, and feature interactions. These are aggregated and tied to your account so
                we can compute reputation, leaderboards, and DDollar rewards.
              </li>
              <li>
                <strong>Connection metadata:</strong> connected wallets (verified addresses only),
                connected apps, and integration credentials (encrypted).
              </li>
            </ul>
          </Section>

          <Section title="2. How we use your data">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>To operate the workspace — relay prompts to AI providers, render chat, sync repos.</li>
              <li>To compute your reputation tier, leaderboard rank, and DDollar balance.</li>
              <li>To power the founder event graph and build history shown on your Founder Den.</li>
              <li>To generate AI responses when you use the platform brain or BYOK routing.</li>
              <li>To detect abuse, fraud, and coordinated inauthentic behavior.</li>
              <li>To improve features — aggregated, anonymized analytics guide product decisions.</li>
              <li>To communicate with you about your account, security, and platform changes.</li>
            </ul>
            <p className="mt-2">
              We do <strong>not</strong> sell personal data to third parties. We do not use your
              data to train external AI models. Provider AI calls send only the prompt you submit —
              see “Third-party services” below.
            </p>
          </Section>

          <Section title="3. Third-party services">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong>DeepSeek / OpenAI / Anthropic / Gemini / OpenRouter:</strong> AI providers
                receive the prompts you send. Their retention policies apply to those prompts. We
                recommend reviewing each provider’s privacy policy before routing sensitive
                requests.
              </li>
              <li>
                <strong>GitHub:</strong> used for repository sync, build event detection, and
                optional issue creation. GitHub receives API requests authenticated with your
                (encrypted) token.
              </li>
              <li>
                <strong>Vercel:</strong> hosts the Next.js web app. Server logs may include IP
                addresses and request metadata.
              </li>
              <li>
                <strong>Neon:</strong> hosts the primary Postgres database. All data at rest is
                managed through Neon’s infrastructure.
              </li>
              <li>
                <strong>Railway:</strong> hosts the NestJS API and the showcase trading agent
                runtime. Application logs may contain request metadata (not prompt bodies).
              </li>
              <li>
                <strong>Google / X (Twitter) OAuth:</strong> authentication providers. We receive
                your email and profile identifier; we do not receive your password.
              </li>
            </ul>
          </Section>

          <Section title="4. Data retention & deletion">
            <p>
              We retain account data for the life of your account. Chat history and IDE bridge
              session metadata are kept until you delete them from the Founder Den. Anonymized,
              aggregated analytics (counts, leaderboards, reputation rollups) may be retained
              indefinitely even after account deletion.
            </p>
            <p className="mt-2">
              To request full account deletion, use Account → Security → Delete account, or contact
              us through the support channel listed in Town Hall. Deletion completes within 30 days
              except where retention is required by law or to resolve disputes.
            </p>
          </Section>

          <Section title="5. Your rights">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong>Access:</strong> view your account data, chat history, and connection
                metadata from Account → Activity History.
              </li>
              <li>
                <strong>Export:</strong> request a JSON export of your account data, trades, and
                build posts via the support channel.
              </li>
              <li>
                <strong>Delete:</strong> delete individual sessions, chats, or your full account at
                any time.
              </li>
              <li>
                <strong>Disconnect:</strong> remove GitHub, wallet, or OAuth connections in Account
                → Connected Accounts. Encrypted tokens are wiped on disconnect.
              </li>
              <li>
                <strong>Object / restrict:</strong> contact us to limit processing for specific
                features (e.g. disable the IDE bridge while keeping your account).
              </li>
            </ul>
            <p className="mt-2">
              Depending on your jurisdiction (GDPR, CCPA), additional rights may apply. Contact us
              to exercise them.
            </p>
          </Section>

          <Section title="6. Cookies & local storage">
            <p>
              We use minimal cookies and local storage: an authenticated session token, your
              selected AI provider / model preferences, your last-opened workspace state, and a
              referral code if you arrived via a referral link. We do not use third-party
              advertising cookies. You can clear local storage from your browser at any time;
              platform preferences will reset to defaults.
            </p>
          </Section>

          <Section title="7. Security">
            <p>
              GitHub tokens, integration credentials, and showcase API keys are encrypted at rest
              using AES with platform-managed keys. Two-factor authentication (TOTP, passkeys,
              recovery codes) is available in Account → Security. We use timing-safe compares for
              secret checks and rotate JWT signing keys periodically.
            </p>
            <p className="mt-2">
              No method of transmission or storage is fully secure. If a breach occurs, we will
              notify affected users through Town Hall and email within 72 hours of confirmation.
            </p>
          </Section>

          <Section title="8. Children’s privacy">
            <p>
              The Service is not directed to children under 13 (or the age of digital consent in
              your jurisdiction). We do not knowingly collect data from such minors. If you believe
              a minor has registered, contact us and we will delete the account.
            </p>
          </Section>

          <Section title="9. Changes to this Policy">
            <p>
              We may update this Privacy Policy from time to time. Material changes will be posted
              in Town Hall. Continued use after the effective date constitutes acceptance. The
              “Last updated” date above reflects the most recent revision.
            </p>
          </Section>

          <Section title="10. Contact">
            <p>
              Privacy questions can be raised through{' '}
              <Link href="/town-hall" className="text-violet-300 hover:underline">
                Town Hall
              </Link>{' '}
              or the support channel listed on your account settings page. For formal privacy
              requests (access, deletion, export), use the same channel with the subject “Privacy
              Request”.
            </p>
          </Section>

          <div className="border-t border-zinc-800 pt-6 text-xs text-zinc-500">
            <Link href="/legal/terms" className="text-violet-300 hover:underline">
              View the Terms of Service →
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-lg font-semibold text-white">{title}</h2>
      <div className="mt-2 space-y-2">{children}</div>
    </section>
  );
}
