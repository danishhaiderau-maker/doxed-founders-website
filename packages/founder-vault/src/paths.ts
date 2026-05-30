/** Default vault folder name on disk (home dir or user-chosen path). */
export const FOUNDER_VAULT_DIR_NAME = 'FounderVault';

export const FOUNDER_VAULT_FILES = {
  meta: 'meta.json',
  nodeConfig: 'node-config.json',
  projectContext: 'project-context.md',
  roadmap: 'roadmap.md',
  tasks: 'tasks.json',
  decisions: 'decisions.md',
  buildHistory: 'build-history.jsonl',
} as const;

export type FounderVaultFileKey = keyof typeof FOUNDER_VAULT_FILES;

export function vaultFilePath(vaultRoot: string, key: FounderVaultFileKey): string {
  const sep = vaultRoot.includes('\\') ? '\\' : '/';
  const normalized = vaultRoot.replace(/[/\\]+$/, '');
  return `${normalized}${sep}${FOUNDER_VAULT_FILES[key]}`;
}
