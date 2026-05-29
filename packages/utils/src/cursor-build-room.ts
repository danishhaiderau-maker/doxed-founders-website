import { translateCommitForTraders } from './github-translate';

/** Rule-based build room → suggested update (no LLM bill). */
export function buildSuggestionFromBuildPrompt(
  prompt: string,
  dayNumber?: number,
): {
  headline: string;
  body: string;
  devSummary: string;
  traderSummary: string;
} {
  const lines = prompt
    .trim()
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);
  const title = lines[0] ?? 'Build session';
  const rest = lines.slice(1);
  const bullets = rest.length > 0 ? rest : [prompt.trim()];
  const day = dayNumber ? `Day ${dayNumber}` : 'Build session';

  const devSummary = bullets.join('; ');
  const traderBullets = bullets.map(
    (b) => `✓ ${translateCommitForTraders(b).replace(/^[^:]+:\s*/, '')}`,
  );

  return {
    headline: `${day} — ${title.slice(0, 80)}`,
    body: [day, '', 'Shipped:', ...bullets.map((b) => `• ${b}`), '', 'Next: keep building in public.'].join('\n'),
    devSummary,
    traderSummary: traderBullets.join('\n'),
  };
}

export function buildDeploySuggestion(input: {
  provider: string;
  projectName?: string;
  environment?: string;
}): {
  headline: string;
  body: string;
  devSummary: string;
  traderSummary: string;
} {
  const env = input.environment ?? 'production';
  const name = input.projectName ?? 'the app';
  return {
    headline: `Deployed to ${env} via ${input.provider}`,
    body: [
      `Fresh deploy to ${env}.`,
      '',
      `• Platform: ${input.provider}`,
      `• Project: ${name}`,
      '',
      'Auto-detected from your connected stack — review and publish everywhere.',
    ].join('\n'),
    devSummary: `${input.provider} deploy to ${env} for ${name}`,
    traderSummary: `✓ New version live on ${env} — product moving forward.\n✓ Infrastructure connected to Founder OS for transparency.`,
  };
}
