import {
  generateNonce,
  type IpcMessage,
} from 'founder-ide-extension/ipc';

export type PendingDispatch = {
  id: string;
  sessionId: string;
  prompt: string;
  ideProvider: string;
};

export function isFounderIdeProvider(provider: string): boolean {
  return ['founder-ide', 'founder_ide', 'void', 'vscode'].includes(provider.toLowerCase());
}

function ipcEnvelope() {
  return { nonce: generateNonce(), ts: new Date().toISOString() };
}

export function buildFounderIdeMessage(dispatch: PendingDispatch): IpcMessage {
  let action: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(dispatch.prompt) as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const root = parsed as Record<string, unknown>;
      action =
        root.founderIdeAction &&
        typeof root.founderIdeAction === 'object' &&
        !Array.isArray(root.founderIdeAction)
          ? (root.founderIdeAction as Record<string, unknown>)
          : root;
    }
  } catch {
    // Plain user prompts are the normal path.
  }

  if (action?.type === 'workspaceReadRequest') {
    return {
      type: 'workspaceReadRequest',
      requestId: dispatch.id,
      ...(typeof action.path === 'string' ? { path: action.path } : {}),
      ...(typeof action.maxEntries === 'number' ? { maxEntries: action.maxEntries } : {}),
      ...ipcEnvelope(),
    };
  }

  if (
    action?.type === 'proposedEdit' &&
    typeof action.path === 'string' &&
    action.edit &&
    typeof action.edit === 'object' &&
    !Array.isArray(action.edit)
  ) {
    const edit = action.edit as Record<string, unknown>;
    const kind = edit.kind;
    if (
      (kind === 'create' || kind === 'overwrite' || kind === 'append' || kind === 'patch') &&
      typeof edit.content === 'string'
    ) {
      return {
        type: 'proposedEdit',
        requestId: dispatch.id,
        path: action.path,
        diff: typeof action.diff === 'string' ? action.diff : '',
        creates: action.creates === true || kind === 'create',
        edit: {
          kind,
          content: edit.content,
          ...(typeof edit.anchor === 'string' ? { anchor: edit.anchor } : {}),
        },
        ...ipcEnvelope(),
      };
    }
  }

  if (action?.type === 'commandRequest' && typeof action.command === 'string') {
    const risk =
      action.risk === 'readonly' || action.risk === 'destructive'
        ? action.risk
        : 'mutation';
    return {
      type: 'commandRequest',
      requestId: dispatch.id,
      command: action.command,
      ...(typeof action.cwd === 'string' ? { cwd: action.cwd } : {}),
      risk,
      ...(typeof action.timeoutMs === 'number' ? { timeoutMs: action.timeoutMs } : {}),
      ...ipcEnvelope(),
    };
  }

  return {
    type: 'chatPrompt',
    requestId: dispatch.id,
    sessionId: dispatch.sessionId,
    prompt: dispatch.prompt,
    ...ipcEnvelope(),
  };
}
