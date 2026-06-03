/** Progressive reveal for LLM answers in Mission Control (server returns full text). */
export async function revealTextInChat(
  text: string,
  onUpdate: (partial: string) => void,
  opts?: { chunkChars?: number; delayMs?: number; maxDurationMs?: number },
): Promise<void> {
  const full = text.trim();
  if (!full) {
    onUpdate('');
    return;
  }
  const chunk = opts?.chunkChars ?? 28;
  const delay = opts?.delayMs ?? 10;
  const maxDuration = opts?.maxDurationMs ?? 8000;
  const maxSteps = Math.ceil(maxDuration / delay);
  const steps = Math.min(Math.ceil(full.length / chunk), maxSteps);
  const stepSize = Math.max(chunk, Math.ceil(full.length / steps));

  for (let i = 0; i < full.length; i += stepSize) {
    onUpdate(full.slice(0, Math.min(i + stepSize, full.length)));
    await new Promise((r) => setTimeout(r, delay));
  }
  onUpdate(full);
}

export function formatThinkingInChat(providerLabel: string, routedAgent?: string): string {
  const who = routedAgent ? `${routedAgent} via ${providerLabel}` : providerLabel;
  return `**${who}** · thinking…\n\n_Answer will stream here in Mission Control._`;
}
