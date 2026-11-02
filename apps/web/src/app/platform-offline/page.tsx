import { PlatformOfflineNotice, type OfflineFeature } from '@/components/platform-offline-notice';

const FEATURES = new Set([
  'login',
  'register',
  'x-sync',
  'x402',
  'trading',
  'account',
  'generic',
]);

export default async function PlatformOfflinePage({
  searchParams,
}: {
  searchParams: Promise<{ feature?: string }>;
}) {
  const params = await searchParams;
  const raw = params.feature ?? 'generic';
  const feature = (FEATURES.has(raw) ? raw : 'generic') as OfflineFeature;
  return <PlatformOfflineNotice feature={feature} />;
}
