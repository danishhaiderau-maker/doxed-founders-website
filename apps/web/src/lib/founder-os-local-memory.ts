import type { DeviceMemoryPayload } from '@dcf/utils';

const STORAGE_KEY = 'dcf-founder-os-local-memory';
const DEVICE_ID_KEY = 'dcf-founder-os-device-id';

function deviceId(): string {
  if (typeof window === 'undefined') return 'server';
  let id = localStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    id = `dev-${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

export function deviceLabel(): string {
  if (typeof navigator === 'undefined') return 'Unknown device';
  const ua = navigator.userAgent;
  if (/iPhone|iPad/i.test(ua)) return 'iPhone / iPad';
  if (/Android/i.test(ua)) return 'Android';
  if (/Windows/i.test(ua)) return 'Windows';
  if (/Mac/i.test(ua)) return 'Mac';
  return 'Browser';
}

export function loadLocalMemory(): DeviceMemoryPayload | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DeviceMemoryPayload;
    if (parsed?.version !== 1) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveLocalMemory(payload: DeviceMemoryPayload) {
  if (typeof window === 'undefined') return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

export function clearLocalMemory() {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(STORAGE_KEY);
}

export function memoryFromProject(input: {
  projectName?: string;
  currentGoal: string;
  projectContext?: string;
  roadmap?: string;
  tasksFile?: DeviceMemoryPayload['tasksFile'];
}): DeviceMemoryPayload {
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    projectName: input.projectName,
    currentGoal: input.currentGoal,
    projectContext: input.projectContext,
    roadmap: input.roadmap,
    tasksFile: input.tasksFile,
    deviceLabel: deviceLabel(),
  };
}

export function mergeMemory(
  local: DeviceMemoryPayload | null,
  remote: DeviceMemoryPayload | null,
): DeviceMemoryPayload | null {
  if (!local && !remote) return null;
  if (!local) return remote;
  if (!remote) return local;
  return new Date(local.updatedAt) >= new Date(remote.updatedAt) ? local : remote;
}

export function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true;
}

export { deviceId };
