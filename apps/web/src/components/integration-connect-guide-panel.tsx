'use client';

import Link from 'next/link';
import type { IntegrationConnectGuide } from '@dcf/utils';

type Props = {
  providerLabel: string;
  guide: IntegrationConnectGuide;
  onClose: () => void;
  children?: React.ReactNode;
};

export function IntegrationConnectGuidePanel({
  providerLabel,
  guide,
  onClose,
  children,
}: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 p-4 sm:items-center">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-zinc-700 bg-zinc-950 p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-lg font-semibold text-white">Connect {providerLabel}</p>
            <p className="mt-1 text-sm text-zinc-400">{guide.summary}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-lg border border-zinc-700 px-2 py-1 text-sm text-zinc-400 hover:text-white"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 text-xs">
          <p>
            <span className="font-semibold text-emerald-300">What it does: </span>
            {guide.whatItDoes}
          </p>
          <p>
            <span className="font-semibold text-amber-300">What it does not: </span>
            {guide.whatItDoesNot}
          </p>
        </div>

        <ol className="mt-4 space-y-3">
          {guide.steps.map((step) => (
            <li key={step.title} className="rounded-lg border border-zinc-800 bg-black/30 p-3">
              <p className="text-sm font-medium text-white">{step.title}</p>
              <p className="mt-1 text-xs leading-relaxed text-zinc-400">{step.body}</p>
              {step.link && (
                <Link
                  href={step.link.href}
                  target={step.link.href.startsWith('http') ? '_blank' : undefined}
                  rel={step.link.href.startsWith('http') ? 'noopener noreferrer' : undefined}
                  className="mt-2 inline-block text-xs font-medium text-violet-300 hover:underline"
                >
                  {step.link.label} →
                </Link>
              )}
            </li>
          ))}
        </ol>

        {guide.note && (
          <p className="mt-4 rounded-lg border border-violet-500/30 bg-violet-950/20 p-3 text-xs text-violet-200">
            {guide.note}
          </p>
        )}

        {children && <div className="mt-5 border-t border-zinc-800 pt-4">{children}</div>}
      </div>
    </div>
  );
}
