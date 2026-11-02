import { PlatformOfflineNotice } from '@/components/platform-offline-notice';
import { isBackendApiRetired } from '@/lib/backend-api-retired';

export const metadata = {
  title: 'Signal Cycle API | Doxxed Crypto',
  description: 'Exchange-neutral BTC signal cycles — offline while website API is retired.',
};

export default function SignalApiDocsPage() {
  if (isBackendApiRetired()) {
    return <PlatformOfflineNotice feature="x402" heading="x402 / Signal Cycle API offline" />;
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 text-gray-200">
      <h1 className="text-3xl font-bold text-white">Signal Cycle API</h1>
      <p className="mt-2 text-gray-400">
        Backend available only when NEXT_PUBLIC_BACKEND_API_RETIRED=0 (lab). Production is static.
      </p>
    </main>
  );
}
