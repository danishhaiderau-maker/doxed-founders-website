'use client';

import {
  EXCHANGE_API_GUIDES,
  type ExchangeProvider,
  exchangeGuideLabel,
} from '@dcf/utils';

export function ExchangeApiGuideDrawer({
  provider,
  open,
  onClose,
}: {
  provider: ExchangeProvider;
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  const guide = EXCHANGE_API_GUIDES[provider];

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-4 sm:items-center" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-zinc-700 bg-zinc-950 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-bold text-white">
              How to create {exchangeGuideLabel(provider)} API keys
            </h3>
            {guide.recommended && guide.recommendReason && (
              <p className="mt-2 rounded-lg border border-emerald-500/30 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-100">
                {guide.recommendReason}
              </p>
            )}
          </div>
          <button type="button" onClick={onClose} className="text-zinc-500 hover:text-white">
            ✕
          </button>
        </div>

        <ol className="mt-5 list-decimal space-y-2 pl-5 text-sm text-zinc-300">
          {guide.steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>

        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-400">Required</p>
            <ul className="mt-2 space-y-1 text-xs text-zinc-400">
              {guide.requiredPermissions.map((p) => (
                <li key={p}>✓ {p}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-red-400">Not required</p>
            <ul className="mt-2 space-y-1 text-xs text-zinc-500">
              {guide.forbiddenPermissions.map((p) => (
                <li key={p}>✗ {p}</li>
              ))}
            </ul>
          </div>
        </div>

        <p className="mt-4 rounded-lg border border-zinc-800 bg-black/30 px-3 py-2 text-xs text-zinc-400">
          Founder OS encrypts API credentials. You remain the owner of your funds. Never enable withdraw permissions.
        </p>

        <a
          href={guide.docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-block text-sm text-violet-400 hover:text-violet-300"
        >
          Official {exchangeGuideLabel(provider)} documentation →
        </a>
      </div>
    </div>
  );
}
