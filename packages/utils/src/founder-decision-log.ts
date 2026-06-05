/** P3 lite — founder decision journal for Brain context assembly. */

export type FounderDecisionEntry = {
  id: string;
  decision: string;
  reason: string;
  date: string;
  source?: string;
};

export const FOUNDER_DECISION_LOG_KEY = '_decisionLog';
const MAX_ENTRIES = 50;

export function parseFounderDecisionLog(raw: unknown): FounderDecisionEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: FounderDecisionEntry[] = [];
  for (const row of raw) {
    if (!row || typeof row !== 'object') continue;
    const o = row as Record<string, unknown>;
    const decision = typeof o.decision === 'string' ? o.decision.trim() : '';
    const reason = typeof o.reason === 'string' ? o.reason.trim() : '';
    if (!decision) continue;
    const entry: FounderDecisionEntry = {
      id:
        typeof o.id === 'string'
          ? o.id
          : `dec-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      decision: decision.slice(0, 500),
      reason: reason.slice(0, 1000),
      date: typeof o.date === 'string' ? o.date : new Date().toISOString(),
    };
    if (typeof o.source === 'string' && o.source.trim()) {
      entry.source = o.source.trim().slice(0, 80);
    }
    out.push(entry);
  }
  return out.slice(0, MAX_ENTRIES);
}

export function appendFounderDecision(
  existing: unknown,
  input: { decision: string; reason?: string; source?: string },
): FounderDecisionEntry[] {
  const log = parseFounderDecisionLog(existing);
  const entry: FounderDecisionEntry = {
    id: `dec-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    decision: input.decision.trim().slice(0, 500),
    reason: (input.reason ?? '').trim().slice(0, 1000),
    date: new Date().toISOString(),
    source: input.source?.trim().slice(0, 80),
  };
  if (!entry.decision) return log;
  return [entry, ...log].slice(0, MAX_ENTRIES);
}

export function formatDecisionLogExcerpt(
  entries: FounderDecisionEntry[],
  max = 8,
): string | null {
  if (entries.length === 0) return null;
  const lines = entries.slice(0, max).map((e) => {
    const when = e.date.slice(0, 10);
    const why = e.reason ? ` — ${e.reason.slice(0, 160)}` : '';
    const src = e.source ? ` [${e.source}]` : '';
    return `- ${when}: **${e.decision}**${why}${src}`;
  });
  return ['## Decision log (recent)', ...lines].join('\n');
}

/** Detect explicit founder decisions stated in chat for auto-logging. */
export function detectDecisionFromPrompt(prompt: string): {
  decision: string;
  reason: string;
} | null {
  const t = prompt.trim();
  if (t.length < 12) return null;

  const patterns: { re: RegExp; decisionGroup: number; reasonGroup?: number }[] = [
    {
      re: /(?:we(?:'ve)?|i(?:'ve)?)\s+decided\s+(?:to\s+)?(.+?)(?:\s+because\s+(.+))?$/i,
      decisionGroup: 1,
      reasonGroup: 2,
    },
    {
      re: /(?:let'?s|we(?:'re| are))\s+(?:going with|choosing|picking)\s+(.+?)(?:\s+because\s+(.+))?$/i,
      decisionGroup: 1,
      reasonGroup: 2,
    },
    {
      re: /decision:\s*(.+?)(?:\s+[-—]\s+(.+))?$/i,
      decisionGroup: 1,
      reasonGroup: 2,
    },
    {
      re: /(?:we(?:'ll)?|i(?:'ll)?)\s+(?:won'?t|will not)\s+(.+?)(?:\s+because\s+(.+))?$/i,
      decisionGroup: 1,
      reasonGroup: 2,
    },
  ];

  for (const p of patterns) {
    const m = t.match(p.re);
    if (!m?.[p.decisionGroup]) continue;
    const decision = m[p.decisionGroup]!.trim().replace(/[.!?]+$/, '');
    const reason = p.reasonGroup && m[p.reasonGroup] ? m[p.reasonGroup]!.trim() : '';
    if (decision.length >= 8) return { decision, reason };
  }

  return null;
}
