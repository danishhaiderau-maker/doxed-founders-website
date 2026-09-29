const FOUNDER_IDE_REMOTE_PROVIDER = 'founder-ide-next';
const FOUNDER_IDE_REMOTE_CAPABILITY_VERSION = 1;
const FOUNDER_IDE_REMOTE_CAPABILITY = 'remote-build-v1';
const PRISMA_INT_MAX = 2_147_483_647;

export type FounderNodeCapabilityWrite = {
  ideProvider: typeof FOUNDER_IDE_REMOTE_PROVIDER | null;
  ideCapabilityVersion: typeof FOUNDER_IDE_REMOTE_CAPABILITY_VERSION | null;
  ideCapabilities: string[];
  ideCapabilitiesAt: Date | null;
};

/** Accept only the versioned protocol implemented by the frozen Founder IDE client. */
export function founderRemoteCapability(
  input: unknown,
  observedAt: Date,
): FounderNodeCapabilityWrite {
  const ide = input && typeof input === 'object' && !Array.isArray(input)
    ? input as Record<string, unknown>
    : null;
  const validObservedAt = observedAt instanceof Date && Number.isFinite(observedAt.getTime());
  const supported =
    validObservedAt &&
    ide?.provider === FOUNDER_IDE_REMOTE_PROVIDER &&
    ide.capabilityVersion === FOUNDER_IDE_REMOTE_CAPABILITY_VERSION &&
    Array.isArray(ide.capabilities) &&
    ide.capabilities.includes(FOUNDER_IDE_REMOTE_CAPABILITY);

  return supported
    ? {
        ideProvider: FOUNDER_IDE_REMOTE_PROVIDER,
        ideCapabilityVersion: FOUNDER_IDE_REMOTE_CAPABILITY_VERSION,
        ideCapabilities: [FOUNDER_IDE_REMOTE_CAPABILITY],
        ideCapabilitiesAt: new Date(observedAt.getTime()),
      }
    : founderRemoteCapabilityReset();
}

export function founderRemoteCapabilityReset(): FounderNodeCapabilityWrite {
  return {
    ideProvider: null,
    ideCapabilityVersion: null,
    ideCapabilities: [],
    ideCapabilitiesAt: null,
  };
}

/** Prisma stores heartbeat capacity metrics as Int columns. */
export function founderNodeIntMetric(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  return Math.min(PRISMA_INT_MAX, Math.round(value));
}
