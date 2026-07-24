export type FounderAutoEscalationReason = 'high_complexity' | 'failed_verification';

export interface FounderAutoEscalationMessage {
  role: string;
  content: string;
}

export function founderAutoEscalationReason(
  messages: readonly FounderAutoEscalationMessage[],
): FounderAutoEscalationReason | null {
  const recentToolResults = messages
    .filter((message) => message.role === 'tool')
    .slice(-4)
    .map((message) => message.content);
  if (recentToolResults.some(isFailedVerificationResult)) return 'failed_verification';

  const request = [...messages]
    .reverse()
    .find((message) => message.role === 'user')
    ?.content
    .replace(/\s+/g, ' ')
    .trim() ?? '';
  if (!request) return null;

  let signals = 0;
  if (request.length >= 800) signals += 1;
  if (/\b(?:architecture|authentication|authorization|concurrency|migration|payments?|permissions?|privacy|release|rollback|security|signing|transaction)\b/i.test(request)) signals += 1;
  if (/\b(?:compare|coordinate|cross-check|end-to-end|trade-?offs?|verify)\b/i.test(request)) signals += 1;
  const mentionedFiles = new Set(
    request.match(/(?:[A-Za-z0-9_.-]+[\\/]){1,8}[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+/g) ?? [],
  );
  if (mentionedFiles.size >= 3) signals += 1;
  return signals >= 2 ? 'high_complexity' : null;
}

export function isFailedVerificationResult(value: string): boolean {
  const normalized = value.toLowerCase();
  const exit = normalized.match(/\[exit code\s+(-?\d+)\]/);
  if (exit && Number(exit[1]) !== 0) return true;
  return /\b(?:tests?|typecheck|build|lint|verification)\b[^\n]{0,80}\b(?:failed|failure|error)\b/i.test(value)
    || /^error:/im.test(value);
}
