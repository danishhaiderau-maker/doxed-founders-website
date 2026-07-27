import {
  generateNonce,
  type IpcMessage,
} from 'founder-ide-extension/ipc';

export type FounderIdeMessageClient = {
  send(message: IpcMessage): boolean;
  on(event: 'message', listener: (message: IpcMessage) => void): unknown;
  off(event: 'message', listener: (message: IpcMessage) => void): unknown;
};

function ipcEnvelope() {
  return { nonce: generateNonce(), ts: new Date().toISOString() };
}

export function sendFounderIdeRequestAndWait(
  client: FounderIdeMessageClient,
  request: IpcMessage,
): Promise<string> {
  if (
    request.type !== 'chatPrompt' &&
    request.type !== 'workspaceReadRequest' &&
    request.type !== 'proposedEdit' &&
    request.type !== 'commandRequest'
  ) {
    return Promise.reject(new Error(`Unsupported Founder IDE action: ${request.type}`));
  }
  const requestId = request.requestId;

  return new Promise((resolve, reject) => {
    const stdout: string[] = [];
    const stderr: string[] = [];
    const timeoutMs =
      request.type === 'commandRequest'
        ? Math.max(30_000, Math.min((request.timeoutMs ?? 30_000) + 30_000, 330_000))
        : request.type === 'workspaceReadRequest'
          ? 30_000
          : request.type === 'chatPrompt'
            ? 30_000
            : 10 * 60_000;
    let timer: NodeJS.Timeout | undefined;
    const cleanup = () => {
      if (timer) clearTimeout(timer);
      client.off('message', onMessage);
    };
    const finish = (result: string) => {
      cleanup();
      resolve(result);
    };
    const onMessage = (message: IpcMessage) => {
      if (!('requestId' in message) || message.requestId !== requestId) return;
      if (request.type === 'chatPrompt' && message.type === 'chatPromptResult') {
        finish(
          JSON.stringify({
            kind: 'chat',
            delivered: message.delivered,
            ...(message.error ? { error: message.error } : {}),
          }),
        );
        return;
      }
      if (
        request.type === 'workspaceReadRequest' &&
        message.type === 'workspaceReadResult'
      ) {
        finish(
          JSON.stringify({
            kind: 'workspace',
            nodes: message.nodes,
            ...(message.error ? { error: message.error } : {}),
          }),
        );
        return;
      }
      if (request.type === 'proposedEdit' && message.type === 'editReviewResult') {
        finish(
          JSON.stringify({
            kind: 'edit',
            approved: message.approved,
            ...(message.reason ? { reason: message.reason } : {}),
          }),
        );
        return;
      }
      if (request.type !== 'commandRequest') return;
      if (message.type === 'commandReviewResult' && !message.approved) {
        finish(
          JSON.stringify({
            kind: 'command',
            approved: false,
            exitCode: 126,
            stdout: '',
            stderr: message.reason ?? 'user_denied',
          }),
        );
        return;
      }
      if (message.type === 'commandOutput') {
        (message.stream === 'stderr' ? stderr : stdout).push(message.chunk);
        if (message.exitCode !== undefined && message.exitCode !== null) {
          finish(
            JSON.stringify({
              kind: 'command',
              approved: true,
              exitCode: message.exitCode,
              stdout: stdout.join('').slice(-12_000),
              stderr: stderr.join('').slice(-8_000),
            }),
          );
        }
      }
    };

    client.on('message', onMessage);
    timer = setTimeout(() => {
      try {
        client.send({
          type: 'cancel',
          requestId,
          reason: 'remote_dispatch_timeout',
          ...ipcEnvelope(),
        });
      } finally {
        cleanup();
        reject(new Error('Founder IDE action timed out waiting for local review'));
      }
    }, timeoutMs);

    let sent = false;
    try {
      sent = client.send(request);
    } catch (error) {
      cleanup();
      reject(error);
      return;
    }
    if (!sent) {
      cleanup();
      reject(new Error('Founder IDE authenticated pipe is unavailable'));
    }
  });
}
