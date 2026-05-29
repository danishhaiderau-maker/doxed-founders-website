/** Heuristic commit → trader-friendly summary (no LLM required). */
export function translateCommitForTraders(message: string): string {
  const m = message.trim().split('\n')[0] ?? message;
  const lower = m.toLowerCase();

  if (/fix|bug|patch|hotfix/.test(lower)) {
    return `Stability improvement shipped: ${m}. Reliability for users should improve.`;
  }
  if (/perf|optim|latency|speed|cache/.test(lower)) {
    return `Performance upgrade: ${m}. The platform should feel faster for users.`;
  }
  if (/auth|login|wallet|security/.test(lower)) {
    return `Security & access update: ${m}. Safer onboarding and account flows.`;
  }
  if (/ui|ux|design|layout|mobile/.test(lower)) {
    return `Product experience update: ${m}. Better usability for the community.`;
  }
  if (/api|endpoint|websocket|queue|infra/.test(lower)) {
    return `Platform capacity improved: ${m}. Better foundation to scale users.`;
  }
  if (/test|ci|deploy/.test(lower)) {
    return `Engineering quality: ${m}. Stronger delivery pipeline behind the scenes.`;
  }
  if (/feat|add|implement|introduce/.test(lower)) {
    return `New capability shipped: ${m}. Another step toward product-market fit.`;
  }
  return `Build progress: ${m}. The team is shipping in public.`;
}

export function buildSuggestedUpdateFromCommits(
  commits: { sha: string; message: string; date: string }[],
  dayNumber?: number,
): {
  headline: string;
  body: string;
  devSummary: string;
  traderSummary: string;
} {
  const day = dayNumber ? `Day ${dayNumber}` : 'Build update';
  const bullets = commits.slice(0, 5).map((c) => `• ${c.message.split('\n')[0]}`);
  const traderBullets = commits
    .slice(0, 5)
    .map((c) => `✓ ${translateCommitForTraders(c.message).replace(/^[^:]+:\s*/, '')}`);

  return {
    headline: `${day} — ${commits.length} commit${commits.length === 1 ? '' : 's'} pushed`,
    body: [`${day}`, '', 'Completed:', ...bullets, '', 'Next: keep building in public.'].join('\n'),
    devSummary: commits.map((c) => c.message.split('\n')[0]).join('; '),
    traderSummary: traderBullets.join('\n'),
  };
}
