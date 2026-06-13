import Link from 'next/link';

export const metadata = {
  title: 'Signal Cycle API | Doxxed Crypto',
  description: 'Exchange-neutral BTC signal cycles with success-fee settlement.',
};

export default function SignalApiDocsPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10 text-gray-200">
      <Link href="/agent-hub/conservative-btc" className="text-sm text-green-400 hover:underline">
        ← Conservative BTC Agent
      </Link>
      <h1 className="mt-4 text-3xl font-bold text-white">Signal Cycle API</h1>
      <p className="mt-2 text-gray-400">
        Exchange-neutral intents. Mandatory exchange stop at fill. Success fee (10% of profit) only after
        profitable close.
      </p>

      <section className="mt-8 space-y-4 rounded-lg border border-gray-700 bg-gray-900/60 p-6">
        <h2 className="text-xl font-semibold text-white">Subscriber mandate</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm">
          <li>Use <strong>your venue mark at receipt</strong> — never Bitfinex absolute prices.</li>
          <li>
            Place <strong>exchange-native stop-loss at fill</strong> at{' '}
            <code className="text-green-400">risk.stop_loss_margin_pct</code> (default −18% margin).
          </li>
          <li>Post lifecycle events: <code>ORDER_PLACED</code> → <code>FILLED</code> → <code>EXIT</code>.</li>
          <li>
            Billing: <strong>10% of profit</strong> on close; $0 on loss; waived if 10% &lt; $0.20.
          </li>
        </ul>
      </section>

      <section className="mt-8 space-y-3">
        <h2 className="text-xl font-semibold text-white">Quick start</h2>
        <ol className="list-decimal space-y-2 pl-5 text-sm text-gray-300">
          <li>Sign in → open Agent Hub → create a Signal API key.</li>
          <li>
            Poll{' '}
            <code className="text-green-400">GET /api/trading-agents/conservative-btc/signals/latest</code>{' '}
            with <code>X-Signal-Api-Key</code>.
          </li>
          <li>Compute limit from <code>entry.offset_pct</code> on your mark.</li>
          <li>
            POST{' '}
            <code className="text-green-400">
              /api/trading-agents/conservative-btc/signals/cycles/:cycleId/events
            </code>{' '}
            with <code>stop_loss_placed: true</code> on fill.
          </li>
          <li>POST <code>EXIT</code> with <code>pnl_usd</code> for settlement.</li>
        </ol>
      </section>

      <section className="mt-8">
        <h2 className="text-xl font-semibold text-white">Full reference</h2>
        <p className="mt-2 text-sm text-gray-400">
          See repository doc{' '}
          <code className="text-green-400">docs/SIGNAL_CYCLE_SUBSCRIBER.md</code> for JSON examples and
          Hyperliquid pseudocode.
        </p>
        <p className="mt-4 text-sm">
          Agent card:{' '}
          <a href="/.well-known/agent-card.json" className="text-green-400 hover:underline">
            /.well-known/agent-card.json
          </a>
        </p>
      </section>
    </main>
  );
}
