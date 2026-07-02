'use client';

/**
 * Collapsible explainer shown on signup / login pages so users understand the
 * two-tier free-token eligibility before they choose X vs email signup.
 *
 * Tier names are intentionally friendly:
 *   - "Verified Builder" = the fast track (verified X + builder activity)
 *   - "Trial"            = email-only, capped, intended to be upgraded from
 *
 * Numbers (500k / 25k / 30%) match the server-side env defaults set in
 * docs/ENV-VARS.md (BUILDER_DAILY_TOKEN_CAP, PARASITE_DAILY_TOKEN_CAP,
 * PROMO_POOL_PRESERVATION_PCT). If the server defaults change, update here too.
 */
export function FounderTokenRulesCollapsible() {
  return (
    <details className="mt-3 rounded-lg border border-zinc-700/60 bg-zinc-900/30 p-4 text-sm open:border-violet-500/40 open:bg-violet-950/10">
      <summary className="flex cursor-pointer list-none items-center justify-between text-zinc-300 hover:text-white">
        <span className="font-medium">How free AI tokens work on Founder OS</span>
        <span className="text-xs text-zinc-500">30-day promo · 10M-token pool</span>
      </summary>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 p-4">
          <p className="flex items-center gap-2 font-semibold text-emerald-300">
            <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
            Verified Builder
          </p>
          <p className="mt-1 text-xs text-emerald-200/70">Verify your X handle</p>
          <ul className="mt-3 space-y-1.5 text-xs text-zinc-300">
            <li>✓ Full 30-day window, no friction</li>
            <li>
              ✓ <strong className="text-white">500k tokens/day</strong> — enough for serious prototyping
            </li>
            <li>✓ Pool reserved for you when it runs low</li>
            <li>✓ Free DDollar signup bonus + GLM-5.2 free 90 days</li>
            <li>✓ Connect GitHub + Cursor + push a commit → priority locked in</li>
          </ul>
        </div>

        <div className="rounded-lg border border-zinc-700/60 bg-zinc-900/40 p-4">
          <p className="flex items-center gap-2 font-semibold text-zinc-300">
            <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-zinc-500" />
            Trial
          </p>
          <p className="mt-1 text-xs text-zinc-500">Email-only signup</p>
          <ul className="mt-3 space-y-1.5 text-xs text-zinc-400">
            <li>• 30-day window still applies</li>
            <li>
              • <strong className="text-zinc-200">25k tokens/day</strong> — enough to try the product
            </li>
            <li>• Paused when pool drops below 30% (reserved for builders)</li>
            <li>• No free DDollar bonus or GLM-5.2 grant</li>
            <li>• Bring your own API key anytime to keep going</li>
          </ul>
        </div>
      </div>

      <p className="mt-4 border-t border-zinc-700/60 pt-3 text-center text-xs text-zinc-400">
        Real builders get a fast track. The upgrade is always one X-verification + one commit away — use{' '}
        <strong className="text-zinc-200">Sign up with X</strong> above to skip the trial.
      </p>
    </details>
  );
}
