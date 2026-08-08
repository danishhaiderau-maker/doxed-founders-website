import { notFound } from 'next/navigation';

/**
 * Demo Control Panel removed from production admin (2026-08-09).
 *
 * Seed / reset / smoke remain on the API when `DEMO_MODE_ENABLED=true`
 * (see `apps/api/src/demo/` and `scripts/demo-harness.mjs` / `npm run demo:full`).
 * Prior UI recovered from git history before this commit.
 */
export default function AdminDemoPageRemoved() {
  notFound();
}
