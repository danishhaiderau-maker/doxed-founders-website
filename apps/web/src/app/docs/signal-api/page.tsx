import Link from 'next/link';
import { SIGNAL_LEGAL_DISCLAIMER } from '@dcf/utils';

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

      <section className="mt-8 space-y-4 rounded-lg border border-amber-700/40 bg-amber-950/20 p-6">
        <h2 className="text-xl font-semibold text-amber-100">Legal disclaimer</h2>
        <p className="text-sm text-amber-100/90">{SIGNAL_LEGAL_DISCLAIMER}</p>
      </section>

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
        <h2 className="text-xl font-semibold text-white">Agentic access (x402)</h2>
        <p className="text-sm text-gray-300">
          Autonomous agents can poll the full ENSE intent without an account:
        </p>
        <ul className="list-disc space-y-2 pl-5 text-sm text-gray-300">
          <li>
            Free preview:{' '}
            <code className="text-green-400">GET .../signals/latest</code> (direction + status only)
          </li>
          <li>
            Full intent:{' '}
            <code className="text-green-400">GET .../signals/intent</code> —{' '}
            <strong>$0.10 USDC</strong> per poll via x402 on Base (<code>eip155:8453</code>)
          </li>
          <li>Or use <code>X-Signal-Api-Key</code> on either endpoint (human / legacy integrators)</li>
          <li>Success fee unchanged: 10% of profit on profitable <code>EXIT</code> only</li>
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
