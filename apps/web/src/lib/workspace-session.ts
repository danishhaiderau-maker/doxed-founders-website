'use client';

import { useCallback, useEffect, useRef } from 'react';
import {
  saveWorkspaceSessionPatch,
  type WorkspaceSessionData,
} from './api';

/**
 * Debounced workspace session persistence. Calls PUT /workspace-session at most
 * once per 500ms with the latest patch — never hammers Neon on every keystroke.
 *
 * Returns a `savePatch` function that can be called from effects on state change.
 */
export function useDebouncedWorkspaceSessionSave(token: string | null | undefined) {
  const pendingRef = useRef<Partial<WorkspaceSessionData>>({});
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef<Promise<unknown> | null>(null);
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const flush = useCallback(async () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const patch = pendingRef.current;
    pendingRef.current = {};
    if (!patch || Object.keys(patch).length === 0) return;
    if (!tokenRef.current) return;
    try {
      inFlightRef.current = saveWorkspaceSessionPatch(tokenRef.current, patch);
      await inFlightRef.current;
    } catch {
      // Swallow — session save is best-effort; UI still works offline.
    } finally {
      inFlightRef.current = null;
    }
  }, []);

  const savePatch = useCallback(
    (patch: Partial<WorkspaceSessionData>) => {
      pendingRef.current = { ...pendingRef.current, ...patch };
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        void flush();
      }, 500);
    },
    [flush],
  );

  // Flush on unmount / page hide so the last patch isn't lost.
  useEffect(() => {
    const onHide = () => {
      void flush();
    };
    window.addEventListener('beforeunload', onHide);
    window.addEventListener('pagehide', onHide);
    return () => {
      window.removeEventListener('beforeunload', onHide);
      window.removeEventListener('pagehide', onHide);
      void flush();
    };
  }, [flush]);

  return { savePatch, flush };
}

/** Cap terminal scrollback to the last N lines before persisting. */
export function trimTerminalScrollback(
  lines: { ts: string; line: string; stream?: string }[],
  max = 200,
) {
  if (lines.length <= max) return lines;
  return lines.slice(lines.length - max);
}
