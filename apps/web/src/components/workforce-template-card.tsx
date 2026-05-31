'use client';

import Link from 'next/link';
import { AGENT_CATEGORY_LABELS, buildCopilotAgentDeepLink, WORKFORCE_PERMISSIONS } from '@dcf/utils';

type Template = {
  key: string;
  label: string;
  category: string;
  description: string;
};

export function WorkforceTemplateCard({
  template,
  href,
  compact = false,
}: {
  template: Template;
  href: string;
  compact?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`group block rounded-xl border border-zinc-800 bg-zinc-900/40 transition hover:border-violet-500/40 hover:bg-violet-950/20 ${
        compact ? 'p-3' : 'p-4'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className={`font-medium text-white ${compact ? 'text-sm' : ''}`}>{template.label}</p>
        <span className="shrink-0 rounded-full bg-zinc-800 px-2 py-0.5 text-[9px] uppercase text-zinc-400">
          {AGENT_CATEGORY_LABELS[template.category] ?? template.category}
        </span>
      </div>
      <p className={`mt-1 text-zinc-500 ${compact ? 'text-[11px] line-clamp-2' : 'text-xs'}`}>
        {template.description}
      </p>
      <p className="mt-2 text-[10px] text-zinc-600">
        Tools:{' '}
        {(WORKFORCE_PERMISSIONS[template.key] ?? WORKFORCE_PERMISSIONS.BUILDER)
          .map((t) => t.replace(/_/g, ' '))
          .join(' · ')}
      </p>
      <p className="mt-3 text-xs font-medium text-violet-300 group-hover:text-violet-200">
        Ask Copilot →
      </p>
    </Link>
  );
}

export function workforceTemplateHref(templateKey: string, signedIn: boolean, projectName?: string) {
  const target = buildCopilotAgentDeepLink(templateKey, projectName);
  if (signedIn) return target;
  return `/login?callbackUrl=${encodeURIComponent(target)}`;
}
