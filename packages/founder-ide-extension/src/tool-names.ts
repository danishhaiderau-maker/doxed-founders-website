export const FOUNDER_TOOL_IDS = {
  editFile: 'founder-edit-file',
  runCommand: 'founder-run-command',
  readWorkspace: 'founder-read-workspace',
} as const;

export const FOUNDER_TOOL_NAMES = new Set<string>(Object.values(FOUNDER_TOOL_IDS));
