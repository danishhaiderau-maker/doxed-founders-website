const IDE_PROVIDER_LABELS: Readonly<Record<string, string>> = {
  cursor: 'Cursor',
  vscode: 'VS Code',
  'vs-code': 'VS Code',
  windsurf: 'Windsurf',
  openhands: 'OpenHands',
  'open-hands': 'OpenHands',
  claude_code: 'Claude Code',
  'claude-code': 'Claude Code',
  founder_ide: 'Founder IDE',
  'founder-ide': 'Founder IDE',
};

export function ideProviderLabel(provider?: string | null): string {
  const normalized = provider?.trim().toLowerCase().replace(/\s+/g, '_');
  if (!normalized) return 'your IDE';
  return IDE_PROVIDER_LABELS[normalized] ?? 'your IDE';
}
