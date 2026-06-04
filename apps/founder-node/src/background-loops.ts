import type { FounderNodeConfig } from '@dcf/founder-vault';

export type BackgroundLoopHandles = {
  syncTimer: ReturnType<typeof setInterval> | null;
  inferenceTimer: ReturnType<typeof setInterval> | null;
  syncJobTimer: ReturnType<typeof setInterval> | null;
  startupTimers: ReturnType<typeof setTimeout>[];
};

export function createLoopHandles(): BackgroundLoopHandles {
  return {
    syncTimer: null,
    inferenceTimer: null,
    syncJobTimer: null,
    startupTimers: [],
  };
}

export function stopBackgroundLoops(handles: BackgroundLoopHandles): void {
  if (handles.syncTimer) {
    clearInterval(handles.syncTimer);
    handles.syncTimer = null;
  }
  if (handles.inferenceTimer) {
    clearInterval(handles.inferenceTimer);
    handles.inferenceTimer = null;
  }
  if (handles.syncJobTimer) {
    clearInterval(handles.syncJobTimer);
    handles.syncJobTimer = null;
  }
  for (const t of handles.startupTimers) {
    clearTimeout(t);
  }
  handles.startupTimers = [];
}

export function readConfigOrNull(
  readConfig: (vaultRoot: string) => FounderNodeConfig | null,
  vaultRoot: string,
): FounderNodeConfig | null {
  return readConfig(vaultRoot);
}
