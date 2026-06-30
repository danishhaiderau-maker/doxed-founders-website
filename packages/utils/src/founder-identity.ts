/**
 * Shared Founder OS identity constants — used across landing, onboarding, and
 * workspace surfaces so the messaging stays consistent.
 */

export const FOUNDER_OS_IDENTITY = {
  tagline: 'Your laptop is the compute.',
  promise:
    'Stop paying for cloud AI before you know your idea is worth building.',
  steps: [
    'Brainstorm',
    'Research',
    'Prototype',
    'Test',
    'Validate demand',
    'Build your community',
    'Ship in public',
    'Scale only when your idea proves itself',
  ],
  mission: 'Founder OS is the operating layer around a software project.',
  positioning: [
    'Build in your preferred IDE',
    'Think with your preferred AI',
    'Run local models when possible',
    'Manage infrastructure',
    'Coordinate work',
    'Build your community',
    'Publish progress',
    'Validate ideas cheaply',
    'Scale only after you have evidence',
  ],
} as const;

export type FounderOsIdentity = typeof FOUNDER_OS_IDENTITY;
