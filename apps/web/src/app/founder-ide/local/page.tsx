'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';

/**
 * /founder-ide/local — destination for the "Local" option in the chat composer
 * AI dropdown. Explains how to pair a local llama.cpp / Ollama / GGUF model
 * with Founder IDE so chat + Tab work without any cloud key.
 */
export default function FounderIdeLocalPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-zinc-100">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold text-white">Local models</h1>
            <p className="text-sm text-zinc-400">
              Run Qwen, Llama, or GGUF models on your laptop. No cloud key required.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-12">
        <div className="rounded-2xl border border-emerald-900/40 bg-emerald-950/10 p-8">
          <h2 className="text-xl font-semibold text-white">Pair a local model with Founder IDE</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Founder IDE ships with a local inference adapter that talks to llama.cpp, Ollama,
            and any GGUF model on your machine. Local models and Tab autocomplete always work —
            even offline or after you exhaust your cloud quota.
          </p>

          <ol className="mt-6 list-decimal space-y-2 pl-5 text-sm text-zinc-300">
            <li>Install <a href="https://ollama.com" target="_blank" rel="noreferrer" className="text-emerald-400 underline">Ollama</a> or your preferred llama.cpp runtime.</li>
            <li>Pull a model: <code className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono text-emerald-300">ollama pull qwen2.5-coder:7b</code></li>
            <li>Open Founder IDE → Settings → Local models.</li>
            <li>Select the model and click <strong className="text-white">Activate</strong>.</li>
            <li>The chat composer&apos;s <strong className="text-white">Local</strong> option will now route to it.</li>
          </ol>

          <div className="mt-6 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
            <p className="text-xs text-zinc-400">
              <strong className="text-zinc-200">Coming soon:</strong> direct GGUF file picker,
              hardware-aware quantization suggestions, and a benchmark chart comparing models
              on your CPU/GPU.
            </p>
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/founder-ide"
              className="rounded-lg bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500"
            >
              ← Back to Founder IDE
            </Link>
            <Link
              href="/founder-ide/byok"
              className="rounded-lg border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
            >
              Prefer cloud keys? Set up BYOK →
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
