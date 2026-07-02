import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Terms of Service — Founder OS',
  description:
    'Terms of Service for Founder OS — the AI development workspace, IDE bridge, and founder validation platform operated by Doxxed Crypto.',
};

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <div className="mx-auto max-w-3xl px-6 py-12">
        <Link href="/" className="text-sm text-zinc-500 hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-4 text-3xl font-bold tracking-tight">
          Terms of <span className="text-violet-400">Service</span>
        </h1>
        <p className="mt-2 text-sm text-zinc-500">Last updated: July 2, 2026</p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-zinc-300">
          <section>
            <p className="text-zinc-400">
              These Terms of Service (“Terms”) govern your access to and use of Founder OS, the AI
              development workspace, IDE bridge, founder validation network, and related services
              (the “Service”) operated by Doxxed Crypto (“we”, “us”, or “our”). By creating an
              account or using any part of the Service, you agree to these Terms. If you do not
              agree, do not use the Service.
            </p>
          </section>

          <Section title="1. Description of the Service">
            <p>
              Founder OS is a development workspace that combines an AI co-pilot, an IDE bridge for
              remote development (Cursor, Claude Code, Codex, Windsurf, and similar tools), a
              founder reputation network, paper-trading simulations, and community tooling. The
              Service includes:
            </p>
            <ul className="mt-2 list-disc space-y-1.5 pl-5">
              <li>An AI workspace and chat interface that routes prompts to AI providers.</li>
              <li>
                An IDE bridge that relays prompts, file context, and session metadata between your
                local editor and the platform.
              </li>
              <li>
                A founder validation layer — public build logs, simulated demand, project listings,
                and reputation scoring.
              </li>
              <li>
                Paper-trading and scout markets that are <strong>simulated only</strong> and never
                settle in real funds.
              </li>
            </ul>
            <p className="mt-2">
              The Service is <strong>not</strong> a real-money exchange, a custodian, a broker, or
              financial advice. DDollar and any platform credits are simulated ecosystem currency
              with no cash value and no right of withdrawal.
            </p>
          </Section>

          <Section title="2. Eligibility & Accounts">
            <p>
              You must be at least 13 years old (or the age of digital consent in your jurisdiction)
              to create an account. You are responsible for maintaining the confidentiality of your
              account credentials, two-factor credentials, recovery codes, and any API keys you
              connect. You agree to notify us promptly of any unauthorized access to your account.
            </p>
            <p className="mt-2">
              We may suspend or terminate accounts that violate these Terms, that we believe are
              controlled by automated bots, or that are linked to fraudulent activity.
            </p>
          </Section>

          <Section title="3. Acceptable Use">
            <p>You agree not to:</p>
            <ul className="mt-2 list-disc space-y-1.5 pl-5">
              <li>Use the Service to upload malware, harass others, or coordinate abuse.</li>
              <li>
                Attempt to access another user’s data, tokens, or sessions without authorization.
              </li>
              <li>
                Reverse-engineer, scrape, or overload the Service, its APIs, or the IDE bridge
                endpoints.
              </li>
              <li>
                Submit content (project listings, build posts, chat prompts) that is illegal,
                infringes third-party IP, or deceives investors about a project’s team or traction.
              </li>
              <li>
                Use the platform to launder funds, evade sanctions, or facilitate trades of
                regulated securities.
              </li>
              <li>
                Resell or repackage the Service as your own product without a written partnership.
              </li>
            </ul>
            <p className="mt-2">
              We may remove content and revoke access for violations. Repeated or severe violations
              may result in permanent termination.
            </p>
          </Section>

          <Section title="4. AI API Usage">
            <p>
              Founder OS routes prompts to AI providers — including DeepSeek, OpenAI, Anthropic,
              Gemini, OpenRouter, Ollama (local), and others. Two billing paths exist:
            </p>
            <ul className="mt-2 list-disc space-y-1.5 pl-5">
              <li>
                <strong>Platform-provided AI (“platform brain” and promo credits):</strong> we pay
                the provider and may impose token caps, rate limits, or model restrictions. Promo
                credits expire and are not refundable.
              </li>
              <li>
                <strong>Bring-your-own-key (BYOK):</strong> you paste your own provider API key. You
                are solely responsible for the cost, usage limits, and compliance with that
                provider’s terms. Your key is encrypted at rest and never shared with other users.
              </li>
            </ul>
            <p className="mt-2">
              AI output may be inaccurate. You are responsible for reviewing generated code, copy,
              and analysis before publishing or deploying it. We are not liable for damages caused
              by reliance on AI-generated content.
            </p>
          </Section>

          <Section title="5. IDE Bridge & Remote Development">
            <p>
              The IDE bridge connects your local editor (Cursor, Claude Code, Codex, Windsurf, etc.)
              to the platform so prompts, file context, and session metadata can be relayed. When
              you enable the bridge:
            </p>
            <ul className="mt-2 list-disc space-y-1.5 pl-5">
              <li>
                You consent to the bridge reading the file paths, prompts, and metadata that your
                editor exposes to it — not arbitrary file contents on your machine.
              </li>
              <li>
                You are responsible for what you choose to send. Do not paste secrets, private keys,
                or customer data into AI prompts.
              </li>
              <li>
                We may retain session metadata to power the founder event graph and your build
                history; you can delete sessions from your Founder Den at any time.
              </li>
            </ul>
          </Section>

          <Section title="6. User Responsibilities">
            <p>
              You are responsible for the accuracy of project information you submit, for any API
              keys and OAuth tokens you connect, and for keeping your founder claims truthful. You
              grant us a worldwide, non-exclusive, royalty-free license to host, display, and
              process content you submit for the purpose of operating the Service.
            </p>
            <p className="mt-2">
              You retain ownership of your content. Project listings, build posts, and public claims
              remain yours — we may display them on the platform and in aggregated analytics.
            </p>
          </Section>

          <Section title="7. Third-Party Services">
            <p>
              The Service integrates with GitHub, AI providers, hosting platforms (Vercel, Railway,
              Neon), and IDE tools. Each third party has its own terms and privacy policy. We are
              not responsible for their behavior, outages, or data practices. If a third party
              revokes your access, your linked features may stop working without refund.
            </p>
          </Section>

          <Section title="8. Disclaimers">
            <p>
              The Service is provided “as is” and “as available” without warranties of any kind,
              whether express or implied. We do not warrant that the Service will be uninterrupted,
              error-free, secure, or that AI output will be accurate or fit for any purpose. Paper
              trading performance is not indicative of real-market results.
            </p>
          </Section>

          <Section title="9. Limitation of Liability">
            <p>
              To the maximum extent permitted by law, neither Doxxed Crypto nor its operators shall
              be liable for any indirect, incidental, special, consequential, or exemplary damages
              — including lost profits, lost data, trading losses, or business interruption —
              arising out of or related to the Service, even if we have been advised of the
              possibility of such damages. Our total aggregate liability for any claim shall not
              exceed the amount you paid us in the 12 months preceding the claim, or USD 50,
              whichever is greater.
            </p>
          </Section>

          <Section title="10. Account Termination">
            <p>
              You may delete your account at any time from Account → Security. We may suspend or
              terminate your access if you violate these Terms, if we are legally required to do so,
              or if we discontinue a feature. Upon termination, your right to use the Service ends.
              Data you contributed to public, aggregated, or analytics datasets may be retained in
              anonymized form.
            </p>
          </Section>

          <Section title="11. Changes to These Terms">
            <p>
              We may update these Terms from time to time. For material changes, we will post a
              notice in Town Hall or notify active users. Continued use after the effective date
              constitutes acceptance of the revised Terms. The “Last updated” date above reflects
              the most recent revision.
            </p>
          </Section>

          <Section title="12. Contact">
            <p>
              Questions about these Terms can be raised through{' '}
              <Link href="/town-hall" className="text-violet-300 hover:underline">
                Town Hall
              </Link>{' '}
              or the support channel listed on your account settings page. For legal requests, use
              the same channel with the subject “Legal — Terms”.
            </p>
          </Section>

          <div className="border-t border-zinc-800 pt-6 text-xs text-zinc-500">
            <Link href="/legal/privacy" className="text-violet-300 hover:underline">
              View the Privacy Policy →
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
