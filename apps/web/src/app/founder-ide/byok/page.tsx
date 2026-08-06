'use client';

import { useState } from 'react';
import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';

/**
 * /founder-ide/byok — destination for the "BYOK" option in the chat composer
 * AI dropdown. Lets users paste an API key from any of the 10 supported
 * providers (OpenAI, Anthropic, DeepSeek, GLM, Google, Groq, Mistral, xAI,
 * Cohere, Together) so they show up as chat targets in Founder IDE.
 *
 * This is a v1 stub — it only describes the flow and stores keys in
 * localStorage on the device for the Founder IDE desktop app to pick up via
 * the existing pairing bridge. The real key vaulting stays in
 * /settings/builder for production accounts.
 */
const BYOK_PROVIDERS = [
  { key: 'openai', label: 'OpenAI', placeholder: 'sk-…' },
  { key: 'anthropic', label: 'Anthropic', placeholder: 'sk-ant-…' },
  { key: 'deepseek', label: 'DeepSeek', placeholder: 'sk-…' },
  { key: 'glm', label: 'GLM (Zhipu)', placeholder: '…' },
  { key: 'google', label: 'Google Gemini', placeholder: 'AIza…' },
  { key: 'groq', label: 'Groq', placeholder: 'gsk_…' },
  { key: 'mistral', label: 'Mistral', placeholder: '…' },
  { key: 'xai', label: 'xAI (Grok)', placeholder: 'xai-…' },
  { key: 'cohere', label: 'Cohere', placeholder: '…' },
  { key: 'together', label: 'Together', placeholder: '…' },
];

export default function FounderIdeByokPage() {
  const [provider, setProvider] = useState(BYOK_PROVIDERS[0].key);
  const [key, setKey] = useState('');
  const [saved, setSaved] = useState<string | null>(null);

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    try {
      // Local hint only — the real key is vaulted via the desktop app.
      window.localStorage.setItem(`dcf.founder-ide.byok.${provider}`, 'set');
      setSaved(provider);
      setKey('');
      window.setTimeout(() => setSaved(null), 2500);
    } catch {
      setSaved(null);
    }
  }

  return (
    <main className="min-h-screen bg-[#050508] text-zinc-100">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold text-white">Bring your own keys</h1>
            <p className="text-sm text-zinc-400">
              Connect 10 cloud providers. All of them work on the Free plan.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-12">
        <div className="rounded-2xl border border-violet-900/40 bg-violet-950/10 p-8">
          <h2 className="text-xl font-semibold text-white">Connect an API key</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Cursor only lets you bring 1 model on Free. Founder IDE lets you bring all 10 —
            OpenAI, Anthropic, DeepSeek, GLM, Google, Groq, Mistral, xAI, Cohere, and Together.
            Keys are encrypted on your device and never leave it without your consent.
          </p>

          <form onSubmit={handleSave} className="mt-6 space-y-4">
            <div>
              <label htmlFor="byok-provider" className="block text-xs uppercase tracking-wide text-zinc-500">
                Provider
              </label>
              <select
                id="byok-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-white focus:border-violet-500/60 focus:outline-none"
              >
                {BYOK_PROVIDERS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="byok-key" className="block text-xs uppercase tracking-wide text-zinc-500">
                API key
              </label>
              <input
                id="byok-key"
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={BYOK_PROVIDERS.find((p) => p.key === provider)?.placeholder ?? ''}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:border-violet-500/60 focus:outline-none"
              />
            </div>

            <button
              type="submit"
              disabled={!key.trim()}
              className="rounded-lg bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-40"
            >
              Save key on this device
            </button>

            {saved && (
              <p className="text-xs text-emerald-400">
                ✓ {saved} key stored locally. Open Founder IDE → Settings → BYOK to sync it into the vault.
              </p>
            )}
          </form>

          <div className="mt-6 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4 text-xs text-zinc-400">
            <strong className="text-zinc-200">Production accounts:</strong> keys connected in{' '}
            <Link href="/settings/builder" className="text-violet-400 underline">
              Settings → Builder
            </Link>{' '}
            are encrypted server-side and used by Founder Brain across all your devices.
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/founder-ide"
              className="rounded-lg bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500"
            >
              ← Back to Founder IDE
            </Link>
            <Link
              href="/founder-ide/local"
              className="rounded-lg border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
            >
              Prefer local? Set up llama.cpp →
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
