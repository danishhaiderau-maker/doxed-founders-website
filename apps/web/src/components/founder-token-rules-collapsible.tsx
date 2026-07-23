'use client';

/** Explains the two honest AI paths without exposing internal routing details. */
export function FounderTokenRulesCollapsible() {
  return (
    <details className="mt-3 rounded-lg border border-zinc-700/60 bg-zinc-900/30 p-4 text-sm open:border-violet-500/40 open:bg-violet-950/10">
      <summary className="flex cursor-pointer list-none items-center justify-between text-zinc-300 hover:text-white">
        <span className="font-medium">How Founder Free works</span>
        <span className="text-xs text-zinc-500">Managed quota | personal AI anytime</span>
      </summary>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 p-4">
          <p className="flex items-center gap-2 font-semibold text-emerald-300">
            <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
            Founder Free
          </p>
          <p className="mt-1 text-xs text-emerald-200/70">Activate with your verified X account</p>
          <ul className="mt-3 space-y-1.5 text-xs text-zinc-300">
            <li>Managed quota for questions, planning, and small edits</li>
            <li>200,000 weighted units in a recurring seven-day window</li>
            <li>DeepSeek V4 Flash for managed everyday work</li>
            <li>Clear used, reserved, remaining, and renewal values</li>
            <li>Your own provider keys remain available at any time</li>
          </ul>
        </div>

        <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/40 p-4">
          <p className="flex items-center gap-2 font-semibold text-zinc-300">
            <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-zinc-500" />
            Personal or local AI
          </p>
          <p className="mt-1 text-xs text-zinc-500">No Founder allowance required</p>
          <ul className="mt-3 space-y-1.5 text-xs text-zinc-400">
            <li>Connect OpenAI, Anthropic, Google, GLM, DeepSeek, or OpenRouter</li>
            <li>Add a custom OpenAI-compatible endpoint</li>
            <li>Run Ollama privately on this device</li>
            <li>Personal provider usage is billed by that provider</li>
            <li>Personal and local usage does not consume Founder Free quota</li>
          </ul>
        </div>
      </div>

      <p className="mt-4 border-t border-zinc-700/60 pt-3 text-center text-xs text-zinc-400">
        Sign in to activate Founder Free, or use your own provider or local model without waiting.
      </p>
    </details>
  );
}
